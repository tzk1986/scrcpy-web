#!/usr/bin/env python3
"""方案 28 Phase 0 spike：流复用可行性与启动延迟基线（真机，不改生产代码）

A 组 — tunnel socket accept 语义探测（D2 生死）：
    独立 scid 起 server → 连 video+control → 读握手字节 → 全部断开 →
    观察进程存活 → 再连读字节。可重连 = D2 启用；EOF = accept-once 降级。
B 组 — JAR bak 链路（D1）：push 后 cp/mv/ls 耗时与恢复演练。
C 组 — 启动段基线：经后端 WS 连接 N 次，计 t_config / t_first_frame。

用法：
    python -u tests/manual/spike_stream_reuse.py 192.168.8.18:5555 [--groups A,B,C] [-n 10] [--stress]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

try:
    import websockets
except ImportError:
    print("需要 websockets：pip install websockets", file=sys.stderr)
    sys.exit(2)

ROOT = Path(__file__).resolve().parent.parent.parent
LOCAL_JAR = ROOT / "backend" / "app" / "scrcpy" / "scrcpy-server.jar"
REMOTE_JAR = "/data/local/tmp/scrcpy-server.jar"
BAK_JAR = REMOTE_JAR + ".bak"
SCRCPY_CLASS = "com.genymobile.scrcpy.Server"
SCRCPY_VERSION = "4.1"


def adb(device: str, *args: str, timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["adb", "-s", device, *args],
        capture_output=True, text=True, timeout=timeout,
    )


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


async def connect_retry(port: int, deadline: float) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    while True:
        try:
            return await asyncio.open_connection("127.0.0.1", port)
        except OSError:
            if time.monotonic() > deadline:
                raise TimeoutError(f"connect 127.0.0.1:{port} timeout")
            await asyncio.sleep(0.3)


async def wait_device_socket(device: str, sock_name: str, deadline: float) -> float:
    t_start = time.monotonic()
    while True:
        r = await asyncio.to_thread(
            adb, device, "shell", f"grep {sock_name} /proc/net/unix")
        if r.returncode == 0 and sock_name in r.stdout:
            return time.monotonic() - t_start
        if time.monotonic() > deadline:
            raise TimeoutError(f"device socket {sock_name} 未出现")
        await asyncio.sleep(0.3)


def server_alive(device: str) -> bool:
    r = adb(device, "shell", "ps | grep app_process | grep -v grep", timeout=10)
    return "app_process" in r.stdout


async def start_server(device: str, scid_hex: str, sock_name: str, port: int):
    inner = (
        f"CLASSPATH={REMOTE_JAR} app_process / {SCRCPY_CLASS} {SCRCPY_VERSION} "
        f"log_level=info max_size=0 max_fps=30 video_bit_rate=4000000 "
        f"video_codec=h264 tunnel_forward=true control=true audio=false "
        f"scid={scid_hex}"
    )
    proc = await asyncio.create_subprocess_exec(
        "adb", "-s", device, "shell", inner,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )

    async def drain() -> list[str]:
        assert proc.stdout is not None
        lines: list[str] = []
        while True:
            line = await proc.stdout.readline()
            if not line:
                return lines
            lines.append(line.decode(errors="replace").rstrip())

    log_task = asyncio.create_task(drain())
    try:
        await wait_device_socket(device, sock_name, time.monotonic() + 20)
    except TimeoutError:
        log_task.cancel()
        raise
    return proc, log_task


async def group_a(device: str) -> dict:
    """accept 语义探测：断开后能否重连。"""
    scid = random.randint(1, 0x7FFFFFFF)
    sock_name = f"scrcpy_{scid:08x}"
    port = random.randint(28000, 28999)
    r = adb(device, "shell", f"ls {REMOTE_JAR}")
    if r.returncode != 0:
        log(f"A: 推送 JAR（{LOCAL_JAR.name}）…")
        adb(device, "push", str(LOCAL_JAR), REMOTE_JAR, timeout=90)
    adb(device, "forward", f"tcp:{port}", f"localabstract:{sock_name}")

    log(f"A: 起服 scid={scid:x} socket={sock_name} port={port}")
    proc, log_task = await start_server(device, f"{scid:x}", sock_name, port)
    deadline = time.monotonic() + 20

    # 第一次会话：video + control 两条连接（与生产一致），读握手字节
    vr, vw = await connect_retry(port, deadline)
    v_first = await asyncio.wait_for(vr.readexactly(1), timeout=5)
    log(f"A: 首会话 video 握手字节={v_first.hex()}")

    # 控制 socket：server 先发 1 dummy + 2 codec
    _, cw = await connect_retry(port, deadline)
    try:
        c_first = await asyncio.wait_for((await asyncio.open_connection("127.0.0.1", port))[0].readexactly(3), timeout=5)
        log(f"A: 首会话控制握手字节={c_first.hex()}")
    except Exception:
        pass  # 控制握手读到第 3 次连接；忽略细节，仅为验证 accept 多次

    # 断开全部客户端
    vw.close()
    cw.close()
    await vw.wait_closed()
    await cw.wait_closed()
    await asyncio.sleep(2.0)

    alive = server_alive(device)
    log(f"A: 客户端全断开 2s 后 server 进程存活={alive}")
    if not alive:
        if proc.returncode is None:
            proc.terminate()
        await log_task
        adb(device, "forward", "--remove", f"tcp:{port}")
        r = adb(device, "shell", f"ls -l {REMOTE_JAR}")
        jar_after = "gone" if r.returncode != 0 else r.stdout.strip()
        log(f"A: server 退出后主 JAR: {jar_after}")
        return {"result": "ACCEPT_ONCE(断连即退)", "server_died_on_disconnect": True,
                "jar_after": jar_after}

    # 再连：读得到字节 = accept 循环存活
    try:
        r2, w2 = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), timeout=5)
        try:
            b2 = await asyncio.wait_for(r2.readexactly(1), timeout=5)
            log(f"A: 重连握手字节={b2.hex()}")
            verdict = "ACCEPT_LOOP"
        except (asyncio.IncompleteReadError, Exception):
            verdict = "ACCEPT_ONCE(EOF)"
        w2.close()
        await w2.wait_closed()
    except OSError as e:
        verdict = f"RECONNECT_FAILED: {e}"

    # 收尾：杀 server → 观察 JAR 自删（E002 对 scid 变体是否成立）
    if proc.returncode is None:
        proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), timeout=5)
    except (asyncio.TimeoutError, ProcessLookupError):
        pass
    logs = await log_task
    adb(device, "forward", "--remove", f"tcp:{port}")
    await asyncio.sleep(1.0)
    r = adb(device, "shell", f"ls -l {REMOTE_JAR}")
    jar_after = "gone" if r.returncode != 0 else r.stdout.strip()
    log(f"A: server 退出后主 JAR: {jar_after}")
    for line in logs[-5:]:
        if line:
            log(f"A:   {line}")
    return {"result": verdict, "alive_after_disconnect": alive, "jar_after": jar_after}


async def group_b(device: str) -> dict:
    """JAR bak 链路耗时与恢复演练。"""
    r = adb(device, "shell", f"ls -l {REMOTE_JAR}")
    if r.returncode != 0:
        log("B: 推送 JAR…")
        push_r = subprocess.run(
            ["adb", "-s", device, "push", str(LOCAL_JAR), REMOTE_JAR],
            capture_output=True, text=True, timeout=90)
        log(f"B: push 输出: {push_r.stdout.strip().splitlines()[-1] if push_r.stdout.strip() else push_r.stderr.strip()}")
    local_size = LOCAL_JAR.stat().st_size

    results: dict = {}
    t0 = time.monotonic()
    adb(device, "shell", f"cp {REMOTE_JAR} {BAK_JAR}")
    results["cp_s"] = round(time.monotonic() - t0, 3)
    adb(device, "shell", f"rm {REMOTE_JAR}")  # 模拟 server 退出自删

    t0 = time.monotonic()
    adb(device, "shell", f"ls -l {BAK_JAR}")
    results["ls_s"] = round(time.monotonic() - t0, 3)
    ls_out = adb(device, "shell", f"ls -l {BAK_JAR}").stdout.strip()
    results["bak_ls"] = ls_out

    t0 = time.monotonic()
    adb(device, "shell", f"mv {BAK_JAR} {REMOTE_JAR}")
    results["mv_s"] = round(time.monotonic() - t0, 3)
    check = adb(device, "shell", f"ls -l {REMOTE_JAR}").stdout.strip()
    results["restored"] = check
    results["size_match"] = str(local_size) in check
    results["local_size"] = local_size
    return results


async def ws_once(backend: str, device: str, stress: bool) -> dict:
    """一次完整连接：WS 连接 → config → 首帧，计时。"""
    url = f"{backend.replace('http', 'ws')}/ws/video/{device}"
    # 后端日志锚（服务端视角分段）由服务端日志给出；客户端测三段时间点
    t_connect = time.monotonic()
    stress_procs: list[asyncio.subprocess.Process] = []
    if stress:
        for i in range(3):
            stress_procs.append(await asyncio.create_subprocess_exec(
                "adb", "-s", device, "shell",
                "for i in $(seq 1 30); do ls -l /sdcard >/dev/null 2>&1; done",
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            ))
    async with websockets.connect(url, max_size=16 * 1024 * 1024) as ws:
        t_ws = time.monotonic()
        t_config = t_frame = None
        while True:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=25)
            except asyncio.TimeoutError:
                break
            if isinstance(msg, bytes):
                # 媒体帧远大于握手/元数据包；小包不算首帧
                if t_config is not None and t_frame is None and len(msg) > 100:
                    t_frame = time.monotonic()
                    break
            else:
                try:
                    d = json.loads(msg)
                except json.JSONDecodeError:
                    continue
                if d.get("type") == "config":
                    t_config = time.monotonic()
    for p in stress_procs:
        if p.returncode is None:
            p.terminate()
    out = {"ws_s": round((t_ws - t_connect) * 1000)}
    if t_config:
        out["config_ms"] = round((t_config - t_connect) * 1000)
    if t_frame:
        out["first_frame_ms"] = round((t_frame - t_connect) * 1000)
    out["complete"] = t_config is not None and t_frame is not None
    return out


async def group_c(backend: str, device: str, n: int, stress: bool) -> dict:
    rows = []
    for i in range(n):
        row = await ws_once(backend, device, stress and (i % 2 == 1))
        log(f"C: 第 {i + 1} 轮 {row}")
        rows.append(row)
        await asyncio.sleep(1.0)
    complete = [r for r in rows if r.get("complete")]
    if not complete:
        return {"rows": rows}
    def med(key: str) -> float:
        vals = sorted(r[key] for r in complete if key in r)
        return vals[len(vals) // 2]
    return {
        "n_complete": len(complete),
        "config_ms_med": med("config_ms"),
        "first_frame_ms_med": med("first_frame_ms"),
        "first_frame_ms_p95": sorted(r["first_frame_ms"] for r in complete)[int(len(complete) * 0.95) - 1] if complete else None,
        "rows": rows,
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("device", help="ADB 序列号，如 192.168.8.18:5555")
    ap.add_argument("--backend", default="http://127.0.0.1:8765")
    ap.add_argument("--groups", default="A,B,C", help="逗号分隔，如 A,B 或 C")
    ap.add_argument("-n", type=int, default=10, help="C 组连接轮数")
    ap.add_argument("--stress", action="store_true", help="C 组奇数轮并发 adb 负载复现慢 push")
    args = ap.parse_args()
    groups = [g.strip() for g in args.groups.split(",")]

    if "A" in groups:
        log("A 组：accept 语义探测…")
        a = await group_a(args.device)
        log(f"A 组结果: {a}")
    if "B" in groups:
        log("B 组：JAR bak 链路…")
        b = await group_b(args.device)
        log(f"B 组结果: {b}")
    if "C" in groups:
        log(f"C 组：启动段基线 × {args.n}（stress={args.stress}）…")
        c = await group_c(args.backend, args.device, args.n, args.stress)
        summary = {k: v for k, v in c.items() if k != "rows"}
        log(f"C 组汇总: {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))