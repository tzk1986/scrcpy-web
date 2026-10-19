# tests/manual/spike_34d5_offline.py
"""方案 34 D5（offline 退避）模拟验证 spike（不入 CI）。

用法：
  1) 隔离实例起后端：BACKEND_PORT=8766 ANDROID_ADB_SERVER_PORT=5039 \
     python run_server.py（日志重定向到 logs/）
  2) adb -P 5039 connect <device> 后本脚本连接 /ws/video/<device>，
     保持读取二进制帧并回显所有文本消息（error/stream_ended 等）
  3) warm 秒后脚本打印 DISCONNECT_MARK，外层执行
     adb -P 5039 disconnect <device> 构造 offline，cold 窗观测后端
     探针/重启行为是否在既有预算（方案 19：60s 内 ≤3 次 + 递增 sleep +
     上抛终止）内收敛；最后 recovery 模式验证恢复出帧
"""
import argparse
import asyncio
import time

import websockets


async def run_phase(ws_url: str, duration: float, label: str) -> None:
    t0 = time.monotonic()

    def mark(msg: str) -> None:
        print(f"[SPIKE {label} t={time.monotonic() - t0:6.1f}s] {msg}", flush=True)

    mark(f"connecting {ws_url}")
    try:
        ws = await websockets.connect(ws_url, max_size=None, open_timeout=60)
    except Exception as e:
        mark(f"connect failed: {type(e).__name__}: {e}")
        return
    mark("ws open")
    frames = 0
    total_bytes = 0
    last_report = time.monotonic()
    deadline = time.monotonic() + duration
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                mark("phase duration end")
                break
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=min(1.0, remaining))
            except asyncio.TimeoutError:
                if time.monotonic() - last_report >= 2.0:
                    mark(f"frames={frames} bytes={total_bytes}")
                    frames = 0
                    total_bytes = 0
                    last_report = time.monotonic()
                continue
            except websockets.ConnectionClosed as e:
                code = e.rcvd.code if e.rcvd else "?"
                reason = e.rcvd.reason if e.rcvd else "?"
                mark(f"ws closed by server: code={code} reason={reason}")
                break
            if isinstance(msg, (bytes, bytearray)):
                frames += 1
                total_bytes += len(msg)
            else:
                mark(f"text: {str(msg)[:300]}")
    finally:
        await ws.close()


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", default="192.168.8.25:5555")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--mode", choices=["stream", "recovery"], default="stream")
    parser.add_argument("--warm", type=float, default=15.0)
    parser.add_argument("--cold", type=float, default=120.0)
    args = parser.parse_args()
    ws_url = f"ws://127.0.0.1:{args.port}/ws/video/{args.device}"
    if args.mode == "recovery":
        await run_phase(ws_url, 20.0, "recovery")
        return

    async def stream_with_marker() -> None:
        task = asyncio.create_task(run_phase(ws_url, args.warm + args.cold, "stream"))
        await asyncio.sleep(args.warm)
        print("[SPIKE] >>> DISCONNECT_MARK <<<", flush=True)
        await task

    await stream_with_marker()


asyncio.run(main())
