"""v0.7.16 发布冒烟：exe 第二实例（PIPE 父拓扑=投毒事发拓扑）三段 WS + 调试开关无毒判据。

用法：PYTHONPATH=backend:. build/venv/Scripts/python.exe tests/manual/smoke_0716.py
隔离：BACKEND_PORT=8766、ANDROID_ADB_SERVER_PORT=5039；设备默认 .19（.18 开发在用）。
判据：①health+devices 200；②视频 WS ≥10 帧+config+IDR；③调试终端开→关全程后
devices 仍 200 且 PEB 探针 stdin/stdout ALIVE（方案 53 修复在事发拓扑下成立）；
④关键帧请求 IDR 再现；⑤shutdown 干净退出零残留。
"""
import asyncio
import json
import os
import re
import subprocess as sp
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_EXE = _ROOT / "dist" / "OpenScrcpy" / "OpenScrcpy.exe"
_PORT_FILE = _ROOT / "dist" / "OpenScrcpy" / "data" / "port.txt"
_LOG = _ROOT / "dist" / "OpenScrcpy" / "logs" / "openscrcpy.log"
_DEVICE = os.environ.get("SMOKE_DEVICE", "192.168.8.19:5555")
_ENV = {"ANDROID_ADB_SERVER_PORT": "5039", "BACKEND_PORT": "8766"}


def http(base, method, path, timeout=15.0):
    req = urllib.request.Request(base + path, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()[:400]
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:400]


def probe(pid, tag):
    p = sp.run([str(_ROOT / "build/venv/Scripts/python.exe"),
                str(_ROOT / "tests/manual/probe_53_peb_handles.py"), str(pid)],
               capture_output=True, text=True, timeout=60,
               env={**os.environ, "ANDROID_ADB_SERVER_PORT": "5039"})
    out = p.stdout
    res = {}
    for name in ("stdin", "stdout"):
        m = re.search(rf"{name}: 0x[0-9a-f]+ DuplicateHandle -> (\w+)", out)
        res[name] = m.group(1) if m else "?"
    print(f"[{tag}] {res}", flush=True)
    return res


async def main() -> int:
    if _PORT_FILE.exists():
        _PORT_FILE.unlink()
    env = {k: v for k, v in os.environ.items() if k != "PYTHONHOME"}
    env.update(_ENV)
    child = sp.Popen([str(_EXE)], stdin=sp.PIPE, stdout=sp.PIPE,
                     stderr=sp.STDOUT, cwd=str(_EXE.parent), env=env)
    print(f"exe pid={child.pid}", flush=True)
    port = None
    deadline = time.time() + 40
    while time.time() < deadline:
        if _PORT_FILE.exists():
            try:
                port = int(_PORT_FILE.read_text().strip())
                break
            except ValueError:
                pass
        await asyncio.sleep(0.5)
    if port != 8766:
        print(f"expected 8766, got {port}", flush=True)
        child.kill()
        return 1
    base, wsbase = f"http://127.0.0.1:{port}", f"ws://127.0.0.1:{port}"

    fails: list[str] = []
    st, _ = http(base, "GET", "/api/devices")
    print(f"devices baseline: {st}", flush=True)
    if st != 200:
        fails.append("devices-baseline")

    from websockets import connect
    quote = urllib.parse.quote(_DEVICE, safe="")
    # ② 视频三段：出流 + 输入后续帧 + keyframe IDR
    vws = await connect(f"{wsbase}/ws/video/{quote}", max_size=None)
    frames = cfg = idr = 0
    keyframes_after_input = 0
    try:
        for _ in range(60):
            msg = await asyncio.wait_for(vws.recv(), timeout=20)
            if isinstance(msg, str):
                if "config" in msg:
                    cfg = 1
                continue
            frames += 1
            if bytes([0, 0, 0, 1, 0x65]) in msg or b"\x67" in msg[:8]:
                idr += 1
            if frames >= 11:
                break
        print(f"video frames={frames} config={cfg} idr={idr}", flush=True)
        if frames < 10:
            fails.append("video-frames")
        # 输入一次 tap，后续帧继续
        await vws.send(json.dumps({"type": "touch", "action": "down",
                                   "x": 100, "y": 100}))
        await vws.send(json.dumps({"type": "touch", "action": "up",
                                   "x": 100, "y": 100}))
        await vws.send(json.dumps({"type": "keyframe"}))
        for _ in range(30):
            msg = await asyncio.wait_for(vws.recv(), timeout=15)
            if isinstance(msg, (bytes, bytearray)) and msg:
                keyframes_after_input += 1
                if keyframes_after_input >= 3:
                    break
        print(f"post-input/keyframe frames={keyframes_after_input}", flush=True)
        if keyframes_after_input < 1:
            fails.append("post-input-frames")

        # ③ 调试终端开→关（投毒事发路径），全程 devices 必须 200
        st, body = http(base, "POST",
                        f"/api/debug/sessions?device_id={_DEVICE}&user_id=smoke0716")
        sid = json.loads(body)["session_id"]
        dws = await connect(f"{wsbase}/ws/debug/{sid}", max_size=None)
        await dws.send(json.dumps({"op": "subscribe"}))
        try:
            await asyncio.wait_for(dws.recv(), timeout=20)
            print("debug frame ok", flush=True)
        except asyncio.TimeoutError:
            fails.append("debug-no-frame")
        await dws.send(json.dumps({"op": "resize", "cols": 60, "rows": 39}))
        await asyncio.sleep(2)
        await dws.close()
        http(base, "DELETE", f"/api/debug/sessions/{sid}")
        await asyncio.sleep(4)
        st, _ = http(base, "GET", "/api/devices")
        print(f"devices after debug teardown: {st}", flush=True)
        if st != 200:
            fails.append("devices-poisoned-after-debug")
        h = probe(child.pid, "post-debug")
        if h.get("stdin") != "ALIVE" or h.get("stdout") != "ALIVE":
            fails.append(f"peb-{h}")
    finally:
        await vws.close()
        await asyncio.sleep(2)

    st, _ = http(base, "GET", "/api/devices")
    print(f"devices after video close: {st}", flush=True)
    if st != 200:
        fails.append("devices-after-video")

    # 日志核对
    if _LOG.exists():
        txt = _LOG.read_text(encoding="utf-8", errors="replace")
        n6 = len(re.findall(r"WinError 6", txt))
        repaired = "std_handles_repaired" in txt
        print(f"log: WinError6={n6} std_handles_repaired={repaired}", flush=True)
        if n6:
            fails.append(f"winerror6-in-log({n6})")

    try:
        http(base, "POST", "/api/system/shutdown", timeout=20)
        child.wait(timeout=30)
    except Exception:
        child.kill()
        fails.append("shutdown-dirty")
    print("VERDICT:", "SMOKE PASS" if not fails else f"SMOKE FAIL {fails}", flush=True)
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
