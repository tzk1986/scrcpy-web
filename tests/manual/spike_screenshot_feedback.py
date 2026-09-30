#!/usr/bin/env python3
"""方案 27 Phase 0 spike：截图模式操作反馈基线（需真机 + 后端在跑）。

测量三组：
  A. 后端截屏耗时分布：direct `adb exec-out screencap -p`（= 后端
     cli.py 同命令，T2+T3）vs HTTP GET /api/devices/{id}/screenshot
     （T2+T3+T4，差值即接口层开销）
  B. 旧基线端到端反馈（模拟现状前端 = 1500ms 固定相位轮询）：
     注入通知栏下拉/收起 → 轮询截图 → PNG 大小突变检出 → 反馈时延
  C. --event-driven 新管线变体（Phase 2 复测用，方案 27 D2/D4 语义）：
     注入后 debounce 160ms 主动截一张 → 未检出则按 1500ms 轮询接续
     （相位重置）

画面变化检出法（无 Pillow 依赖）：通知栏展开/收起使 PNG 体积突变
（全屏状态栏差异数百 KB 量级），相邻帧体积比 ≥2.5× 或 ≤0.4× 判为
画面变化。B/C 同法比较，相对改善不受检出灵敏度影响。

用法：
    PYTHONPATH=. python -u tests/manual/spike_screenshot_feedback.py \
        192.168.8.18:5555 --backend http://127.0.0.1:8765 [--event-driven]

前提：后端已启动；设备已 adb connect；设备屏幕朝向任意（注入坐标
按当前分辨率居中计算，通知栏下拉为屏幕原生手势，横竖屏均生效）。
"""

import argparse
import asyncio
import json
import statistics
import sys
import time
import urllib.parse
from datetime import datetime

import httpx
import websockets

# 通知栏下拉手势参数（设备坐标，居中下拉 1/3 屏高——屏幕原生手势）
SWIPE_FRACTION = 0.4
SWIPE_DURATION_MS = 250
DEBOUNCE_MS = 160
POLL_MS = 1500
MUTATE_RATIO_UP = 1.15
MUTATE_RATIO_DOWN = 0.87


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] {msg}", flush=True)


def mutated(size: int, base: int) -> bool:
    if base <= 0:
        return True
    r = size / base
    return r >= MUTATE_RATIO_UP or r <= MUTATE_RATIO_DOWN


async def screenshot_direct(device_id: str) -> tuple[int, int]:
    """直接 subprocess 执行 adb exec-out screencap -p（= 后端同命令）。
    返回 (字节数, 耗时 ms)。"""
    t0 = time.perf_counter()
    proc = await asyncio.create_subprocess_exec(
        "adb", "-s", device_id, "exec-out", "screencap", "-p",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    dt = (time.perf_counter() - t0) * 1000
    if proc.returncode != 0:
        raise RuntimeError(f"screencap failed: {stderr.decode(errors='replace')}")
    return len(stdout), dt


async def screenshot_http(client: httpx.AsyncClient, device_id: str) -> tuple[int, int]:
    t0 = time.perf_counter()
    r = await client.get(
        f"/api/devices/{urllib.parse.quote(device_id, safe='')}/screenshot", timeout=15)
    dt = (time.perf_counter() - t0) * 1000
    if r.status_code != 200:
        raise RuntimeError(f"screenshot HTTP {r.status_code}")
    return len(r.content), dt


def dist(vals: list[float]) -> str:
    s = sorted(vals)
    def q(p: float) -> float:
        return s[min(len(s) - 1, int(p * len(s)))]
    return (f"n={len(s)} 中位={statistics.median(s):.0f} 均值={statistics.fmean(s):.0f} "
            f"p95={q(0.95):.0f} 最大={max(s):.0f} 最小={min(s):.0f}")


class FeedbackHarness:
    def __init__(self, device_id: str, backend: str, event_driven: bool):
        self.device_id = device_id
        self.event_driven = event_driven
        self.http = httpx.AsyncClient(base_url=backend)
        self.ws = None
        self.frames: asyncio.Queue[tuple[float, int]] = asyncio.Queue()
        self.poller_task = None
        self._running = False

    async def connect(self) -> tuple[int, int]:
        """连接 WS（触发后端视频流启动，与截图模式实态一致——回退时
        WS 仍连接、流仍在推）并取设备分辨率用于手势坐标。"""
        quoted = urllib.parse.quote(self.device_id, safe='')
        self.ws = await websockets.connect(f"ws://127.0.0.1:8765/ws/video/{quoted}",
                                           max_size=10 * 1024 * 1024)
        self._incoming: asyncio.Queue = asyncio.Queue()
        self._recv_task = asyncio.create_task(self._drain_ws())
        # 分辨率来自 drain 队列中的 config 消息
        width = height = 0
        for _ in range(200):
            try:
                raw = await asyncio.wait_for(self._incoming.get(), timeout=10)
            except asyncio.TimeoutError:
                break
            if not isinstance(raw, str):
                continue
            msg = json.loads(raw)
            if msg.get("type") == "config":
                width, height = msg.get("width", 0), msg.get("height", 0)
                if width and height:
                    break
        if not width:
            # config 兜底：直接问 adb
            proc = await asyncio.create_subprocess_exec(
                "adb", "-s", self.device_id, "shell", "wm", "size",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            out, _ = await proc.communicate()
            m = out.decode(errors="replace").strip()
            # 形如 Physical size: 1360x768
            w, h = m.rsplit(":", 1)[1].strip().lower().split("x")
            width, height = int(w), int(h)
        log(f"设备分辨率 {width}x{height}")
        return width, height

    async def _drain_ws(self) -> None:
        try:
            async for raw in self.ws:
                self._incoming.put_nowait(raw)
        except Exception:
            pass

    async def inject(self, action: dict) -> None:
        await self.ws.send(json.dumps(action))

    async def swipe_down(self, w: int, h: int) -> None:
        x = w // 2
        await self.inject({
            "action": "swipe",
            "x1": x, "y1": 5, "x2": x,
            "y2": max(10, int(h * SWIPE_FRACTION)),
            "duration": SWIPE_DURATION_MS,
        })

    async def press_back(self) -> None:
        await self.inject({"action": "key", "keycode": 4})

    async def start_poller(self) -> None:
        """1500ms 固定相位轮询（B 基线）；新帧入队 (完成时刻, 字节数)。"""
        self._running = True
        self.poller_task = asyncio.create_task(self._poll_loop())

    async def _poll_loop(self) -> None:
        while self._running:
            try:
                _, size = await asyncio.wait_for(
                    self._take_shot(), timeout=20)
                self.frames.put_nowait((time.perf_counter(), size))
            except Exception as e:
                log(f"轮询异常: {e}")
            await asyncio.sleep(POLL_MS / 1000)

    async def _take_shot(self) -> tuple[float, int]:
        size, _dt = await screenshot_http(self.http, self.device_id)
        return time.perf_counter(), size

    async def wait_mutation(self, base: int, timeout_s: float, t0: float) -> float | None:
        """等待队列首帧相对 base 突变；返回时延(s)或超时 None。"""
        deadline = time.perf_counter() + timeout_s
        while time.perf_counter() < deadline:
            remain = deadline - time.perf_counter()
            try:
                t, size = await asyncio.wait_for(self.frames.get(), timeout=remain)
            except asyncio.TimeoutError:
                return None
            if mutated(size, base):
                return t - t0
        return None

    async def wait_restore(self, base: int, timeout_s: float) -> bool:
        """等待画面回到 base 附近（±12% 容忍带），用于收起后复位确认。
        与突变阈值 1.15× 不相交，避免把「仍展开态」的帧误判为已复位。"""
        deadline = time.perf_counter() + timeout_s
        while time.perf_counter() < deadline:
            remain = deadline - time.perf_counter()
            try:
                _, size = await asyncio.wait_for(self.frames.get(), timeout=remain)
            except asyncio.TimeoutError:
                return False
            if abs(size - base) / base <= 0.12:
                return True
        return False

    async def clear_frames(self) -> None:
        while not self.frames.empty():
            try:
                self.frames.get_nowait()
            except asyncio.QueueEmpty:
                break

    async def baseline_round(self, w: int, h: int) -> float | None:
        """B 组单轮：下拉通知栏 → 固定相位轮询检出 → back 收起。
        返回反馈时延(ms)。"""
        await self.clear_frames()
        # 取注入前基线尺寸：等首帧入队
        try:
            b_t, b_size = await asyncio.wait_for(self.frames.get(), timeout=5)
        except asyncio.TimeoutError:
            log("B 轮超时：无轮询帧")
            return None
        t0 = time.perf_counter()
        await self.swipe_down(w, h)
        d1 = await self.wait_mutation(b_size, 5.0, t0)
        if d1 is None:
            log("B 轮超时：下拉未检出变化")
            await self.press_back()
            return None
        # 收起（back），等待画面回到原尺寸容忍带（±12%）
        await asyncio.sleep(0.3)
        await self.press_back()
        ok = await self.wait_restore(b_size, 5.0)
        if not ok:
            log("B 轮：收起未复位（下轮基线可能偏移）")
        return d1 * 1000

    async def eventdriven_round(self, w: int, h: int) -> float | None:
        """C 组单轮（--event-driven，模拟方案 27 D2/D3/D4 语义）：
        注入 → 160ms debounce → 主动截一张（单飞，无并发轮询干扰——
        真实新前端 in-flight 期间跳过其它请求）→ 未检出则按 1500ms
        轮询接续（相位重置）。返回反馈时延(ms)。"""
        _, base_size = await self._take_shot()
        t0 = time.perf_counter()
        await self.swipe_down(w, h)
        await asyncio.sleep(DEBOUNCE_MS / 1000)
        t, size = await self._take_shot()          # 事件驱动帧
        d: float | None = None
        if mutated(size, base_size):
            d = (t - t0) * 1000
        else:
            # 事件帧未检出（UI 响应慢）→ 相位重置后按 1500ms 接续
            for _ in range(2):
                await asyncio.sleep(POLL_MS / 1000)
                t, size = await self._take_shot()
                if mutated(size, base_size):
                    d = (t - t0) * 1000
                    break
        await asyncio.sleep(0.3)
        await self.press_back()
        await asyncio.sleep(1.5)  # 收起动画完成
        return d


async def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="方案 27 Phase 0 spike：截图模式反馈基线")
    ap.add_argument("device", help="ADB 序列号，如 192.168.8.18:5555")
    ap.add_argument("--backend", default="http://127.0.0.1:8765")
    ap.add_argument("--event-driven", action="store_true", help="C 组事件驱动变体")
    ap.add_argument("--n", type=int, default=None, help="每圈注入次数（默认 B10/C10）")
    args = ap.parse_args()

    h = FeedbackHarness(args.device, args.backend, args.event_driven)
    w, hgt = await h.connect()
    log("WS 已连接（后端视频流已启动）")

    # A 组：后端截屏耗时分布
    n_a = args.n or 20
    direct_ms, http_ms = [], []
    log(f"A 组：direct adb screencap × {n_a} …")
    for i in range(n_a):
        size, dt = await screenshot_direct(args.device)
        direct_ms.append(dt)
    log(f"A 组：HTTP screenshot × {n_a} …")
    for i in range(n_a):
        size, dt = await screenshot_http(h.http, args.device)
        http_ms.append(dt)
    log(f"A/direct: {dist(direct_ms)}")
    log(f"A/http:   {dist(http_ms)}")
    gap = statistics.median(http_ms) - statistics.median(direct_ms)
    log(f"A/接口层开销(HTTP中位-direct中位): {gap:.0f} ms")

    # B 或 C 组：端到端反馈
    rounds = args.n or 10
    n_ok = 0
    results: list[float] = []
    label = "C/事件驱动" if args.event_driven else "B/旧基线(1500ms轮询)"
    if not args.event_driven:
        await h.start_poller()
        await asyncio.sleep(2.0)  # 轮询相位稳定
    log(f"{label}：× {rounds} 轮（通知栏下拉/收起交替）…")
    for i in range(rounds):
        if args.event_driven:
            d = await h.eventdriven_round(w, hgt)
        else:
            d = await h.baseline_round(w, hgt)
        if d is not None:
            results.append(d)
            n_ok += 1
            log(f"  第 {i + 1} 轮反馈时延: {d:.0f} ms")
        else:
            log(f"  第 {i + 1} 轮：未检出（跳过）")
        await asyncio.sleep(0.8)

    if results:
        log(f"{label} 汇总: {dist(results)}")
    else:
        log("无有效样本！检查设备画面/注入坐标/后端日志")
    print()
    if not args.event_driven:
        log("提示：跑完 B 基线后加 --event-driven 复测 C 组（Phase 2 判定）")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        raise SystemExit(130)