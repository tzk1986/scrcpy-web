#!/usr/bin/env python3
"""帧签名 spike：静止动画 vs 动态内容的帧大小/间隔可分性（P2-1 候选 b 调研用）。

背景：方案 21 残留——静止设备每 ~10 分钟经历「降档→复核→回弹」循环。
候选 b 拟用前端可观测信号从源头区分「内容静止」与「链路拥塞」。
调研发现 droppedFrames（解码积压主动丢帧）在两类场景下都低，信号无效；
改用「帧大小」内容信号：静止动画（时钟等小区域变化）delta 帧极小，
动态内容帧大。本脚本真机采集帧序列，输出大小/间隔分位数验证可分性。

用法：
    python tests/manual/spike_still_frame_signature.py 192.168.8.25:5555 60
    python tests/manual/spike_still_frame_signature.py 192.168.8.18:5555 30
"""

import argparse
import asyncio
import statistics
import sys
import time

from websockets.asyncio.client import connect

START_CODE_4 = b"\x00\x00\x00\x01"
START_CODE_3 = b"\x00\x00\x01"


def first_nalu_type(data: bytes) -> int | None:
    if data[:4] == START_CODE_4:
        return data[4] & 0x1F if len(data) > 4 else None
    if data[:3] == START_CODE_3:
        return data[3] & 0x1F if len(data) > 3 else None
    return None


def pct(xs: list[float], q: float) -> float:
    if not xs:
        return float("nan")
    return statistics.quantiles(xs, n=100, method="inclusive")[int(q) - 1]


def main_summary(name: str, sizes: list[int], gaps: list[float]) -> None:
    print(f"\n===== {name} =====")
    n = len(sizes)
    print(f"帧数={n}")
    print(f"大小B: min={min(sizes)} p25={pct(sizes,25):.0f} p50={pct(sizes,50):.0f} "
          f"p75={pct(sizes,75):.0f} p90={pct(sizes,90):.0f} max={max(sizes)}")
    small = sum(1 for s in sizes if s <= 100)
    tiny = sum(1 for s in sizes if s <= 500)
    print(f"≤100B 占比={small}/{n}={small/n:.1%}  ≤500B 占比={tiny/n}={tiny/n:.1%}")
    if gaps:
        print(f"帧间隔ms: min={min(gaps):.0f} p50={pct(gaps,50):.0f} "
              f"p90={pct(gaps,90):.0f} max={max(gaps):.0f} 均值={statistics.mean(gaps):.0f}")
        cv = statistics.pstdev(gaps) / statistics.mean(gaps) if statistics.mean(gaps) else float("nan")
        print(f"间隔变异系数CV={cv:.2f} （静止规律→低CV；拥塞burst-gap→高CV）")


async def capture(device_id: str, duration: float) -> int:
    url = f"ws://127.0.0.1:8765/ws/video/{device_id}"
    print(f"[spike] connecting {url} for {duration:.0f}s")
    sizes: list[tuple[float, int, bool]] = []  # (t, size, is_key)

    async with connect(url, max_size=None) as ws:
        started = time.monotonic()
        while True:
            remaining = duration - (time.monotonic() - started)
            if remaining <= 0:
                break
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=remaining)
            except asyncio.TimeoutError:
                break
            if isinstance(msg, str):
                continue
            t = time.monotonic() - started
            sizes.append((t, len(msg), first_nalu_type(msg) == 5))

    print(f"\n===== {device_id} 原始序列（前 12 帧） =====")
    for t, s, k in sizes[:12]:
        print(f"  t={t:7.2f}s size={s:>7}B {'[IDR]' if k else ''}")

    all_sizes = [s for _, s, _ in sizes]
    gaps = [(sizes[i][0] - sizes[i - 1][0]) * 1000 for i in range(1, len(sizes))]
    main_summary("全量帧", all_sizes, gaps)

    # 只留 delta：IDR 是周期性大帧，会污染静止特征
    delta = [s for _, s, k in sizes if not k]
    dgaps = [
        (sizes[i][0] - sizes[i - 1][0]) * 1000
        for i in range(1, len(sizes))
        if not sizes[i][2]
    ]
    main_summary("仅 delta 帧", delta, dgaps)
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("device_id", help="ADB serial，如 192.168.8.25:5555")
    p.add_argument("duration", type=float, help="抓取秒数")
    args = p.parse_args()
    return asyncio.run(capture(args.device_id, args.duration))


if __name__ == "__main__":
    sys.exit(main())