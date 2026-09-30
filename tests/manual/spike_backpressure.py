#!/usr/bin/env python3
"""P0-2/P0-3 反压恢复 spike（非 pytest）：慢消费者注入队列满，观测恢复行为。

用法：
    PYTHONPATH=backend python -u tests/manual/spike_backpressure.py 192.168.8.18:5555 \
        --warmup 5 --stall 10 --resume 15

方法（同进程直连 ScrcpyEncoder，不经 WS 层）：
    - 消费循环体内 await asyncio.sleep(stall_s) = 慢消费者（不上拉队列），
      真实复现前端卡顿/WS 反压的队列满场景
    - 采样并发记录 _data_queue.qsize() 与 _awaiting_keyframe 状态迁移：
      False→True = 溢出清积压事件；True→False = RESET_VIDEO 响应（新 IDR）
      到达——每次 True 的持续时长即服务端重出 IDR 的实际延迟
    - 恢复阶段逐帧解析 NAL type 5 判定关键帧：首帧即 IDR 说明
      「丢到关键帧」成立且积压为新鲜画面（无旧帧回放）
    - 停滞期间出现多次 True→False→True 循环即证明读循环未被
      config 包 put 卡死（P0-3：每轮 RESET 响应都携带 SESSION/CONFIG）

判定：
    - 停滞期间积压被清空（qsize 从 100 掉到 0），修复前 qsize 恒 100
    - awaiting=True 的窗口均 < 1s（服务端 0.1-0.4s 出 IDR + 节流窗口）
    - 恢复后首帧（或前几帧内）即关键帧，花屏窗口为 0
"""

import argparse
import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))  # config 包在项目根（config/settings.py）

from app.domain.ports import EncoderOpts  # noqa: E402
from app.infrastructure.stream.scrcpy import ScrcpyEncoder  # noqa: E402


def is_keyframe(payload: bytes) -> bool:
    """Annex B NAL 扫描：任一 NALU 为 type 5（IDR）即真。"""
    n = len(payload)
    i = 0
    while i + 4 <= n:
        if payload[i] == 0 and payload[i + 1] == 0 and payload[i + 2] == 1:
            if (payload[i + 3] & 0x1F) == 5:
                return True
            i += 4
        elif payload[i] == 0 and payload[i + 1] == 0 and payload[i + 2] == 0 \
                and i + 5 <= n and payload[i + 3] == 1:
            if (payload[i + 4] & 0x1F) == 5:
                return True
            i += 5
        else:
            i += 1
    return False


async def run(device: str, warmup: float, stall: float, resume: float,
              interval: float) -> int:
    encoder = ScrcpyEncoder()
    t0 = time.monotonic()
    samples: list[tuple[float, int, bool]] = []
    state = {"prev_awaiting": False, "true_count": 0, "false_count": 0,
             "true_total": 0.0, "true_max": 0.0, "true_start": 0.0,
             "qsize_max": 0}

    async def sampler() -> None:
        while not sampler_stop.is_set():
            now = time.monotonic() - t0
            q = encoder._data_queue.qsize()
            awaiting = encoder._awaiting_keyframe
            samples.append((now, q, awaiting))
            state["qsize_max"] = max(state["qsize_max"], q)
            if awaiting and not state["prev_awaiting"]:
                state["true_count"] += 1
                state["true_start"] = now
            if not awaiting and state["prev_awaiting"]:
                state["false_count"] += 1
                dur = now - state["true_start"]
                state["true_total"] += dur
                state["true_max"] = max(state["true_max"], dur)
            state["prev_awaiting"] = awaiting
            print(f"[spike] t={now:6.1f}s qsize={q:3d} awaiting={int(awaiting)}",
                  flush=True)
            await asyncio.sleep(interval)

    sampler_stop = asyncio.Event()
    sampler_task = asyncio.create_task(sampler())

    print(f"[spike] device={device} warmup={warmup:.0f}s stall={stall:.0f}s "
          f"resume={resume:.0f}s", flush=True)
    print("[spike] 启动编码器（首次含 JAR 推送，可能 ~10s）…", flush=True)

    phase = "warmup"
    frames = 0
    keys = 0
    warmup_frames = 0
    t_resume = 0.0
    resume_frames: list[float] = []
    first_key_idx: int | None = None
    resume_frames_seen = 0

    try:
        agen = encoder.start(
            device, EncoderOpts(max_size=0, bit_rate="4M", codec="h264", fps=30))
        # 方案 32：encoder 产出元组 (pts, payload)
        async for _pts, payload in agen:
            now = time.monotonic()
            frames += 1
            if is_keyframe(payload):
                keys += 1
            if phase == "warmup":
                warmup_frames += 1
                if now - t0 >= warmup:
                    phase = "stall"
                    print(f"[spike] >>> 进入停滞（慢消费者）{stall:.0f}s，"
                          f"已收 {warmup_frames} 帧", flush=True)
                    await asyncio.sleep(stall)  # 不上拉队列 = 慢消费者
                    phase = "resume"
                    t_resume = time.monotonic()
                    print(f"[spike] >>> 恢复消费 t={t_resume - t0:.1f}s",
                          flush=True)
            elif phase == "resume":
                resume_frames_seen += 1
                dt = now - t_resume
                resume_frames.append(dt)
                if first_key_idx is None and is_keyframe(payload):
                    first_key_idx = resume_frames_seen
                    print(f"[spike] 恢复后第 {first_key_idx} 帧为关键帧 "
                          f"(+{dt * 1000:.0f}ms)", flush=True)
                if dt >= resume:
                    break
            if frames % 120 == 0 and phase == "warmup":
                print(f"[spike] warmup {frames} 帧 keys={keys}", flush=True)
    finally:
        sampler_stop.set()
        await agen.aclose()
        await sampler_task

    # 汇总
    stall_phase = [s for s in samples if s[0] >= warmup and s[0] < warmup + stall]
    cycles = state["true_count"]
    avg_true = state["true_total"] / state["false_count"] if state["false_count"] else 0.0
    warmup_fps = warmup_frames / warmup if warmup else 0.0
    print(f"\n[spike] ==== 汇总 ====", flush=True)
    print(f"[spike] 帧率（warmup）={warmup_fps:.1f} fps；总帧={frames} "
          f"关键帧={keys}", flush=True)
    print(f"[spike] 停滞期间溢出循环数（awaiting False→True）={cycles} "
          f"（≥2 即读循环存活，config 未卡死）", flush=True)
    print(f"[spike] awaiting=True 窗口：次数={state['false_count']} "
          f"平均={avg_true * 1000:.0f}ms 最大={state['true_max'] * 1000:.0f}ms "
          f"（服务端重出 IDR 实测延迟）", flush=True)
    print(f"[spike] 停滞期间 qsize：峰值={state['qsize_max']} "
          f"末值={stall_phase[-1][1] if stall_phase else 'N/A'} "
          f"（修复后清空→0，修复前恒 100）", flush=True)
    sec1 = sum(1 for dt in resume_frames if dt <= 1.0)
    print(f"[spike] 恢复后首秒收到 {sec1} 帧；首帧延时 "
          f"{resume_frames[0] * 1000:.0f}ms", flush=True)
    print(f"[spike] 恢复后首个关键帧：第 {first_key_idx} 帧"
          f"{'' if first_key_idx is None else ''}"
          f"（1-2 帧内 = 花屏窗口 ~0，修复前要等下个自然 IDR ~10s）", flush=True)
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("device")
    p.add_argument("--warmup", type=float, default=5.0)
    p.add_argument("--stall", type=float, default=10.0)
    p.add_argument("--resume", type=float, default=15.0)
    p.add_argument("--interval", type=float, default=0.2)
    args = p.parse_args()
    return asyncio.run(run(args.device, args.warmup, args.stall,
                           args.resume, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())