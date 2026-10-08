#!/usr/bin/env python3
"""切页输入→画面更新时延分层探针（.33 RK3566 画面滞后调研用，非 pytest）。

测量链路（浏览器点击路径全链）：
    WS 发 touch → 设备响应切页 → 编码出帧 → WS 帧到达

同时采集三条时间线：
    1. WS 帧线：每帧到达时刻（相对注入）、大小、首 NALU 类型、PTS
    2. 输入线：touch 经 WS 发出的时刻
    3. 设备屏幕线：adb screencap 轮询内容哈希变化时刻（设备侧切页可见证据）

用法：
    python tests/manual/probe_switch_latency.py 192.168.8.33:5555 216 396 15
    （device_id, tap_x, tap_y, post_seconds；前 --pre 秒为基线观测，默认 3s）
"""

import argparse
import asyncio
import hashlib
import json
import statistics
import sys
import time

from websockets.asyncio.client import connect

sys.stdout.reconfigure(encoding="utf-8")

START_CODE_4 = b"\x00\x00\x00\x01"
START_CODE_3 = b"\x00\x00\x01"


def first_nalu_type(payload: bytes) -> int | None:
    """取首个 NALU 类型（IDR=5 / SPS=7 / PPS=8 / 非 IDR=1）。"""
    if payload[:4] == START_CODE_4:
        return payload[4] & 0x1F if len(payload) > 4 else None
    if payload[:3] == START_CODE_3:
        return payload[3] & 0x1F if len(payload) > 3 else None
    return None


async def screencap_loop(dev: str, t0: float, screens: list, stop: asyncio.Event) -> None:
    """adb screencap 轮询：记录 (t_rel, 内容哈希前 8 位)。"""
    while not stop.is_set():
        try:
            proc = await asyncio.create_subprocess_exec(
                "adb", "-s", dev, "exec-out", "screencap", "-p",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            h = hashlib.md5(out).hexdigest()[:8]
            screens.append((time.monotonic() - t0, h, len(out)))
        except Exception:
            pass
        try:
            await asyncio.wait_for(stop.wait(), timeout=0.05)
        except asyncio.TimeoutError:
            pass


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("device_id", help="如 192.168.8.33:5555")
    ap.add_argument("x", type=int)
    ap.add_argument("y", type=int)
    ap.add_argument("post", type=float, nargs="?", default=15.0)
    ap.add_argument("--warm", type=float, default=6.0,
                    help="首帧到达后再等 N 秒（越过启动密集 IDR 期）注入 tap")
    args = ap.parse_args()

    url = f"ws://127.0.0.1:8765/ws/video/{args.device_id}"
    print(f"[probe] 连接 {url}，首帧+warm={args.warm}s -> tap({args.x},{args.y}) -> post={args.post}s")
    t0 = time.monotonic()
    frames: list[tuple[float, int, int | None, int]] = []
    screens: list[tuple[float, str, int]] = []
    stop = asyncio.Event()
    config_seen = False

    try:
        async with connect(url, max_size=None) as ws:

            async def reader() -> None:
                nonlocal config_seen
                while not stop.is_set():
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=1)
                    except asyncio.TimeoutError:
                        continue
                    except Exception:
                        break
                    t = time.monotonic() - t0
                    if isinstance(msg, str):
                        try:
                            j = json.loads(msg)
                        except json.JSONDecodeError:
                            continue
                        if j.get("type") == "config":
                            config_seen = True
                            print(f"[probe] config: {j.get('width')}x{j.get('height')} "
                                  f"codec={j.get('codec')} @t={t:.2f}s")
                        elif j.get("type") in ("restarting", "error", "stream_ended"):
                            print(f"[probe] {j.get('type')}: {j} @t={t:.2f}s")
                    elif isinstance(msg, bytes) and len(msg) > 8:
                        pts = int.from_bytes(msg[:8], "big")
                        payload = msg[8:]
                        frames.append((t, len(payload), first_nalu_type(payload), pts))

            reader_task = asyncio.create_task(reader())
            sc_task = asyncio.create_task(screencap_loop(args.device_id, t0, screens, stop))

            # 等首帧抵达（流启动完成后）再预热，保证 tap 打在稳态流上
            wait_deadline = time.monotonic() + 30
            while not frames and time.monotonic() < wait_deadline:
                await asyncio.sleep(0.2)
            if not frames:
                stop.set()
                print("[probe] 30s 内未收到任何帧，中止")
                return 1
            print(f"[probe] 首帧 @t={frames[0][0]:.2f}s，预热 {args.warm}s…")
            await asyncio.sleep(args.warm)

            t_tap = time.monotonic() - t0
            await ws.send(json.dumps({"action": "touch", "x": args.x, "y": args.y}))
            print(f"[probe] touch 已发出 @t={t_tap:.2f}s")

            await asyncio.sleep(args.post)
            stop.set()
            reader_task.cancel()
            sc_task.cancel()
            try:
                await reader_task
            except (asyncio.CancelledError, Exception):
                pass
            try:
                await sc_task
            except (asyncio.CancelledError, Exception):
                pass
    except Exception as e:
        stop.set()
        print(f"[probe] 连接异常: {e}")
        return 1

    # ===== 报告 =====
    # 基线取 tap 前 5s 稳态窗（不含流启动期），p50 用于识别切页大帧
    pre_frames = [f for f in frames if f[0] < t_tap]
    base_frames = [f for f in frames if t_tap - 5.0 <= f[0] < t_tap]
    pre_sizes = [f[1] for f in base_frames] or [f[1] for f in pre_frames]
    pre_p50 = statistics.median(pre_sizes) if pre_sizes else 0
    big_threshold = max(pre_p50 * 5, 3000)
    print("\n===== 基线（tap 前 5s 稳态窗）=====")
    print(f"帧数={len(base_frames)} 大小 p50={pre_p50:.0f}B max={max(pre_sizes, default=0)}B")
    if base_frames:
        span = base_frames[-1][0] - base_frames[0][0]
        print(f"时长={span:.1f}s 平均fps={len(base_frames) / span:.1f}" if span > 0 else "")

    print("\n===== tap 后帧时间线（dt=相对 tap 秒 / size / NALU 类型）=====")
    post_frames = [f for f in frames if f[0] >= t_tap]
    first_big = None
    first_idr = None
    for t, size, ntype, pts in post_frames[:80]:
        dt = t - t_tap
        mark = ""
        if size >= big_threshold and first_big is None:
            first_big = dt
            mark = "  <== 首个大帧"
        if ntype == 5 and first_idr is None:
            first_idr = dt
            mark += "  <== 首个IDR" if mark == "" else " +首个IDR"
        if dt < 3.0 or size >= big_threshold or ntype == 5:
            print(f"  dt={dt:6.2f}s size={size:>8}B type={ntype}\t{mark}")

    print("\n===== 设备屏幕线（screencap 哈希变化）=====")
    changes = []
    prev_h = None
    for t, h, nbytes in screens:
        if prev_h is not None and h != prev_h:
            changes.append((t, h, nbytes))
        prev_h = h
    for t, h, nbytes in changes:
        dt = t - t_tap
        prefix = "tap前" if dt < 0 else "tap后"
        print(f"  {prefix} dt={dt:6.2f}s hash={h} png={nbytes}B")

    print("\n===== 汇总指标 =====")
    tap_after_change = next((t for t, _, _ in changes if t >= t_tap), None)
    print(f"设备切页可见(tap→screencap 首变)   : "
          f"{tap_after_change - t_tap:.2f}s" if tap_after_change else "设备切页可见: 窗口内未检测到变化")
    print(f"首个大帧到达(tap→>={big_threshold:.0f}B)   : "
          f"{first_big:.2f}s" if first_big is not None else "首个大帧: 无")
    print(f"首个 IDR 到达(tap→IDR)             : "
          f"{first_idr:.2f}s" if first_idr is not None else "首个 IDR: 无")
    post_span = (post_frames[-1][0] - t_tap) if post_frames else 0
    print(f"tap 后帧数={len(post_frames)}（窗口 {post_span:.1f}s）")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))