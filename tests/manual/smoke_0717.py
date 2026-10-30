"""v0.7.17 发布冒烟：exe 实例（PIPE 父拓扑）跑方案 54 协议层判据。

用法：PYTHONPATH=backend:. python tests/manual/smoke_0717.py
隔离：BACKEND_PORT=8766、ANDROID_ADB_SERVER_PORT=5039；设备 SMOKE_DEVICE 默认 .25。

判据：
  ①exe 起服 port.txt=8766 且 devices 200、目标设备在列
  ②WS 起流：config 文本 + ≥5 个二进制帧
  ③键盘旧格式/扩展 down-up/repeat/meta、scroll、text 帧全程不断连、无 error 应答
  ④text 300×2 分段不断控
  ⑤clipboard_set(中文) → clipboard_get 一致（exe 内 G3 主链路）
  ⑥clipboard_get copy_key=1 有协议应答（clipboard/clipboard_error 任一）
  ⑬剪贴板镜像链（方案 55）：开镜像收到首轮 clipboard_push；set 同文后
    连续两轮不弹推（环回抑制+基线同步）；关镜像后无新 push
  ⑦关 WS 后 devices 仍 200
  ⑧POST /api/system/shutdown → 进程干净退出 rc=0、运行期零 Traceback、
    关闭期仅放行 Exception ignored in 前缀的 asyncio GC 噪音（Windows
    loop 关闭后 __del__ 慢病，见进度追踪一百一十九次；真实异常不放行）、
    8766 释放
"""
import asyncio
import json
import os
import subprocess as sp
import sys
import time
import urllib.request
from pathlib import Path

# GBK 控制台打不出 ⑩⑪⑫⑬ 等带圈字符（U+246A 起不在 GBK）——stdout 强制 UTF-8
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_ROOT = Path(__file__).resolve().parents[2]
_EXE = _ROOT / "dist" / "OpenScrcpy" / "OpenScrcpy.exe"
_PORT_FILE = _ROOT / "dist" / "OpenScrcpy" / "data" / "port.txt"
_LOG = _ROOT / "dist" / "OpenScrcpy" / "logs" / "openscrcpy.log"
_DEVICE = os.environ.get("SMOKE_DEVICE", "192.168.8.20:5555")
_PORT = 8766
_ENV = {"ANDROID_ADB_SERVER_PORT": "5039", "BACKEND_PORT": str(_PORT)}


def http(method: str, path: str, timeout: float = 15.0) -> tuple[int, bytes]:
    req = urllib.request.Request(f"http://127.0.0.1:{_PORT}{path}", method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def adb(*args: str) -> None:
    sp.run(["adb", "-P", "5039", *args], capture_output=True, text=True,
           timeout=30, env={**os.environ, "MSYS_NO_PATHCONV": "1"}, check=False)


class Recorder:
    def __init__(self) -> None:
        self.binary_frames = 0
        self.texts: list[dict] = []
        self.clipboard_events: asyncio.Queue[dict] = asyncio.Queue()
        self.mirror_events: asyncio.Queue[dict] = asyncio.Queue()
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
        elif msg.get("type") in ("clipboard_push", "clipboard_mirror"):
            self.mirror_events.put_nowait(msg)
        elif msg.get("type") == "error":
            self.errors.append(msg)


async def main() -> int:
    import websockets

    fails: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}", flush=True)
        if not ok:
            fails.append(name)

    if not _EXE.exists():
        print(f"exe not found: {_EXE}", flush=True)
        return 1
    if _PORT_FILE.exists():
        _PORT_FILE.unlink()

    env = {k: v for k, v in os.environ.items() if k != "PYTHONHOME"}
    env.update(_ENV)
    # PIPE 父拓扑（方案 53 事发拓扑，顺带回归 std 治理）
    child = sp.Popen([str(_EXE)], stdin=sp.PIPE, stdout=sp.PIPE,
                     stderr=sp.STDOUT, cwd=str(_EXE.parent), env=env)
    print(f"exe pid={child.pid}", flush=True)
    try:
        port = None
        deadline = time.time() + 60
        while time.time() < deadline:
            if _PORT_FILE.exists():
                try:
                    port = int(_PORT_FILE.read_text().strip())
                    break
                except ValueError:
                    pass
            await asyncio.sleep(0.5)
        if port != _PORT:
            check("①exe 起服端口", False, f"port={port}")
            return 1

        adb("connect", _DEVICE)
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
        body = json.loads(http("GET", "/api/devices")[1])
        devices = [d.get("device_id", d.get("id", ""))
                   for d in (body if isinstance(body, list) else body.get("devices", []))]
        check("①devices 200 且在列", ready and any(_DEVICE in str(d) for d in devices),
              str(devices))
        if not ready:
            return 1

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

            deadline = time.time() + 15
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

            await send({"action": "key", "keycode": 4})
            await settle(0.3)
            await send({"action": "key", "keycode": 29, "down": True, "repeat": 0, "meta": 0})
            await send({"action": "key", "keycode": 29, "down": False, "repeat": 0, "meta": 0})
            await send({"action": "key", "keycode": 29, "down": True, "repeat": 1, "meta": 1})
            await send({"action": "key", "keycode": 29, "down": False, "repeat": 0, "meta": 0})
            await send({"action": "scroll", "x": 100, "y": 100, "h_scroll": 0, "v_scroll": 1})
            await send({"action": "text", "text": "ab"})
            await settle(1.5)
            check("③输入帧不断连无错误", not rec.closed and not rec.errors,
                  f"errors={rec.errors}")

            await send({"action": "text", "text": "x" * 300})
            await send({"action": "text", "text": "y" * 300})
            await settle(1.0)
            check("④长文本不断控", not rec.closed, f"closed={rec.closed}")

            payload = "冒烟测试SMOKE0717"
            await send({"action": "clipboard_set", "text": payload, "paste": False})
            await settle(0.8)
            await send({"action": "clipboard_get", "copy_key": 0})
            try:
                reply = await asyncio.wait_for(rec.clipboard_events.get(), timeout=5)
            except asyncio.TimeoutError:
                reply = {"type": "timeout"}
            check("⑤剪贴板双向一致",
                  reply.get("type") == "clipboard" and reply.get("text") == payload,
                  str(reply))

            await send({"action": "clipboard_get", "copy_key": 1})
            try:
                reply2 = await asyncio.wait_for(rec.clipboard_events.get(), timeout=5)
                check("⑥copy_key=1 有协议应答",
                      reply2.get("type") in ("clipboard", "clipboard_error"), str(reply2))
            except asyncio.TimeoutError:
                check("⑥copy_key=1 有协议应答", False, "timeout")

            # ⑬ 剪贴板镜像链（方案 55）：首轮推送 → set 环回不弹推 → 关闭后静默
            await send({"action": "clipboard_mirror", "enabled": True})
            mirror_seen: list[dict] = []
            first_push: dict | None = None
            deadline = time.time() + 4.0
            while first_push is None and time.time() < deadline:
                try:
                    ev = await asyncio.wait_for(rec.mirror_events.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                mirror_seen.append(ev)
                if ev.get("type") == "clipboard_push":
                    first_push = ev
            check("⑬镜像开启首轮推送", first_push is not None, str(mirror_seen))

            mirror_text = "镜像冒烟MIRROR0717"
            await send({"action": "clipboard_set", "text": mirror_text,
                        "paste": False})
            await settle(2.5)  # 覆盖 ≥2 轮轮询：抑制一次 + 基线同步后再读同文
            while not rec.mirror_events.empty():
                mirror_seen.append(rec.mirror_events.get_nowait())
            loopback = any(e.get("type") == "clipboard_push"
                           and e.get("text") == mirror_text for e in mirror_seen)
            await send({"action": "clipboard_mirror", "enabled": False})
            await settle(1.5)
            tail_pushes = []
            while not rec.mirror_events.empty():
                ev = rec.mirror_events.get_nowait()
                if ev.get("type") == "clipboard_push":
                    tail_pushes.append(ev)
            check("⑬环回抑制且关闭后静默",
                  not loopback and not tail_pushes,
                  f"loopback={loopback} tail={tail_pushes} seen={mirror_seen[-3:]}")

            await ws.close()
            pump_task.cancel()

        st, _ = http("GET", "/api/devices")
        check("⑦关会话后 devices 200", st == 200, f"status={st}")

        st, _ = http("POST", "/api/system/shutdown")
        check("⑧shutdown accepted", st == 200, f"status={st}")
        try:
            out, _ = child.communicate(timeout=20)
        except sp.TimeoutExpired:
            child.kill()
            out, _ = child.communicate()
        check("⑧进程退出 rc=0", child.returncode == 0, f"rc={child.returncode}")

        if _LOG.exists():
            text = _LOG.read_text(encoding="utf-8", errors="replace")
            lines = text.splitlines()
            stop_idx = max(
                (i for i, ln in enumerate(lines) if "api/system/shutdown" in ln),
                default=len(lines) - 1,
            )
            runtime_tb, exit_real, exit_ign = 0, 0, 0
            for i, ln in enumerate(lines):
                if "Traceback" not in ln:
                    continue
                ign = any("Exception ignored" in lines[j]
                          for j in range(max(0, i - 3), i))
                if i <= stop_idx:
                    runtime_tb += 1
                elif ign:
                    exit_ign += 1
                else:
                    exit_real += 1
            check("⑧日志 Traceback 口径",
                  runtime_tb == 0 and exit_real == 0,
                  f"运行期={runtime_tb} 关闭期真实={exit_real} 关闭期ExceptionIgnored={exit_ign}")
    finally:
        if child.poll() is None:
            child.kill()

    released = False
    for _ in range(10):
        try:
            http("GET", "/api/devices", timeout=2)
        except OSError:
            released = True
            break
        await asyncio.sleep(0.5)
    check("⑧8766 释放", released)

    print("\nSMOKE_0717 " + ("PASS" if not fails else f"FAIL: {fails}"), flush=True)
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
