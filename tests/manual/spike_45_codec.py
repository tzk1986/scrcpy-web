#!/usr/bin/env python3
"""方案 45 T3 spike：codec 后端放开真机验证（h265 出流与 codec id 判读，非 pytest）。

需真机、手动跑、不入 CI。

目的：
    1. 同进程直连 ScrcpyEncoder（不经后端/WS 层），以 EncoderOpts(codec=...)
       开一个**短**会话，验证设备能否按请求的 codec 正常开流；
    2. 握手 4B codec id（dummy 1B + 设备名 64B 之后，大端）由包装
       read_packets 截获原样打印：h265 应为 0x68323635；若请求 h265
       却见 0x68323634（"h264"）＝ scrcpy-server 静默回退（设备无 h265
       编码器）；0＝设备禁用视频流、1＝配置错误（read_packets 抛异常）；
    3. 出流后对前若干包按 scan_packet_nalus(hevc=...) 解析 NAL 类型，
       确认真为 HEVC 流（VPS/SPS/PPS=32/33/34，IRAP∈[16,21]）或回退后
       的 h264 流（SPS/PPS/IDR=7/8/5）；
    4. 判读即停（超时上限 --budget 秒），不抢 8765 生产服务的设备会话、
       不长占 scrcpy session。

用法（Git Bash；adb 子进程由 Python 直启，MSYS_NO_PATHCONV=1 为保险丝，
详见 docs/经验记录.md）：
    cd D:/tangzk/py/scrcpy-web
    PYTHONPATH=backend MSYS_NO_PATHCONV=1 python -u tests/manual/spike_45_codec.py \
        --device 192.168.8.18:5555 --codec h265

判定（写进末尾 verdict 行）：
    OK               请求 h265 且 codec id=0x68323635、≥1 个 HEVC 包出流
    FALLBACK_H264    请求 h265 但 codec id=0x68323634（设备不支持，server 回退）
    NO_STREAM        未出流（进程早退/握手异常/socket 断开），见上方错误输出
    OK_H264          请求 h264 且正常出流（对照）
"""

import argparse
import asyncio
import struct
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))  # config 包在项目根（config/settings.py）

from app.domain.ports import EncoderOpts  # noqa: E402
from app.infrastructure.stream import scrcpy as scrcpy_module  # noqa: E402
from app.infrastructure.stream.scrcpy import ScrcpyEncoder  # noqa: E402
from app.scrcpy.h264_parser import scan_packet_nalus  # noqa: E402
from app.scrcpy.stream_protocol import (  # noqa: E402
    SC_CODEC_ID_DISABLED,
    SC_CODEC_ID_ERROR,
    SC_CODEC_ID_H264,
    SC_CODEC_ID_H265,
    SessionEvent,
)

CODEC_NAMES = {
    SC_CODEC_ID_H264: "h264",
    SC_CODEC_ID_H265: "h265",
    SC_CODEC_ID_DISABLED: "disabled(0)",
    SC_CODEC_ID_ERROR: "config-error(1)",
}


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def install_codec_id_spy(state: dict) -> None:
    """包装 scrcpy 模块内 read_packets：先截获握手 4B codec id，再原样转发事件。

    仅在本 spike 进程内生效（模块属性替换），不改生产代码。
    """
    orig = scrcpy_module.read_packets

    async def spied(reader):  # type: ignore[no-untyped-def]
        # 注意：read_packets 内部会自行读握手（1+64+4B），spy 先读必须
        # feed_data 原样回吐，否则 orig 会把包数据当握手重复消费（spike 自身坑）
        dummy = await reader.readexactly(1)
        name = await reader.readexactly(64)
        codec_bytes = await reader.readexactly(4)
        codec_id = struct.unpack(">I", codec_bytes)[0]
        state["codec_id"] = codec_id
        state["device_name"] = name.rstrip(b"\x00").decode("utf-8", errors="replace")
        log(
            f"handshake: codec id = 0x{codec_id:08X}"
            f"（{CODEC_NAMES.get(codec_id, 'unknown')}），"
            f"device_name={state['device_name']!r}, dummy=0x{dummy[0]:02X}"
        )
        reader.feed_data(dummy + name + codec_bytes)
        async for event in orig(reader):
            if isinstance(event, SessionEvent) and "session" not in state:
                state["session"] = (event.width, event.height)
                log(f"session 包：分辨率 {event.width}x{event.height}")
            yield event

    scrcpy_module.read_packets = spied


def describe_first_packets(packets: list[bytes], *, hevc: bool) -> list[list[int]]:
    """对前若干包做 NAL 类型扫描（hevc 判定与 WS 层同款），返回逐包类型列表。"""
    out = []
    for payload in packets:
        out.append([ntype for _, _, ntype in scan_packet_nalus(payload, hevc=hevc)])
    return out


async def consume(encoder: ScrcpyEncoder, device: str, opts: EncoderOpts,
                  state: dict, max_packets: int) -> str:
    """消费 start() 生成器至 max_packets 包或流自然结束（超时由外层控制）。"""
    async for _pts, payload in encoder.start(device, opts):
        state["packets"].append(payload)
        n = len(state["packets"])
        if n <= 3:
            log(f"packet #{n}: {len(payload)}B pts={_pts}")
        if n >= max_packets:
            return "enough"
    return "stream ended (EOF)"


async def run(device: str, codec: str, budget: float, max_packets: int) -> int:
    state: dict = {"codec_id": None, "packets": [], "session": None}
    install_codec_id_spy(state)

    encoder = ScrcpyEncoder()
    log(f"=== spike_45 codec：device={device} requested={codec} budget={budget}s ===")
    outcome = "unknown"
    try:
        task = asyncio.create_task(consume(encoder, device, EncoderOpts(codec=codec),
                                           state, max_packets))
        done, _ = await asyncio.wait({task}, timeout=budget)
        if not done:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
            outcome = "timeout (budget exhausted)"
        else:
            exc = task.exception()
            if exc is not None:
                outcome = f"consume raised: {exc!r}"
                log(f"❌ {outcome}")
            else:
                outcome = task.result()
    finally:
        await encoder.stop()

    codec_id = state["codec_id"]
    packets = state["packets"]
    hevc_expected = codec == "h265"
    types_preview = describe_first_packets(packets[:3], hevc=hevc_expected)
    log(f"packets={len(packets)} outcome={outcome}")
    for i, t in enumerate(types_preview, 1):
        log(f"  packet #{i} NAL types (hevc={hevc_expected}) = {t}")

    verdict = "NO_STREAM"
    if codec_id is not None:
        name = CODEC_NAMES.get(codec_id, f"unknown(0x{codec_id:08X})")
        if packets:
            if hevc_expected:
                if codec_id == SC_CODEC_ID_H265:
                    verdict = "OK"
                elif codec_id == SC_CODEC_ID_H264:
                    verdict = "FALLBACK_H264"
                else:
                    verdict = f"WEIRD_CODEC_ID({name})"
            else:
                verdict = "OK_H264" if codec_id == SC_CODEC_ID_H264 else f"WEIRD_CODEC_ID({name})"
    log(f"verdict: {verdict} | requested={codec} codec_id="
        f"{'0x%08X' % codec_id if codec_id is not None else 'N/A'} packets={len(packets)}")
    ok = verdict in ("OK", "OK_H264", "FALLBACK_H264")
    log(f"总结：{'通过' if ok else '未通过'}（FALLBACK_H264 视为设备能力结论，同样出报告）")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="方案 45 T3 codec 真机 spike（手动，不入 CI）")
    ap.add_argument("--device", default="192.168.8.18:5555", help="ADB 序列号")
    ap.add_argument("--codec", choices=("h265", "h264"), default="h265",
                    help="请求的视频编码（默认 h265）")
    ap.add_argument("--budget", type=float, default=8.0, help="会话时间上限秒数（判读即停）")
    ap.add_argument("--max-packets", type=int, default=6, help="消费多少包后主动停止")
    args = ap.parse_args()

    try:
        return asyncio.run(run(args.device, args.codec, args.budget, args.max_packets))
    except KeyboardInterrupt:
        log("用户中断")
        return 130


if __name__ == "__main__":
    sys.exit(main())
