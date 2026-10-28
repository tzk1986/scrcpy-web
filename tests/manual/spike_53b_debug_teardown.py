"""v0.7.15 调试终端关闭→全局 adb spawn 投毒 黑盒复现（一次性 spike）。

前提：dev server（pythonw 无控制台宿主，3.10 build venv）已在 8766 运行，
ANDROID_ADB_SERVER_PORT=5039 且 .18 已 connect。

序列（与用户 14:27 操作同构）：
  1) GET /api/devices 基线 200
  2) POST /api/debug/sessions → WS /ws/debug/{sid} send subscribe
     → 后端 get_or_create_shell → ConPTY spawn adb -s <dev> shell
  3) resize 一次，停留数秒（模拟打字窗口）
  4) 关 WS + DELETE session（触发 shell.stop 3s 超时→taskkill 链）
  5) 轮询 GET /api/devices 15s：出现 500/WinError 6 → REPRODUCED

用法：PYTHONPATH=backend:. build/venv/Scripts/python.exe tests/manual/spike_53b_debug_teardown.py [--device 192.168.8.18:5555]
"""
import argparse
import asyncio
import json
import sys
import urllib.request

BASE = "http://127.0.0.1:8766"
WS = "ws://127.0.0.1:8766"


def http(method: str, path: str, timeout: float = 15.0):
    req = urllib.request.Request(BASE + path, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()[:200]
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:200]


async def main() -> int:
    from websockets import connect

    ap = argparse.ArgumentParser()
    ap.add_argument("--device", default="192.168.8.18:5555")
    args = ap.parse_args()

    st, _ = http("GET", "/api/devices")
    print(f"baseline /api/devices: {st}")
    if st != 200:
        print("server not clean, abort")
        return 2

    st, body = http("POST", f"/api/debug/sessions?device_id={args.device}&user_id=spike")
    print(f"create session: {st} {body}")
    sid = json.loads(body)["session_id"]

    async with connect(f"{WS}/ws/debug/{sid}", max_size=None) as ws:
        await ws.send(json.dumps({"op": "subscribe"}))
        try:
            msg = await asyncio.wait_for(ws.recv(), timeout=20)
            print(f"first frame: {msg[:120]!r}")
        except asyncio.TimeoutError:
            print("WARN: no frame after subscribe (shell may not have spawned)")
        await ws.send(json.dumps({"op": "resize", "cols": 60, "rows": 39}))
        await asyncio.sleep(3)
    print("ws closed")

    st, body = http("DELETE", f"/api/debug/sessions/{sid}")
    print(f"delete session: {st} {body}")

    poisoned = False
    for i in range(8):
        await asyncio.sleep(2)
        st, body = http("GET", "/api/devices")
        print(f"poll{i}: {st} {body[:80]!r}")
        if st == 500:
            poisoned = True
            break
    print("VERDICT:", "REPRODUCED (WinError 6 poison after debug teardown)"
          if poisoned else "NOT REPRODUCED")
    return 0 if poisoned else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
