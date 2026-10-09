#!/usr/bin/env python3
"""双会话并发防踩停探针（方案 34 D4/D8 验收用，非 pytest）。

验证：设备已有活跃视频会话时，第二客户端应被守卫拒绝（后端日志
stream_active_waiting_cleanup×3 → stream_already_active；WS 仅收
stream_ended、零帧），且第一会话不断流、无重启（旧行为是 finally
无条件 stop_stream 踩停第一客户端，成 ping-pong 重启循环）。

用法：
    python tests/manual/probe_double_session_guard.py
        [--device 192.168.8.33:5555] [--base ws://127.0.0.1:8765]
        [--hold 6]

注意：默认连 8765（打包版服务）。勿同时起 dev 后端（SO_REUSEADDR
双绑定陷阱：两者都显示绑定成功，但只有一个收到流量）。运行前确认
设备无其他客户端（浏览器直播页需先关闭），否则 A 首帧等不到。
"""

import argparse
import asyncio
import json
import sys
import time

from websockets.asyncio.client import connect

sys.stdout.reconfigure(encoding="utf-8")


class Session:
    def __init__(self, name: str):
        self.name = name
        self.frames: list[float] = []  # 帧到达时刻（相对 t0）
        self.texts: list[tuple[float, str]] = []
        self.closed: str | None = None
        self.stop = asyncio.Event()
        self.first_frame = asyncio.Event()
        self.task: asyncio.Task | None = None

    def start(self, url: str, t0: float, tag: str) -> None:
        self.task = asyncio.create_task(self._run(url, t0, tag))

    async def _run(self, url: str, t0: float, tag: str) -> None:
        try:
            async with connect(url, max_size=None) as ws:
                while not self.stop.is_set():
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=0.5)
                    except asyncio.TimeoutError:
                        continue
                    except Exception as e:
                        self.closed = f"{type(e).__name__}"
                        break
                    t = time.monotonic() - t0
                    if isinstance(msg, str):
                        try:
                            j = json.loads(msg)
                        except json.JSONDecodeError:
                            continue
                        self.texts.append((t, str(j.get("type"))))
                        print(f"[guard] {tag} 收 {j.get('type')} @t={t:.2f}s")
                        if j.get("type") in ("stream_ended",):
                            self.closed = "stream_ended"
                            break
                    elif isinstance(msg, bytes) and len(msg) > 8:
                        if not self.frames:
                            self.first_frame.set()
                        self.frames.append(t)
        except Exception as e:
            self.closed = f"{type(e).__name__}: {e}"

    async def stop_and_join(self) -> None:
        self.stop.set()
        if self.task is not None:
            try:
                await asyncio.wait_for(self.task, timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self.task.cancel()

    def frames_between(self, lo: float, hi: float) -> int:
        return sum(1 for t in self.frames if lo <= t < hi)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", default="192.168.8.33:5555")
    ap.add_argument("--base", default="ws://127.0.0.1:8765")
    ap.add_argument("--hold", type=float, default=6.0,
                    help="B 被拒后第一会话继续观察秒数")
    ap.add_argument("--timeout", type=float, default=30.0, help="首帧等待上限")
    args = ap.parse_args()

    url = f"{args.base}/ws/video/{args.device}"
    print(f"[guard] 连接 {url}")
    t0 = time.monotonic()

    a = Session("A")
    b = Session("B")
    a.start(url, t0, "A(先占)")
    try:
        await asyncio.wait_for(a.first_frame.wait(), timeout=args.timeout)
    except asyncio.TimeoutError:
        print(f"[guard] A {args.timeout:.0f}s 内无首帧——设备离线或已被"
              f"其他客户端占用，中止")
        await a.stop_and_join()
        return 1
    print(f"[guard] A 首帧 @t={a.frames[0]:.2f}s，warm 3s 后发起 B…")
    await asyncio.sleep(3.0)

    t_b_conn = time.monotonic() - t0
    b.start(url, t0, "B(后到)")
    # B 应在 ~4s 内被拒（3×1s 守卫 + 回包）；给足 15s 上限
    try:
        await asyncio.wait_for(asyncio.shield(b.task), timeout=15)
    except asyncio.TimeoutError:
        pass
    t_b_end = time.monotonic() - t0

    print(f"[guard] B 结束 @t={t_b_end:.2f}s（closed={b.closed}），"
          f"A 继续观察 {args.hold:.0f}s…")
    await asyncio.sleep(args.hold)
    t_end = time.monotonic() - t0
    await a.stop_and_join()
    await b.stop_and_join()

    # ===== 判定 =====
    a_in_b = a.frames_between(t_b_conn, t_b_end)
    a_after = a.frames_between(t_b_end, t_end)
    a_bad = [t for t, typ in a.texts if typ in ("stream_ended", "restarting", "stalled")]
    b_zero = len(b.frames) == 0
    b_rejected = any(typ == "stream_ended" for _, typ in b.texts)

    print("\n===== 汇总 =====")
    print(f"A 总帧数={len(a.frames)}（B 窗口内 {a_in_b} 帧，B 后 {a_after} 帧）；"
          f"A 消息={[typ for _, typ in a.texts]} closed={a.closed}")
    print(f"B 总帧数={len(b.frames)}；B 消息={[typ for _, typ in b.texts]} "
          f"closed={b.closed}")
    ok_a = not a_bad and a_in_b > 0 and a_after > 0
    ok_b = b_zero and b_rejected
    print(f"第一会话不断流: {'PASS' if ok_a else 'FAIL'}"
          f"（中断类消息 {len(a_bad)} 条）")
    print(f"第二会话被拒零帧: {'PASS' if ok_b else 'FAIL'}"
          f"（帧 {len(b.frames)}，stream_ended {b_rejected}）")
    return 0 if (ok_a and ok_b) else 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))