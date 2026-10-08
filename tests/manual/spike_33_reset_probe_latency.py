#!/usr/bin/env python3
"""RESET_VIDEO 探针应答延迟 spike（方案 34 调研用，非 pytest）。

测量链路：WS 发 request_keyframe → 后端转发 RESET_VIDEO(1B) → 设备重建
视频管线 → 新帧经 WS 到达。

两条时间线：
    1. 被动观测段（warm 后 N 秒）：帧到达节奏（间隔分布 / burst-gap / fps）
    2. 探针段：每针「请求→下一帧到达」「请求→下一 IDR」延迟分布

背景：打包版日志（.33）显示 idle_reset 探针发出后 5-6s 无数据到达 →
EncoderStalledError → 全量重启风暴。待验证假说：设备端编码会话重建时间
超过探针判死窗口（idle_reset=5s），属结构性误判。

用法：
    python tests/manual/spike_33_reset_probe_latency.py
        [--device 192.168.8.33:5555] [--base ws://127.0.0.1:8765]
        [--warm 8] [--passive 60] [--probes 20] [--spacing 4]

注意：默认连 8765（打包版服务）。勿同时起 dev 后端（SO_REUSEADDR
双绑定陷阱：两者都显示绑定成功，但只有一个收到流量）。
"""

import argparse
import asyncio
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


def pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return s[min(len(s) - 1, int(q * len(s)))]


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", default="192.168.8.33:5555")
    ap.add_argument("--base", default="ws://127.0.0.1:8765")
    ap.add_argument("--warm", type=float, default=8.0)
    ap.add_argument("--passive", type=float, default=60.0)
    ap.add_argument("--probes", type=int, default=20)
    ap.add_argument("--spacing", type=float, default=4.0)
    args = ap.parse_args()

    url = f"{args.base}/ws/video/{args.device}"
    print(f"[spike] 连接 {url}")
    t0 = time.monotonic()
    frames: list[tuple[float, int, int | None, int]] = []
    text_msgs: list[tuple[float, dict]] = []
    stop = asyncio.Event()

    try:
        async with connect(url, max_size=None) as ws:

            async def reader() -> None:
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
                        text_msgs.append((t, j))
                        if j.get("type") in ("config", "restarting", "error",
                                             "stream_ended", "stalled"):
                            print(f"[spike] {j.get('type')} @t={t:.2f}s: {j}")
                    elif isinstance(msg, bytes) and len(msg) > 8:
                        pts = int.from_bytes(msg[:8], "big")
                        payload = msg[8:]
                        frames.append((t, len(payload), first_nalu_type(payload), pts))

            reader_task = asyncio.create_task(reader())

            deadline = time.monotonic() + 30
            while not frames and time.monotonic() < deadline:
                await asyncio.sleep(0.2)
            if not frames:
                stop.set()
                reader_task.cancel()
                print("[spike] 30s 内未收到任何帧（设备离线/第二客户端空流？），中止")
                for t, j in text_msgs[:10]:
                    print(f"  msg @{t:.1f}s: {j}")
                return 1
            print(f"[spike] 首帧 @t={frames[0][0]:.2f}s，warm {args.warm}s…")
            await asyncio.sleep(args.warm)

            t_pw_start = time.monotonic() - t0
            print(f"[spike] === 被动观测段 {args.passive}s（不发任何请求）===")
            await asyncio.sleep(args.passive)
            t_pw_end = time.monotonic() - t0

            print(f"[spike] === 探针段：{args.probes} 针 × 间隔 {args.spacing}s ===")
            probes: list[float] = []
            for i in range(args.probes):
                t_req = time.monotonic() - t0
                await ws.send(json.dumps({"op": "request_keyframe"}))
                probes.append(t_req)
                print(f"[spike] 第 {i + 1} 针 @t={t_req:.2f}s")
                await asyncio.sleep(args.spacing)

            stop.set()
            reader_task.cancel()
            try:
                await reader_task
            except (asyncio.CancelledError, Exception):
                pass
    except Exception as e:
        stop.set()
        print(f"[spike] 连接异常: {e}")
        return 1

    # ===== 被动段：帧节奏 =====
    pf = [f for f in frames if t_pw_start <= f[0] < t_pw_end]
    print(f"\n===== 被动观测段（{args.passive:.0f}s，{len(pf)} 帧）=====")
    if len(pf) >= 2:
        span = pf[-1][0] - pf[0][0]
        ivals = [pf[i + 1][0] - pf[i][0] for i in range(len(pf) - 1)]
        print(f"有效时长={span:.1f}s fps={len(pf) / span:.2f}")
        print(f"帧间隔 p50={pct(ivals, .5) * 1000:.0f}ms p90={pct(ivals, .9) * 1000:.0f}ms "
              f"max={max(ivals) * 1000:.0f}ms")
        over5 = [v for v in ivals if v >= 5.0]
        over10 = [v for v in ivals if v >= 10.0]
        print(f"间隔 >=5s 次数={len(over5)}（生产中每处触发 1 针 RESET_VIDEO）"
              f"  >=10s 次数={len(over10)}（生产中判死 → EncoderStalledError）")
        if over5:
            print(f"  >=5s 间隔明细: {[round(v, 1) for v in over5]}")
    else:
        print(f"帧数不足（{len(pf)}），无法统计")

    # ===== 探针段：应答延迟 =====
    print(f"\n===== 探针段应答延迟（{len(probes)} 针）=====")
    lat_next: list[float] = []
    lat_idr: list[float] = []
    print("  针#  请求t(s)  下一帧延迟   首NALU  下一IDR延迟  帧大小B")
    for i, t_req in enumerate(probes):
        nxt = next((f for f in frames if f[0] >= t_req), None)
        t_end = probes[i + 1] if i + 1 < len(probes) else t_req + 12.0
        nxt_idr = next((f for f in frames if t_req <= f[0] < t_end and f[2] == 5), None)
        lat = (nxt[0] - t_req) if nxt else None
        latd = (nxt_idr[0] - t_req) if nxt_idr else None
        if lat is not None:
            lat_next.append(lat)
        if latd is not None:
            lat_idr.append(latd)
        lat_s = f"{lat * 1000:>7.0f}ms" if lat is not None else "  无帧!!"
        latd_s = f"{latd * 1000:>8.0f}ms" if latd is not None else "  窗口内无IDR"
        ntype = nxt[2] if nxt else "-"
        size = nxt[1] if nxt else 0
        print(f"  {i + 1:>3}  {t_req:>8.2f}  {lat_s}      {ntype:>4}  {latd_s}  {size:>8}")

    print("\n===== 汇总 =====")
    if lat_next:
        over5 = [v for v in lat_next if v >= 5.0]
        print(f"探针→下一帧延迟: p50={statistics.median(lat_next) * 1000:.0f}ms "
              f"p90={pct(lat_next, .9) * 1000:.0f}ms max={max(lat_next) * 1000:.0f}ms "
              f"（>=5s 判死阈值: {len(over5)}/{len(lat_next)} 针）")
    if lat_idr:
        print(f"探针→首 IDR 延迟: p50={statistics.median(lat_idr) * 1000:.0f}ms "
              f"p90={pct(lat_idr, .9) * 1000:.0f}ms max={max(lat_idr) * 1000:.0f}ms "
              f"（{len(lat_idr)}/{len(probes)} 针窗口内检出）")
    else:
        print("探针→首 IDR: 全部探针窗口内未检出 IDR（设备未响应 RESET_VIDEO？）")

    rt_msgs = [(t, j) for t, j in text_msgs
               if j.get("type") in ("restarting", "error", "stream_ended", "stalled")]
    print(f"窗口内流中断类消息: {len(rt_msgs)} 条")
    for t, j in rt_msgs[:10]:
        print(f"  @{t:.1f}s {j}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))