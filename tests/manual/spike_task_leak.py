#!/usr/bin/env python3
"""P0-1 Task 泄漏 spike（非 pytest）：量化 StreamService 帧循环的 Event.wait 泄漏。

用法：
    PYTHONPATH=backend python -u tests/manual/spike_task_leak.py 192.168.8.18:5555 --seconds 120

背景（性能调研 P0-1，方案 26 前置验证）：
    stream_service.py 帧循环每轮创建 frame_task 与 event_task
    （asyncio.ensure_future(restart_event.wait())）赛跑；
    frame 先到时 event_task 不被取消，下一轮循环引用被覆盖后永久
    失去取消句柄（finally 只取消最后一批）。健康稳态 restart_event
    永不 set（仅自适应降码档时 set）→ 每帧泄漏 1 个 Event.wait 协程。

    本脚本同进程直接驱动 start_stream（不经 WS 层 → 无人调用
    report_client_fps → 自适应决策器无样本 → 隔离降档唤醒路径，
    即泄漏最恶劣的健康稳态），按 --interval 采样 asyncio.all_tasks()
    中 qualname 含 "Event.wait" 的 pending 任务数。

判定：泄漏速率 ≈ 实测 fps（每帧 1 个）；停止后残留反映 finally
    无法回收历史泄漏（GC 时机以实测为准）。
"""

import argparse
import asyncio
import gc
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))  # config 包在项目根（config/settings.py）

from app.application.stream_service import StreamService  # noqa: E402


def count_event_wait() -> int:
    n = 0
    for t in asyncio.all_tasks():
        name = getattr(t.get_coro(), "__qualname__", "") or ""
        if "Event.wait" in name:
            n += 1
    return n


async def run(device: str, seconds: float, interval: float) -> int:
    svc = StreamService()
    print(f"[spike] device={device} seconds={seconds:.0f} interval={interval:.0f}",
          flush=True)
    print(f"[spike] baseline Event.wait={count_event_wait()} "
          f"total_tasks={len(asyncio.all_tasks())}", flush=True)

    frames = 0
    samples: list[tuple[float, int, int]] = []
    t0 = time.monotonic()
    next_at = t0 + interval

    stream = svc.start_stream(device)
    try:
        async for _frame in stream:
            frames += 1
            now = time.monotonic()
            if now >= next_at:
                next_at = now + interval
                n = count_event_wait()
                samples.append((now - t0, n, frames))
                print(f"[spike] t={now - t0:6.1f}s frames={frames} "
                      f"Event.wait={n} total={len(asyncio.all_tasks())}",
                      flush=True)
                if now - t0 >= seconds:
                    break
    finally:
        await svc.stop_stream(device)
        await stream.aclose()
        await asyncio.sleep(0.5)
        resid = count_event_wait()
        print(f"[spike] stopped: Event.wait={resid} "
              f"total={len(asyncio.all_tasks())} (未 GC)", flush=True)
        gc.collect()
        await asyncio.sleep(0.5)
        resid2 = count_event_wait()
        print(f"[spike] after gc.collect(): Event.wait={resid2} "
              f"total={len(asyncio.all_tasks())}", flush=True)

    if len(samples) >= 2:
        dt = samples[-1][0] - samples[0][0]
        df = samples[-1][2] - samples[0][2]
        dw = samples[-1][1] - samples[0][1]
        rate = dw / dt if dt else float("nan")
        fps = df / dt if dt else float("nan")
        print(f"\n[spike] 结论：泄漏速率={rate:.1f} 任务/s，实测帧率={fps:.1f} fps，"
              f"比值={rate / fps if fps else float('nan'):.2f} 任务/帧", flush=True)
        print(f"[spike] 帧数={frames} 末次采样 Event.wait={samples[-1][1]} "
              f"停止后残留={resid} gc 后={resid2}", flush=True)
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("device")
    p.add_argument("--seconds", type=float, default=120.0)
    p.add_argument("--interval", type=float, default=5.0)
    args = p.parse_args()
    return asyncio.run(run(args.device, args.seconds, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())