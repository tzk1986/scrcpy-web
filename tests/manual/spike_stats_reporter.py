#!/usr/bin/env python3
"""stats 上报模拟器：复刻 VideoPlayer.vue 的 2s 帧率上报（方案 30 验收用）。

背景：后端自适应决策器的输入是前端上报的 {op:'stats', fps}（每 2s、fps≥1
才上报、码率重启宽限期内暂停）。纯接收脚本（capture_ws_frames）不上报，
决策器无样本不动作，无法观察降档/回弹行为。本脚本模拟真实前端上报，
供方案 30 冷静期递增序列的真机验收观察。

用法：
    python tests/manual/spike_stats_reporter.py 192.168.8.25:5555 2400
同时观察后端日志（stdout）的 adaptive_bitrate_switch_pending 事件序列。
"""

import argparse
import asyncio
import sys
import time

from websockets.asyncio.client import connect


async def run(device_id: str, duration: float) -> int:
    url = f"ws://127.0.0.1:8765/ws/video/{device_id}"
    print(f"[reporter] connecting {url} for {duration:.0f}s", flush=True)
    started = time.monotonic()
    window_start = started
    window_frames = 0
    last_fps = 0.0
    report_count = 0

    async with connect(url, max_size=None) as ws:
        async def report_loop() -> None:
            nonlocal window_start, window_frames, last_fps, report_count
            while True:
                await asyncio.sleep(2.0)
                now = time.monotonic()
                if now - started >= duration:
                    return
                # 复刻 VideoPlayer：每 2s 读一次「最近一秒窗口 fps」
                # （h264VideoStream 每秒刷新 _fps）——这里用 2s 窗均值近似
                elapsed = now - window_start
                fps = window_frames / elapsed if elapsed > 0 else 0.0
                window_start = now
                window_frames = 0
                last_fps = fps
                if fps < 1:
                    continue  # 静止到几乎无帧：与前端一致不上报
                report_count += 1
                await ws.send(f'{{"op": "stats", "fps": {fps:.2f}}}')
                if report_count % 15 == 0:
                    print(f"[reporter] t={now - started:.0f}s sent stats #{report_count} "
                          f"fps={fps:.2f}", flush=True)

        reporter = asyncio.create_task(report_loop())
        try:
            while True:
                remaining = duration - (time.monotonic() - started)
                if remaining <= 0:
                    break
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=remaining)
                except asyncio.TimeoutError:
                    break
                if not isinstance(msg, str):
                    window_frames += 1
        finally:
            reporter.cancel()

    print(f"[reporter] done: {report_count} stats sent, last_fps={last_fps:.2f}", flush=True)
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("device_id", help="ADB serial，如 192.168.8.25:5555")
    p.add_argument("duration", type=float, help="观察秒数")
    args = p.parse_args()
    return asyncio.run(run(args.device_id, args.duration))


if __name__ == "__main__":
    sys.exit(main())