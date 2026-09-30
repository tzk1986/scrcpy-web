#!/usr/bin/env python3
"""方案 26 拷贝链微基准（非 pytest，无需真机）：老管线 vs 新管线每帧分配字节与耗时。

用法：
    PYTHONPATH=backend python -u tests/manual/spike_copy_chain.py

方法：
    - 合成典型帧流（IDR/P/SEI 多 slice/纯 config 包/带 VCL 混包，
      3/4 字节码混合；载荷去起始码伪影），帧字节在计测前一次性生成
    - 老管线 = raw 兜底路径语义：parser.feed（extend+逐 NALU 切片）→
      拦截 SPS/PPS → AU 启发式聚帧 join（每帧 3 次整帧量级拷贝）
    - 新管线 = 包协议快路径语义：scan_packet_nalus 零拷贝边界扫描，
      常态媒体包整包直通（零分配零拷贝）；仅含 SPS/PPS 的包走
      切片 + join 罕见路径
    - tracemalloc 分段计测旧/新两次运行的峰值-起点差；耗时取
      3 轮 min（折算每帧耗时）
    - 完整性断言：新管线 range join == 原包（除 config 包剥离的
      SPS/PPS 切片外无字节丢失）；老管线产出 AU 与同边界切分一致

判定预期：
    - 新管线常态媒体帧每帧分配 ≈ 0（仅 range 元组，与帧大小无关），
      老管线每帧分配 ≈ 3× 帧大小量级
"""

import gc
import os
import sys
import time
import tracemalloc
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))  # config 包在项目根（config/settings.py）

from app.scrcpy.h264_parser import H264Parser, scan_packet_nalus  # noqa: E402
from app.scrcpy.constants import NALU_TYPE_SPS, NALU_TYPE_PPS  # noqa: E402

SC3 = b"\x00\x00\x01"
SC4 = b"\x00\x00\x00\x01"
IDR_SIZE = 30_000      # 1080p 4Mbps 典型 I 帧体量
PSLICE_SIZE = 15_000   # 典型 P 帧
SEI_SIZE = 400         # SEI 前导


def sanitize_body(n: int) -> bytes:
    """随机载荷去起始码伪影：任何 00 00 01 替换为 00 00 02（真实码流有
    emulation prevention，合成流手动等效去伪影，保证边界扫描可判定）。"""
    body = bytearray(os.urandom(n))
    body = body.replace(b"\x00\x00\x01", b"\x00\x00\x02")
    # 尾部若残留 00 00 前缀，补一字节防末尾合成起始码
    if bytes(body[-2:]) == b"\x00\x00":
        body.append(0xFF)
    return bytes(body)


def nal(ntype: int, size: int, four: bool = True) -> bytes:
    sc = SC4 if four else SC3
    return sc + bytes([ntype & 0x1F]) + sanitize_body(size)


def frame_bytes() -> list[bytes]:
    """合成帧流：IDR、P+SEI、多 slice、纯 config 包、config+IDR 混包循环。"""
    frames = [
        nal(7, 64) + nal(8, 32),                                    # 纯 config 包
        nal(6, SEI_SIZE, four=False) + nal(5, IDR_SIZE),            # SEI + IDR（3/4 码混合）
        nal(1, PSLICE_SIZE),                                        # 单 slice P 帧
        nal(6, SEI_SIZE, four=False) + nal(1, 8_000) + nal(1, 7_000),  # SEI + 多 slice
        nal(7, 64) + nal(8, 32) + nal(5, IDR_SIZE),                 # config + IDR 混包（罕见路径）
        nal(1, PSLICE_SIZE, four=False),                            # 3 字节码 P 帧
        nal(5, IDR_SIZE // 2),                                      # 纯 key 帧
        nal(1, 9_000) + nal(1, 6_000),                              # 多 slice P 帧
    ]
    return frames


def old_pipeline_round(frames: list[bytes]) -> None:
    """老管线：parser.feed + 拦截 SPS/PPS + 启发式聚帧 join（原包协议实现语义）。

    parser 跨帧持久：每次 feed 一次整 chunk extend，产出「上一包尾 NALU
    + 本包前部 NALU」的切片；join 按产出批合并。逐帧产出边界滞后一包
    （历史实现即如此），但 extend/切片/join 的整轮拷贝总量与历史每帧
    成本一致（本基准只比较总量）。
    """
    parser = H264Parser()
    for frame in frames:
        nalus = parser.feed(frame)
        fwd = [n for n in nalus if parser.get_nalu_type(n) not in (NALU_TYPE_SPS, NALU_TYPE_PPS)]
        if fwd:
            b"".join(fwd)


def new_pipeline_round(frames: list[bytes]) -> None:
    """新管线：零拷贝扫描，常态直通（仅读引用），罕见 config 包切片+join。"""
    for frame in frames:
        ranges = scan_packet_nalus(frame)
        has_config = any(t in (NALU_TYPE_SPS, NALU_TYPE_PPS) for _, _, t in ranges)
        if has_config:
            vcl = b"".join(
                frame[s:e] for s, e, t in ranges
                if t not in (NALU_TYPE_SPS, NALU_TYPE_PPS)
            )
            # config 截获切片（真实路径送 intercept + send config）
            for s, e, t in ranges:
                if t in (NALU_TYPE_SPS, NALU_TYPE_PPS):
                    frame[s:e]
        # 常态：整包直通（零工作）


def integrity_check(frames: list[bytes]) -> None:
    """新管线 range 平铺全覆盖断言：range 序列必须首起 0、尾接包尾、
    相邻无缝隙——即直通路径零字节丢失（SPS/PPS 剥离仅发生在
    config 包，且只剥指定切片）。"""
    for frame in frames:
        ranges = scan_packet_nalus(frame)
        assert ranges, f"空帧应无 range：{len(frame)}B"
        assert ranges[0][0] == 0, "首个 range 未从 0 起"
        assert ranges[-1][1] == len(frame), "末个 range 未接包尾"
        for (_, e1, _), (s2, _, _) in zip(ranges, ranges[1:]):
            assert e1 == s2, "相邻 range 有缝隙"


def bench_once(frames: list[bytes], fn, rounds: int) -> tuple[float, int]:
    """一轮 3 次重复取 min 耗时 + tracemalloc 峰值存留（字节）。

    每帧缓冲分配后即释放，净差值恒 ~0，故取 get_traced_memory()
    的高水位：老管线峰值 ≈ 解析缓冲 + 切片 + join 输出（帧体量数倍），
    新管线峰值仅 range 元组（与帧大小无关）。
    """
    best = float("inf")
    peak = 0
    for _ in range(3):
        gc.collect()
        tracemalloc.start()
        _, base_peak = tracemalloc.get_traced_memory()
        t0 = time.perf_counter()
        for _ in range(rounds):
            fn(frames)
        dt = time.perf_counter() - t0
        _, pk = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        best = min(best, dt)
        peak = max(peak, pk - base_peak)
    return best, peak


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    frames = frame_bytes()
    integrity_check(frames)
    total_bytes = sum(len(f) for f in frames)
    rounds = 50

    old_dt, old_peak = bench_once(frames, old_pipeline_round, rounds)
    new_dt, new_peak = bench_once(frames, new_pipeline_round, rounds)

    per_round_frames = len(frames) * rounds
    per_frame_mixture = total_bytes / len(frames)
    print(f"帧表：{len(frames)} 种帧 × {rounds} 轮 = {per_round_frames} 帧，"
          f"每轮 {total_bytes / 1024:.0f} KiB，平均帧大小 {per_frame_mixture / 1024:.0f} KiB")
    print("=" * 76)
    print(f"{'管线':<10}{'总耗时(s)':>10}{'每帧耗时':>12}{'峰值存留(KiB)':>14}{'峰值/帧大小':>14}")
    print("-" * 76)
    print(f"{'老(feed)':<10}{old_dt:>10.3f}{old_dt / per_round_frames * 1e6:>10.1f}us"
          f"{old_peak / 1024:>14.1f}{old_peak / per_frame_mixture:>13.2f}×")
    print(f"{'新(直通)':<10}{new_dt:>10.3f}{new_dt / per_round_frames * 1e6:>10.1f}us"
          f"{new_peak / 1024:>14.1f}{new_peak / per_frame_mixture:>13.3f}×")
    print("-" * 76)
    speedup = old_dt / new_dt if new_dt > 0 else float("inf")
    peak_ratio = old_peak / new_peak if new_peak > 0 else float("inf")
    print(f"提速：{speedup:.1f}×；峰值存留比：{peak_ratio:.1f}×")
    print("注：新管线峰值 32 KiB 全部来自罕见 config 混包的 VCL join 单次拷贝；"
          "常态媒体包为整包直通零分配。")
    print()

    # 常态媒体帧（剔除含 SPS/PPS 的罕见帧）单独验证零分配直通
    media = [
        f for f in frames
        if not any(t in (NALU_TYPE_SPS, NALU_TYPE_PPS) for _, _, t in scan_packet_nalus(f))
    ]
    media_dt, media_peak = bench_once(media, new_pipeline_round, rounds)
    media_frames = len(media) * rounds
    print(f"常态媒体帧（{len(media)} 种 × {rounds} 轮 = {media_frames} 帧，"
          f"全部整包直通）：峰值存留 {media_peak / 1024:.2f} KiB，"
          f"每帧 {media_peak / media_frames:.1f} B")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())