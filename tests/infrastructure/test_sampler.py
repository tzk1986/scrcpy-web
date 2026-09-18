"""
性能采样器测试（方案 17 实施项 3）
====================================

覆盖：
    - 去重：每轮恰好 4 次 shell 调用，activity 不再被 fps 采集重查
    - gfxinfo 降频：每 5 轮一次；中间轮沿用 _last_fps、jank=0
    - 失联自动停采：连续 MAX_FAILURES 次失败后 sample() 结束（不再产出）
    - 失败计数重置：失败后成功采样一轮，计数清零、不误停
    - shell 总超时兜底：挂起的 shell 被 wait_for 打断并抛 TimeoutError
"""

import asyncio

import pytest

from app.core.exceptions import AdbError
from app.infrastructure.performance.sampler import PerformanceSampler

GOOD_OUTPUTS = {
    "cat /proc/stat": "cpu  100 20 30 40 5 6 0 0 0 0\n",
    "cat /proc/meminfo": "MemTotal: 2048000 kB\nMemAvailable: 1024000 kB\n",
    "dumpsys activity activities": (
        "mResumedActivity: ActivityRecord{abc123 u0 com.example.app/.MainActivity t123}\n"
    ),
    "dumpsys gfxinfo": "Total frames rendered: 100\nJanky frames: 5\n",
}


class FakeAdb:
    """按命令前缀分发的假 ADB 驱动，记录每次 shell 调用。

    属性：
        calls: 全部 shell 命令历史。
        fail_calls: 前 N 次调用抛 AdbError（模拟设备失联），0 表示不失败。
        hang: True 时 shell 永不返回（模拟 dumpsys 挂起）。
    """

    def __init__(self, outputs: dict[str, str] | None = None):
        self.outputs = outputs or {}
        self.calls: list[str] = []
        self.fail_calls = 0
        self.hang = False

    async def shell(self, device_id: str, cmd: str) -> str:
        self.calls.append(cmd)
        if self.hang:
            await asyncio.Event().wait()  # 永不返回
        if len(self.calls) <= self.fail_calls:
            raise AdbError(f"ADB command failed: {cmd[:50]}")
        for prefix, out in self.outputs.items():
            if cmd.startswith(prefix):
                return out
        return ""


async def test_collect_once_uses_four_shell_calls():
    """去重后每轮恰好 4 次 shell：activity/cpu/mem + gfxinfo，activity 不重查。"""
    adb = FakeAdb(GOOD_OUTPUTS)
    sampler = PerformanceSampler(adb, "device-X")

    m = await sampler._collect_once()

    assert len(adb.calls) == 4
    activity_calls = [c for c in adb.calls if c.startswith("dumpsys activity")]
    assert len(activity_calls) == 1
    # gfxinfo 使用去重后拿到的 package，不再内部重新查 activity
    assert adb.calls[3] == "dumpsys gfxinfo com.example.app"
    assert m.top_package == "com.example.app"
    assert m.current_activity == "com.example.app/.MainActivity"
    assert m.cpu_percent == 0.0  # 首轮无 cpu 基准
    assert m.total_memory_mb == 2000.0
    assert m.used_memory_mb == 1000.0
    assert m.fps is None  # 首轮 gfx 无基准


async def test_gfxinfo_sampled_every_five_rounds():
    """gfxinfo 每 5 轮采集一次：6 轮中仅第 1、6 轮调用。"""
    adb = FakeAdb(GOOD_OUTPUTS)
    sampler = PerformanceSampler(adb, "device-X")

    results = [await sampler._collect_once() for _ in range(6)]

    gfx_calls = [c for c in adb.calls if c.startswith("dumpsys gfxinfo")]
    assert len(gfx_calls) == 2
    # 中间轮（轮 2-5）：fps 沿用 _last_fps（首轮无基准 → None），jank=0
    for m in results[1:5]:
        assert m.fps is None
        assert m.jank_count == 0


async def test_gfxinfo_off_rounds_reuse_last_fps():
    """降频轮复用上次有效 fps，且不再调用 gfxinfo。"""
    adb = FakeAdb(GOOD_OUTPUTS)
    sampler = PerformanceSampler(adb, "device-X")
    await sampler._collect_once()  # 轮 1：gfx 采集
    sampler._last_fps = 25.5  # 模拟已有有效 fps

    m = await sampler._collect_once()  # 轮 2：非 gfx 轮

    gfx_calls = [c for c in adb.calls if c.startswith("dumpsys gfxinfo")]
    assert len(gfx_calls) == 1  # 轮 2 未再调用
    assert m.fps == 25.5
    assert m.jank_count == 0


async def test_sampling_stops_after_max_failures():
    """连续 MAX_FAILURES 次采样失败后 sample() 结束、不再产出、不再调用 shell。"""
    adb = FakeAdb(GOOD_OUTPUTS)
    adb.fail_calls = 10**9  # 全部失败
    sampler = PerformanceSampler(adb, "device-X")

    results = [m async for m in sampler.sample(interval=0)]

    assert results == []
    # 每轮失败时 gather 的 3 个 shell（activity/cpu/mem）全部发出，
    # 串行的 gfxinfo 因 gather 已抛异常而不会执行
    assert len(adb.calls) == PerformanceSampler.MAX_FAILURES * 3


async def test_failure_counter_resets_after_success():
    """失败后一旦成功采样，失败计数重置：连续产出超过一次成功即证明未误停。"""
    adb = FakeAdb(GOOD_OUTPUTS)
    adb.fail_calls = 12  # 前 12 次调用失败 = 4 轮（每轮 3 个并发调用）
    sampler = PerformanceSampler(adb, "device-X")

    results = []
    async for m in sampler.sample(interval=0):
        results.append(m)
        if len(results) >= 3:
            break  # 若计数未重置，第 5 轮前就会停止，拿不到 3 个结果

    assert len(results) == 3


async def test_shell_timeout_raises(monkeypatch):
    """挂起的 shell 被总超时兜底打断，抛出 TimeoutError。"""
    adb = FakeAdb(GOOD_OUTPUTS)
    adb.hang = True
    sampler = PerformanceSampler(adb, "device-X")
    monkeypatch.setattr(PerformanceSampler, "SHELL_TIMEOUT", 0.05)

    with pytest.raises(asyncio.TimeoutError):
        await sampler._collect_once()