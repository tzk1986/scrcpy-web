"""一次性 spike：对照复现 v0.7.17 exe 关流退出时的 asyncio 'Event loop is closed'/closed pipe 噪音。

用法：先起服（8766/5039），再 PYTHONPATH=backend python tests/manual/spike_54_exit_tb.py
步骤：连 .25 WS 起流→键/剪贴板一轮→正常 close→POST /api/system/shutdown→看服务端日志尾。
"""
import asyncio
import json
import os
import urllib.request

DEVICE = os.environ.get("SMOKE_DEVICE", "192.168.8.25:5555")
PORT = 8766


def http(method, path):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


async def main():
    import websockets

    async with websockets.connect(f"ws://127.0.0.1:{PORT}/ws/video/{DEVICE}",
                                  max_size=None) as ws:
        frames = 0
        try:
            while frames < 6:
                msg = await asyncio.wait_for(ws.recv(), timeout=15)
                if isinstance(msg, (bytes, bytearray)):
                    frames += 1
        except asyncio.TimeoutError:
            pass
        print(f"frames={frames}", flush=True)
        await ws.send(json.dumps({"action": "key", "keycode": 4}))
        await ws.send(json.dumps({"action": "clipboard_set", "text": "TB_SPIKE", "paste": False}))
        await asyncio.sleep(1.0)
        await ws.send(json.dumps({"action": "clipboard_get", "copy_key": 0}))
        await asyncio.sleep(1.0)
    print("ws closed cleanly", flush=True)
    await asyncio.sleep(2)
    print("shutdown:", http("POST", "/api/system/shutdown"), flush=True)


asyncio.run(main())
