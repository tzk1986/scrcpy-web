"""v0.7.15 投毒复现 spike E：受控第二实例 exe + 逐步 PEB 探针采样。

用 python 父进程以 PIPE 给 exe 三个真 kernel 句柄（同 tessoa→exe 拓扑），
隔离 adb 5039；按用户时间线跑：视频流出流 → 调试终端开→关 → 视频关，
每一步后用 probe_53_peb_handles 从外部读 exe 的 stdin/stdout 句柄生死，
钉死投毒发生在哪一步。

用法：PYTHONPATH=backend:. build/venv/Scripts/python.exe tests/manual/spike_53e_exe_repro.py
"""
import asyncio
import json
import re
import subprocess as sp
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_EXE = _ROOT / "dist" / "OpenScrcpy" / "OpenScrcpy.exe"
_ADB = _ROOT / "dist" / "OpenScrcpy" / "_internal" / "tools" / "adb.exe"
_PORT_FILE = _ROOT / "dist" / "OpenScrcpy" / "data" / "port.txt"
_LOG = _ROOT / "dist" / "OpenScrcpy" / "logs" / "openscrcpy.log"
_DEVICE = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--device=")),
               "192.168.8.19:5555")  # .18 开发在用，换 .19
_ENV = {"ANDROID_ADB_SERVER_PORT": "5039"}


import os


def run(cmd: list[str]) -> tuple[int, str]:
    # 注意：不能给子进程空 PATH/env——Windows 上 python/adb 需要 SystemRoot
    # 等变量，否则静默失败（首跑实证）。
    p = sp.run(cmd, capture_output=True, text=True, encoding="utf-8",
               errors="replace", timeout=60, env={**os.environ, **_ENV})
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def http(base: str, method: str, path: str, timeout: float = 15.0):
    req = urllib.request.Request(base + path, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()[:300]
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:300]


def probe(pid: int, tag: str) -> str:
    _, out = run([str(_ROOT / "build" / "venv" / "Scripts" / "python.exe"),
                  str(_ROOT / "tests" / "manual" / "probe_53_peb_handles.py"), str(pid)])
    m = {}
    for name in ("stdin", "stdout", "stderr"):
        mm = re.search(rf"{name}: 0x[0-9a-f]+ DuplicateHandle -> (\w+)", out)
        m[name] = mm.group(1) if mm else "?"
    line = f"[{tag}] pid={pid} stdin={m['stdin']} stdout={m['stdout']} stderr={m['stderr']}"
    if "?" in m.values():
        line += f" raw={out[:300]!r}"
    print(line, flush=True)
    return line


async def main() -> int:
    # 0) 清理 .18 上的孤儿 scrcpy 会话（上次投毒实例残留）。检测命令与应用
    #    _STRAY_GREP_CMD 同款：旧 toybox ps 的 NAME 列看不到 scrcpy 字样。
    rc, out = run([str(_ADB), "-s", _DEVICE, "shell",
                   "grep -lE 'scrcpy[.]Server' /proc/[0-9]*/cmdline 2>/dev/null"])
    pids = re.findall(r"/proc/(\d+)/", out)
    print(f"orphan scan rc={rc} pids={pids}", flush=True)
    if pids:
        run([str(_ADB), "-s", _DEVICE, "shell",
             "kill -9 " + " ".join(pids)])
        await asyncio.sleep(1)

    if _PORT_FILE.exists():
        _PORT_FILE.unlink()

    exe_env = {k: v for k, v in __import__("os").environ.items() if k != "PYTHONHOME"}
    exe_env.update(_ENV)
    child = sp.Popen([str(_EXE)], stdin=sp.PIPE, stdout=sp.PIPE, stderr=sp.PIPE,
                     cwd=str(_EXE.parent), env=exe_env)
    print(f"exe launched pid={child.pid}", flush=True)

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
    if port is None:
        print("FAIL: port.txt never appeared")
        child.kill()
        return 2
    print(f"port={port}", flush=True)
    base = f"http://127.0.0.1:{port}"
    wsbase = f"ws://127.0.0.1:{port}"

    from websockets import connect
    st, _ = http(base, "GET", "/api/devices")
    print(f"baseline devices: {st}", flush=True)

    probe(child.pid, "step0-start")

    # 1) 视频流【必须】出流：被残留守卫挡时清孤儿重试，收不满帧直接中止实验
    async def clear_orphans():
        _, out = run([str(_ADB), "-s", _DEVICE, "shell",
                      "grep -lE 'scrcpy[.]Server' /proc/[0-9]*/cmdline 2>/dev/null"])
        pids = re.findall(r"/proc/(\d+)/", out)
        if pids:
            run([str(_ADB), "-s", _DEVICE, "shell", "kill -9 " + " ".join(pids)])
            await asyncio.sleep(1.5)
        return pids

    vws = None
    got = 0
    for attempt in range(6):
        try:
            vws = await connect(f"{wsbase}/ws/video/{urllib.parse.quote(_DEVICE, safe='')}",
                                max_size=None)
            first = await asyncio.wait_for(vws.recv(), timeout=30)
            if isinstance(first, str) and '"error"' in first:
                print(f"video blocked (attempt {attempt}): {first[:160]}", flush=True)
                await vws.close()
                vws = None
                await clear_orphans()
                await asyncio.sleep(3)
                continue
            got = 1
            while got < 40:
                try:
                    msg = await asyncio.wait_for(vws.recv(), timeout=15)
                    if isinstance(msg, str) and '"error"' in msg:
                        raise RuntimeError(f"mid-stream error frame: {msg[:160]}")
                    got += 1
                except asyncio.TimeoutError:
                    break
        except Exception as e:
            print(f"video issue (attempt {attempt}): {e!r}", flush=True)
            if vws is not None:
                try:
                    await vws.close()
                except Exception:
                    pass
                vws = None
            got = 0
            await clear_orphans()
            await asyncio.sleep(3)
            continue
        if got >= 10:
            break
        print(f"video too few frames ({got}, attempt {attempt}), retry", flush=True)
        await vws.close()
        vws = None
        await asyncio.sleep(2)
    if vws is None or got < 10:
        print(f"FAIL: video never flowed (frames={got}); aborting experiment",
              flush=True)
        try:
            http(base, "POST", "/api/system/shutdown", timeout=20)
        except Exception:
            pass
        child.kill()
        return 3
    print(f"video frames: {got} (flowing)", flush=True)
    probe(child.pid, "step1-video-open")

    # 2) 调试终端开
    st, body = http(base, "POST",
                    f"/api/debug/sessions?device_id={_DEVICE}&user_id=spike")
    print(f"debug create: {st}", flush=True)
    sid = json.loads(body)["session_id"]
    dws = await connect(f"{wsbase}/ws/debug/{sid}", max_size=None)
    await dws.send(json.dumps({"op": "subscribe"}))
    try:
        await asyncio.wait_for(dws.recv(), timeout=20)
        print("debug first frame ok", flush=True)
    except asyncio.TimeoutError:
        print("WARN no debug frame", flush=True)
    await dws.send(json.dumps({"op": "resize", "cols": 60, "rows": 39}))
    await asyncio.sleep(3)
    probe(child.pid, "step2-debug-open")

    # 3) 关调试（WS+DELETE）
    await dws.close()
    st, _ = http(base, "DELETE", f"/api/debug/sessions/{sid}")
    print(f"debug delete: {st}", flush=True)
    await asyncio.sleep(5)
    p3 = probe(child.pid, "step3-debug-closed")

    # 4) 关视频流
    if vws is not None:
        await vws.close()
        print("video ws closed", flush=True)
    await asyncio.sleep(4)
    p4 = probe(child.pid, "step4-video-closed")

    # 5) 轮询侦测
    poisoned_at = None
    for i in range(10):
        await asyncio.sleep(2)
        st, body = http(base, "GET", "/api/devices")
        if st != 200:
            poisoned_at = f"poll{i}"
            print(f"poll{i}: {st} {body[:150]!r}", flush=True)
            break
    probe(child.pid, "step5-poll-end")
    print(f"devices poisoned: {poisoned_at is not None} at {poisoned_at}", flush=True)

    print("---- exe log tail (WinError 6 / stop marks) ----", flush=True)
    if _LOG.exists():
        txt = _LOG.read_text(encoding="utf-8", errors="replace")
        marks = [ln for ln in txt.splitlines()
                 if re.search(r"WinError|conpty|shell_stopped|stopping_scrcpy|"
                              r"terminate_error|forward|spawned", ln)]
        print("\n".join(marks[-40:]), flush=True)

    # 收尾：停机
    try:
        http(base, "POST", "/api/system/shutdown", timeout=20)
    except Exception as e:
        print(f"shutdown err {e!r}", flush=True)
    try:
        child.wait(timeout=30)
    except sp.TimeoutExpired:
        print("exe did not exit, killing", flush=True)
        child.kill()
    print("VERDICT:", "REPRODUCED via exe second instance" if poisoned_at
          else "NOT REPRODUCED via exe second instance", flush=True)
    return 0 if poisoned_at else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
