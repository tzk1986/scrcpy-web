"""v0.7.15 投毒复现 spike D：真实视频流并发下的调试终端 teardown。

与 spike B 的差别：先开 /ws/video 出流（encoder/adb forward spawn 压力），
再按用户时间线开→关调试终端，随后关视频流，全程轮询 /api/devices 侦测
WinError 6 投毒（500）。用户时间线：debug shell_stopped 后 ~1s 视频 stop，
06:27:30.6 最后一次成功 spawn，06:27:35 起全灭。

前提：8766 dev server 已运行（pythonw + ANDROID_ADB_SERVER_PORT=5039，.18 已 connect）。

用法：PYTHONPATH=backend:. build/venv/Scripts/python.exe tests/manual/spike_53d_video_debug_combo.py [--device 192.168.8.18:5555]
"""
import argparse
import asyncio
import json
import urllib.parse
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
    dev_enc = urllib.parse.quote(args.device, safe="")

    st, _ = http("GET", "/api/devices")
    print(f"baseline /api/devices: {st}")
    if st != 200:
        print("server not clean, abort")
        return 2

    # 1) 视频流出流 6s
    vws = await connect(f"{WS}/ws/video/{dev_enc}", max_size=None)
    got = 0
    try:
        while got < 60:
            msg = await asyncio.wait_for(vws.recv(), timeout=15)
            got += 1
        print(f"video frames received: {got}")
    except (asyncio.TimeoutError, Exception) as e:
        print(f"video recv issue after {got}: {e!r}")

    # 2) 调试终端开 → resize → 停留
    st, body = http("POST", f"/api/debug/sessions?device_id={args.device}&user_id=spike")
    print(f"create debug session: {st} {body[:80]}")
    sid = json.loads(body)["session_id"]
    dws = await connect(f"{WS}/ws/debug/{sid}", max_size=None)
    await dws.send(json.dumps({"op": "subscribe"}))
    try:
        msg = await asyncio.wait_for(dws.recv(), timeout=20)
        print(f"debug first frame: {msg[:100]!r}")
    except asyncio.TimeoutError:
        print("WARN: no debug frame (shell may not have spawned)")
    await dws.send(json.dumps({"op": "resize", "cols": 60, "rows": 39}))
    await asyncio.sleep(3)

    # 3) 关调试（WS+DELETE → shell.stop 3s 超时 taskkill 链）
    await dws.close()
    st, body = http("DELETE", f"/api/debug/sessions/{sid}")
    print(f"delete debug session: {st}")
    await asyncio.sleep(2)

    # 4) 关视频流（触发 stream stop：terminate + forward --remove）
    await vws.close()
    print("video ws closed")

    # 5) 轮询侦测投毒
    poisoned = False
    for i in range(16):
        await asyncio.sleep(2)
        st, body = http("GET", "/api/devices")
        if st != 200:
            print(f"poll{i}: {st} {body[:120]!r}")
            poisoned = True
            break
        if i % 4 == 0:
            print(f"poll{i}: 200 ok")
    print("VERDICT:", "REPRODUCED (poison after video+debug teardown combo)"
          if poisoned else "NOT REPRODUCED")
    return 0 if poisoned else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
