#!/usr/bin/env python3
"""方案 28 D4 真机回归：码率重启路径 bak 化后走查（不改生产代码）

诱导自适应降档 → 观察 restarting 预告 → 重启后新 config + 首帧：
    WS 连流 → 收 config → 每秒上报低 fps stats（窗口 8、坏样本 ≥6 降档）
    → 收 {"type":"restarting"} → 再收 config（重启后 SPS/PPS 重发）→ 再收媒体帧。

判据（全部满足 = D4 路径 bak 化后无回归）：
    config1 / restarting / config2 / frame2 四个时间点齐全，且 config2 > restarting。

用法：
    python -u tests/manual/spike_bitrate_restart.py 192.168.8.18:5555
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

try:
    import websockets
except ImportError:
    print("需要 websockets：pip install websockets", file=sys.stderr)
    sys.exit(2)


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("device", help="ADB 序列号，如 192.168.8.18:5555")
    ap.add_argument("--backend", default="http://127.0.0.1:8765")
    ap.add_argument("--low-fps", type=float, default=1.0, help="上报的诱导低 fps")
    ap.add_argument("--samples", type=int, default=10, help="低 fps 上报次数（≥ window+2）")
    args = ap.parse_args()

    url = f"{args.backend.replace('http', 'ws')}/ws/video/{args.device}"
    t_connect = time.monotonic()
    t_config1 = t_restarting = t_config2 = t_frame2 = None
    config_count = 0
    sent = 0
    last_send = 0.0

    async with websockets.connect(url, max_size=16 * 1024 * 1024) as ws:
        while t_frame2 is None:
            # 每秒一度补发低 fps stats，直至发满
            now = time.monotonic()
            if sent < args.samples and now - last_send >= 1.0:
                await ws.send(json.dumps({"op": "stats", "fps": args.low_fps}))
                sent += 1
                last_send = now
                if sent >= args.samples:
                    log(f"低 fps stats 已发满 {sent} 次，等待重启收敛…")
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=20)
            except asyncio.TimeoutError:
                log(f"超时：sent={sent} 后 20s 无消息（流可能已被重启打断），中止")
                break
            if isinstance(msg, bytes):
                payload = msg[8:]  # 方案 32：剥离 8B PTS 前缀
                if t_config2 is not None and t_frame2 is None and len(payload) > 100:
                    t_frame2 = time.monotonic()
            else:
                try:
                    d = json.loads(msg)
                except json.JSONDecodeError:
                    continue
                mtype = d.get("type")
                if mtype == "config":
                    config_count += 1
                    if config_count == 1:
                        t_config1 = time.monotonic()
                        log(f"首次 config @ {t_config1 - t_connect:.3f}s")
                    elif t_config2 is None:
                        t_config2 = time.monotonic()
                        log(f"重启后二次 config @ {t_config2 - t_connect:.3f}s")
                elif mtype == "restarting":
                    t_restarting = time.monotonic()
                    log(f"收到 restarting 预告 bit_rate={d.get('bit_rate')} "
                        f"@ {t_restarting - t_connect:.3f}s")

    def ms(t: float | None) -> int | None:
        return None if t is None else round((t - t_connect) * 1000)

    verdict = (
        t_config1 is not None and t_restarting is not None
        and t_config2 is not None and t_frame2 is not None
        and t_config2 > t_restarting
    )
    print(json.dumps({
        "verdict": "D4_OK" if verdict else "D4_INCOMPLETE",
        "config1_ms": ms(t_config1),
        "restarting_ms": ms(t_restarting),
        "config2_ms": ms(t_config2),
        "frame2_ms": ms(t_frame2),
        "low_fps_sent": sent,
    }, ensure_ascii=False))
    return 0 if verdict else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))