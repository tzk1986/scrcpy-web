"""方案 54 真机冒烟（协议层）：键盘扩展帧 / 滚轮 / text / 剪贴板双向 / 兜底不污染。

用法：PYTHONPATH=backend python tests/manual/smoke_54.py
隔离：BACKEND_PORT=8766、ANDROID_ADB_SERVER_PORT=5039；设备 SMOKE_DEVICE 默认 .25。

注：.19 剪贴板 get 不回帧属该 ROM 个例（ClipboardManager hidden API 失效，
上游 text==null 设计即静默），后端正确降级 clipboard_error 不断控；冒烟默认
设备换用 .25（7.1.2 全通，另 .22/A9 亦全通）。

判据：
  ①/api/devices 200 且目标设备在列
  ②WS 起流：config 文本 + ≥5 个二进制帧
  ③旧格式 key（虚拟键）、扩展 key down/up、scroll、text 帧全程不断连、无 error 应答
  ④text 300 边界与 600 两分段发送不断控（服务端 INJECT_TEXT 上限实证）
  ⑤clipboard_set(中文) → clipboard_get 取回内容一致（G3 双向主链路）
  ⑥clipboard_get copy_key=1 得到 clipboard/clipboard_error 任一协议应答（不崩不断）
  ⑦关闭 WS 后 devices 仍 200（会话与 reader 收口无残留）
  ⑧shutdown 干净退出

浏览器键盘捕获手感类判据（打字表单/长按 repeat/Esc 退出/粘贴/失焦补 up）
属人工验收清单，见方案 54 §T4。
"""
import asyncio
import json
import os
import subprocess as sp
import sys
import time
import urllib.request
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_DEVICE = os.environ.get("SMOKE_DEVICE", "192.168.8.25:5555")
_PORT = 8766
_ENV = {
    "ANDROID_ADB_SERVER_PORT": "5039",
    "BACKEND_PORT": str(_PORT),
}


def http(method: str, path: str, timeout: float = 15.0) -> tuple[int, bytes]:
    req = urllib.request.Request(f"http://127.0.0.1:{_PORT}{path}", method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def adb(*args: str) -> None:
    sp.run(
        ["adb", "-P", "5039", *args],
        capture_output=True, text=True, timeout=30,
        env={**os.environ, "MSYS_NO_PATHCONV": "1"},
        check=False,
    )


class Recorder:
    """WS 接收环：统计二进制帧与文本消息，clipboard 应答入队列。"""

    def __init__(self) -> None:
        self.binary_frames = 0
        self.texts: list[dict] = []
        self.clipboard_events: asyncio.Queue[dict] = asyncio.Queue()
        self.errors: list[dict] = []
        self.closed = False

    def feed(self, raw) -> None:
        if isinstance(raw, (bytes, bytearray)):
            self.binary_frames += 1
            return
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            return
        self.texts.append(msg)
        if msg.get("type") in ("clipboard", "clipboard_error"):
            self.clipboard_events.put_nowait(msg)
        elif msg.get("type") == "error":
            self.errors.append(msg)


async def main() -> int:
    import websockets

    fails: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}", flush=True)
        if not ok:
            fails.append(name)

    # 服务与设备准备
    env = {k: v for k, v in os.environ.items() if k != "PYTHONHOME"}
    env.update(_ENV)
    server = sp.Popen(
        [sys.executable, str(_ROOT / "run_server.py")],
        cwd=str(_ROOT), env=env,
        stdin=sp.DEVNULL, stdout=sp.DEVNULL, stderr=sp.STDOUT,
    )
    try:
        ready = False
        deadline = time.time() + 40
        while time.time() < deadline:
            try:
                if http("GET", "/api/devices")[0] == 200:
                    ready = True
                    break
            except OSError:
                pass
            await asyncio.sleep(0.5)
        check("①服务就绪", ready)
        if not ready:
            return 1

        adb("connect", _DEVICE)
        body = json.loads(http("GET", "/api/devices")[1])
        devices = [d.get("device_id", d.get("id", "")) for d in (body if isinstance(body, list) else body.get("devices", []))]
        check("①设备在列", any(_DEVICE in str(d) for d in devices), str(devices))

        rec = Recorder()
        async with websockets.connect(
            f"ws://127.0.0.1:{_PORT}/ws/video/{_DEVICE}", max_size=None
        ) as ws:

            async def pump() -> None:
                try:
                    async for raw in ws:
                        rec.feed(raw)
                except Exception:
                    rec.closed = True

            pump_task = asyncio.create_task(pump())

            async def send(obj: dict) -> None:
                await ws.send(json.dumps(obj))

            async def settle(seconds: float = 1.0) -> None:
                await asyncio.sleep(seconds)

            # ②起流：等 config + 帧
            deadline = time.time() + 12
            while time.time() < deadline and (
                not any(t.get("type") == "config" for t in rec.texts)
                or rec.binary_frames < 5
            ):
                await asyncio.sleep(0.5)
            check(
                "②起流出帧",
                any(t.get("type") == "config" for t in rec.texts) and rec.binary_frames >= 5,
                f"binary={rec.binary_frames}",
            )

            # ③键盘扩展/滚轮/text 帧不断连（静屏无新帧属正常，不判帧增量）
            await send({"action": "key", "keycode": 4})  # 旧格式虚拟键
            await settle(0.3)
            await send({"action": "key", "keycode": 29, "down": True, "repeat": 0, "meta": 0})
            await send({"action": "key", "keycode": 29, "down": False, "repeat": 0, "meta": 0})
            await send({"action": "key", "keycode": 29, "down": True, "repeat": 1, "meta": 1})
            await send({"action": "key", "keycode": 29, "down": False, "repeat": 0, "meta": 0})
            await send({"action": "scroll", "x": 100, "y": 100, "h_scroll": 0, "v_scroll": 1})
            await send({"action": "text", "text": "ab"})
            await settle(1.5)
            check(
                "③输入帧不断连无错误",
                not rec.closed and not rec.errors,
                f"errors={rec.errors}",
            )

            # ④text 300 边界与 600 两分段
            await send({"action": "text", "text": "x" * 300})
            await send({"action": "text", "text": "y" * 300})
            await settle(1.0)
            check("④长文本不断控", not rec.closed, f"closed={rec.closed}")

            # ⑤剪贴板双向：set(中文) → get 一致
            payload = "冒烟测试SMOKE54"
            await send({"action": "clipboard_set", "text": payload, "paste": False})
            await settle(0.8)
            await send({"action": "clipboard_get", "copy_key": 0})
            try:
                reply = await asyncio.wait_for(rec.clipboard_events.get(), timeout=5)
            except asyncio.TimeoutError:
                reply = {"type": "timeout"}
            check(
                "⑤剪贴板双向一致",
                reply.get("type") == "clipboard" and reply.get("text") == payload,
                str(reply),
            )

            # ⑥copy_key=1 协议应答（clipboard 或 clipboard_error 均可）
            await send({"action": "clipboard_get", "copy_key": 1})
            try:
                reply2 = await asyncio.wait_for(rec.clipboard_events.get(), timeout=5)
                check(
                    "⑥copy_key=1 有协议应答",
                    reply2.get("type") in ("clipboard", "clipboard_error"),
                    str(reply2),
                )
            except asyncio.TimeoutError:
                check("⑥copy_key=1 有协议应答", False, "timeout")

            await ws.close()
            pump_task.cancel()

        # ⑦关会话后服务健康
        st, _ = http("GET", "/api/devices")
        check("⑦关会话后 devices 200", st == 200, f"status={st}")

        # ⑧shutdown 干净退出（router prefix=/api/system）
        st, _ = http("POST", "/api/system/shutdown")
        check("⑧shutdown accepted", st == 200, f"status={st}")
        try:
            server.wait(timeout=15)
        except sp.TimeoutExpired:
            server.kill()
        check("⑧进程退出", server.returncode == 0, f"rc={server.returncode}")
    finally:
        if server.poll() is None:
            server.kill()

    print("\nSMOKE_54 " + ("PASS" if not fails else f"FAIL: {fails}"), flush=True)
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
