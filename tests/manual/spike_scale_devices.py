# tests/manual/spike_scale_devices.py
"""路线图第 10 项「100+ 设备规模化」模拟验证 spike（不入 CI）。

口径：mock adb 输出 + 压测脚本，先量化再动手；N=50 无瓶颈则如实归档
「规模上限证据」，不做预防性重构。

做法：对 AdbCliDriver._run / screenshot / screenshot_raw_gzip 打桩
（模拟真实 adb 子进程耗时 40-120ms/次），其余全部走**真实代码路径**：
DeviceService 5s 刷新循环（首周期 N 台新设备串行富化）、GET /api/devices
（方案 35 并发 8）、SSE /events 上下线事件、SQLite 落库、截图端点。
指标写 logs/spike_scale_N{N}.json。

用法（分两轮 N=50/100）：
  PYTHONPATH=backend python tests/manual/spike_scale_devices.py --n 50 --port 8766
  PYTHONPATH=backend python tests/manual/spike_scale_devices.py --n 100 --port 8766
"""
import argparse
import asyncio
import ctypes
import json
import os
import random
import sqlite3
import statistics
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "backend"))
sys.path.insert(0, _ROOT)

# 隔离 DB（logs/ 已 gitignore），必须在导入 app 前设置
N = 0  # 由 main 填充后供 _run 桩使用
PNG_BYTES = b""


def _serial(i: int) -> str:
    return f"10.66.{i // 254}.{i % 254 + 1}:5555"


def rss_mb() -> float:
    from ctypes import wintypes

    psapi = ctypes.WinDLL("psapi")
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL

    class PMC(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    pmc = PMC()
    pmc.cb = ctypes.sizeof(pmc)
    psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb)
    return pmc.WorkingSetSize / 1e6


def patch_adb() -> None:
    from app.infrastructure.adb.cli import AdbCliDriver

    async def fake_run(self, *args, timeout=None, decode_errors="strict"):  # noqa: ANN001
        await asyncio.sleep(random.uniform(0.04, 0.12))
        joined = " ".join(args)
        if args and args[0] == "devices":
            rows = "\n".join(f"{_serial(i)}\tdevice" for i in range(N))
            return f"List of devices attached\n{rows}"
        if "getprop ro.product.model" in joined:
            return "MOCKMODEL"
        if "getprop ro.build.version.release" in joined:
            return "13"
        if "wm size" in joined:
            return "Physical size: 1080x1920"
        if "dumpsys battery" in joined:
            return "Current Battery Service state:\n  level: 88"
        if "dumpsys display" in joined:
            return "mCurrentOrientation=0\nmPrimaryDisplayDeviceBuiltIn=true"
        if "get-state" in joined:
            return "device"
        return ""

    async def fake_screenshot(self, device_id):  # noqa: ANN001
        await asyncio.sleep(random.uniform(0.15, 0.45))
        return PNG_BYTES

    async def fake_screenshot_raw(self, device_id):  # noqa: ANN001
        await asyncio.sleep(random.uniform(0.15, 0.45))
        return PNG_BYTES

    AdbCliDriver._run = fake_run
    AdbCliDriver.screenshot = fake_screenshot
    AdbCliDriver.screenshot_raw_gzip = fake_screenshot_raw


async def heartbeat(drifts: list[float], stop: asyncio.Event) -> None:
    next_tick = time.monotonic() + 0.1
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=max(0.0, next_tick - time.monotonic()))
        except asyncio.TimeoutError:
            pass
        drifts.append(time.monotonic() - next_tick)
        next_tick += 0.1


def db_count(db_path: str) -> int:
    try:
        con = sqlite3.connect(db_path, timeout=2)
    except sqlite3.OperationalError:
        return -1
    try:
        return con.execute("SELECT COUNT(*) FROM devices").fetchone()[0]
    except sqlite3.OperationalError:
        return -1
    finally:
        con.close()


async def main() -> None:
    global N, PNG_BYTES
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--steady", type=float, default=30.0)
    args = ap.parse_args()
    N = args.n
    db_path = f"logs/spike_scale_N{N}.sqlite"
    os.environ["DB_PATH"] = db_path
    if os.path.exists(db_path):
        os.remove(db_path)

    from PIL import Image
    import io
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (10, 20, 30)).save(buf, "PNG")
    PNG_BYTES = buf.getvalue()

    patch_adb()

    import httpx
    import uvicorn
    from app.main import app

    cfg = uvicorn.Config(app, host="127.0.0.1", port=args.port,
                         log_level="warning", access_log=False)
    server = uvicorn.Server(cfg)
    t_start = time.monotonic()
    serve_task = asyncio.create_task(server.serve())
    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{args.port}") as client:
        for _ in range(200):
            try:
                r = await client.get("/health", timeout=1)
                if r.status_code == 200:
                    break
            except Exception:
                pass
            await asyncio.sleep(0.1)

        drifts: list[float] = []
        stop = asyncio.Event()
        hb = asyncio.create_task(heartbeat(drifts, stop))

        # SSE 挂上，记录 connected 事件到达（事件在 reader 挂上后才可见）
        sse_times: list[float] = []
        sse_diag: dict = {"status": None, "chunks": 0, "sample": None}

        async def sse_reader() -> None:
            async with client.stream("GET", "/api/devices/events", timeout=None) as resp:
                sse_diag["status"] = resp.status_code
                async for chunk in resp.aiter_raw():
                    sse_diag["chunks"] += 1
                    if sse_diag["sample"] is None:
                        sse_diag["sample"] = repr(chunk[:200])
                    for line in chunk.split(b"\n"):
                        if line.startswith(b"data:") and b'"connected"' in line:
                            sse_times.append(time.monotonic() - t_start)

        sse_task = asyncio.create_task(sse_reader())

        # Phase C 提前：仪表盘首屏 GET 两次（第二次记 slow 计数——
        # 若首查失败走冷却兜底会体现在 slow 上）
        t = time.monotonic()
        r0 = await client.get("/api/devices")
        first_get_ms = (time.monotonic() - t) * 1000
        t = time.monotonic()
        r1 = await client.get("/api/devices")
        second_get_ms = (time.monotonic() - t) * 1000
        slow_count = sum(1 for d in r1.json() if d.get("slow")) if r1.status_code == 200 else None

        # Phase A：首刷新周期完成（repo 内设备数到 N）
        count = -1
        t_a = None
        while time.monotonic() - t_start < 180:
            count = db_count(db_path)
            if count >= N:
                t_a = time.monotonic() - t_start
                break
            await asyncio.sleep(0.25)

        a_drift_p99 = statistics.quantiles(drifts, n=100)[98] if len(drifts) > 10 else None
        rss_a = rss_mb()

        # Phase A2：SSE 上线事件突发——重置刷新循环的已见状态表，
        # 让下一周期把 N 台全部当作新设备重发 connected（N 台集中上线的量化）
        sse_times.clear()
        from app.deps import get_device_service
        get_device_service()._previous_states = {}  # noqa: SLF001（spike 专用）
        t_churn = time.monotonic()
        await asyncio.sleep(15.0)
        churn_drift_p99 = (statistics.quantiles(drifts[-150:], n=100)[98]
                           if len(drifts) > 10 else None)

        # Phase B：稳态
        t_b = time.monotonic()
        rss_samples: list[float] = []
        while time.monotonic() - t_b < args.steady:
            await asyncio.sleep(5)
            rss_samples.append(rss_mb())
        warm_lat: list[float] = []
        for _ in range(3):
            t = time.monotonic()
            await client.get("/api/devices")
            warm_lat.append((time.monotonic() - t) * 1000)

        # Phase D：缩略图轮询风暴（全部设备各 1 次截图，并发 16）
        serials = [_serial(i) for i in range(N)]
        sem = asyncio.Semaphore(16)

        async def one_shot(s: str) -> float:
            async with sem:
                t = time.monotonic()
                r = await client.get(f"/api/devices/{s}/screenshot")
                dt = (time.monotonic() - t) * 1000
                return dt if r.status_code == 200 else float("nan")

        t_d = time.monotonic()
        shots = await asyncio.gather(*(one_shot(s) for s in serials))
        wall_shot = time.monotonic() - t_d
        ok_shots = [x for x in shots if x == x]

        stop.set()
        await hb
        sse_task.cancel()
        try:
            await sse_task
        except asyncio.CancelledError:
            pass

    result = {
        "n": N,
        "db_path": db_path,
        "phaseA_first_refresh_wall_s": round(t_a, 1) if t_a else None,
        "phaseA_timeout_180s": t_a is None,
        "first_get_devices_ms_cold": round(first_get_ms, 1),
        "second_get_devices_ms": round(second_get_ms, 1),
        "second_get_slow_count": slow_count,
        "warm_get_devices_ms": [round(x, 1) for x in warm_lat],
        "steady_rss_mb": [round(x, 1) for x in rss_samples],
        "rss_after_A_mb": round(rss_a, 1),
        "loop_drift_p99_s_phaseA": round(a_drift_p99, 3) if a_drift_p99 else None,
        "loop_drift_p99_s_churn": round(churn_drift_p99, 3) if churn_drift_p99 else None,
        "sse_connected_events_seen": len(sse_times),
        "sse_connected_spread_s": (round(sse_times[-1] - sse_times[0], 1) if len(sse_times) > 1 else None),
        "sse_diag": sse_diag,
        "shots_ok": len(ok_shots),
        "shots_p50_ms": round(statistics.median(ok_shots), 0) if ok_shots else None,
        "shots_p95_ms": round(statistics.quantiles(ok_shots, n=100)[94], 0) if len(ok_shots) > 20 else None,
        "shots_wall_s": round(wall_shot, 1),
        "n_http_list": len(r1.json()) if r1.status_code == 200 else None,
    }
    out = f"logs/spike_scale_N{N}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)

    # 结果已落盘：尽力优雅停机，超时即硬退（SSE 等长连接不参与等待）
    server.should_exit = True
    try:
        await asyncio.wait_for(serve_task, timeout=10)
    except (asyncio.TimeoutError, Exception):
        pass
    os._exit(0)


asyncio.run(main())
