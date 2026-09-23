#!/usr/bin/env python3
"""方案 19 真机验证：回切关键帧 / WS 瞬断恢复 / 卡死注入陪伴观察（非 pytest）。

用法：
    # 回切 request_keyframe + WS 瞬断重连序列（验证 2 与 6）
    python -u tests/manual/verify_recovery_19.py 192.168.8.25:5555 --mode recovery

    # 纯观察（卡死注入验证 3 陪伴：逐帧时间戳/大小/关键帧，汇总 max_gap）
    python -u tests/manual/verify_recovery_19.py 192.168.8.25:5555 \
        --mode observe --seconds 45
"""

import argparse
import asyncio
import json
import time

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

URL = "ws://127.0.0.1:8765/ws/video/{device}"


def first_nalu_type(data: bytes) -> int | None:
    if data[:4] == b"\x00\x00\x00\x01":
        return data[4] & 0x1F if len(data) > 4 else None
    if data[:3] == b"\x00\x00\x01":
        return data[3] & 0x1F if len(data) > 3 else None
    return None


async def recv_loop(ws, seconds: float, label: str, verbose: bool = True) -> dict:
    """接收 seconds 秒，返回 {frames, keys, first_frame_at, last_frame_at, max_gap}。"""
    t0 = time.monotonic()
    frames = 0
    keys = 0
    first_frame_at: float | None = None
    last_frame_at: float | None = None
    max_gap = 0.0
    msgs: list[str] = []
    closed_early = False
    while True:
        remaining = seconds - (time.monotonic() - t0)
        if remaining <= 0:
            break
        try:
            msg = await asyncio.wait_for(ws.recv(), timeout=remaining)
        except asyncio.TimeoutError:
            break
        except ConnectionClosed:
            closed_early = True
            if verbose:
                print(f"[{label}] 连接被服务端关闭（提前结束）", flush=True)
            break
        now = time.monotonic() - t0
        if isinstance(msg, str):
            try:
                j = json.loads(msg)
            except json.JSONDecodeError:
                continue
            msgs.append(f"{j.get('type')}: {j.get('message', '')}")
            if verbose:
                print(f"[{label}] t={now:6.2f}s MSG {j.get('type')} {j.get('message', '')}",
                      flush=True)
            continue
        frames += 1
        if first_frame_at is None:
            first_frame_at = now
        if last_frame_at is not None:
            max_gap = max(max_gap, now - last_frame_at)
        last_frame_at = now
        is_key = first_nalu_type(msg) == 5
        if is_key:
            keys += 1
        if verbose and (is_key or frames <= 3):
            print(f"[{label}] t={now:6.2f}s frame#{frames} size={len(msg)} "
                  f"key={is_key}", flush=True)
    return {"frames": frames, "keys": keys, "first_frame_at": first_frame_at,
            "last_frame_at": last_frame_at, "max_gap": max_gap, "msgs": msgs,
            "closed_early": closed_early}


async def mode_observe(device: str, seconds: float) -> int:
    url = URL.format(device=device)
    print(f"[observe] {url} for {seconds:.0f}s", flush=True)
    async with connect(url, max_size=None) as ws:
        r = await recv_loop(ws, seconds, "observe")
    print(f"\n[observe] 帧数={r['frames']} 关键帧={r['keys']} "
          f"max_gap={r['max_gap']:.2f}s 消息={r['msgs'][:5]}", flush=True)
    return 0


async def mode_recovery(device: str, gap: float) -> int:
    url = URL.format(device=device)
    print(f"[recovery] {url} gap={gap}s", flush=True)

    # 1. 基准静置
    async with connect(url, max_size=None) as ws:
        base = await recv_loop(ws, 8.0, "base", verbose=False)
        print(f"[recovery] 基准 8s: 帧数={base['frames']} keys={base['keys']} "
              f"max_gap={base['max_gap']:.2f}s", flush=True)

        # 2. 回切：发 request_keyframe，等下一个 IDR
        t_send = time.monotonic()
        await ws.send(json.dumps({"op": "request_keyframe"}))
        print(f"[recovery] >>> request_keyframe 已发送 t=0", flush=True)
        latency: float | None = None
        while time.monotonic() - t_send < 5.0:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=5.0)
            except asyncio.TimeoutError:
                break
            now = time.monotonic() - t_send
            if isinstance(msg, str):
                continue
            if first_nalu_type(msg) == 5:
                latency = now
                print(f"[recovery] <<< IDR 到达 t={now:.2f}s size={len(msg)}", flush=True)
                break
            print(f"[recovery] t={now:.2f}s 非关键帧先到 size={len(msg)} "
                  f"(不阻断，继续等 IDR)", flush=True)
        print(f"[recovery] 回切延迟={latency if latency is not None else '超时>5s'}", flush=True)

        # 3. 防抖：立即重复发送（后端 1s 窗口内应合并）
        await ws.send(json.dumps({"op": "request_keyframe"}))
        await ws.send(json.dumps({"op": "request_keyframe"}))
        print("[recovery] >>> 连发 2 次 request_keyframe（防抖对照，后端日志验证）",
              flush=True)
        await recv_loop(ws, 3.0, "debounce", verbose=False)

    # 4. 瞬断：关闭 WS 等 gap 秒
    print(f"\n[recovery] WS 关闭，静默 {gap:.0f}s 模拟瞬断...", flush=True)
    await asyncio.sleep(gap)

    # 5. 重连（模拟前端指数退避 1/2/4/8/8s）
    backoff = [1, 2, 4, 8, 8]
    for attempt, wait in enumerate([0, *backoff], start=1):
        if wait:
            await asyncio.sleep(wait)
        print(f"[recovery] 重连尝试 #{attempt} ...", flush=True)
        try:
            ws = await asyncio.wait_for(connect(url, max_size=None), timeout=6.0)
        except Exception as e:  # noqa: BLE001 - 现场诊断脚本，如实记录
            print(f"[recovery] 尝试 #{attempt} 失败: {e!r}", flush=True)
            continue
        if attempt == 1:
            print("[recovery] 首次重连即成功（无需退避）", flush=True)
        else:
            print(f"[recovery] 重连成功（第 #{attempt} 次）", flush=True)
        async with ws:
            r = await recv_loop(ws, 15.0, "after", verbose=True)
        print(f"\n[recovery] 尝试 #{attempt}: 帧数={r['frames']} keys={r['keys']} "
              f"max_gap={r['max_gap']:.2f}s closed_early={r['closed_early']} "
              f"消息={r['msgs'][:5]}", flush=True)
        if r["frames"] == 0 or r["closed_early"]:
            print(f"[recovery] 尝试 #{attempt} 流未恢复（服务端启动竞争），"
                  f"继续指数退避重试", flush=True)
            continue
        print("[recovery] [pass] 重连后恢复推流", flush=True)
        return 0

    print("[recovery] [fail] 5 次重连全部失败", flush=True)
    return 1


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("device")
    p.add_argument("--mode", choices=["recovery", "observe"], default="recovery")
    p.add_argument("--seconds", type=float, default=45.0, help="observe 模式时长")
    p.add_argument("--gap", type=float, default=3.0, help="recovery 模式瞬断静默秒数")
    args = p.parse_args()
    if args.mode == "observe":
        return asyncio.run(mode_observe(args.device, args.seconds))
    return asyncio.run(mode_recovery(args.device, args.gap))


if __name__ == "__main__":
    raise SystemExit(main())