"""
性能采样器采样链 additive 测试（方案 37 D3）
==============================================

覆盖：
    - _collect_once 五路 gather：进程 CPU 明细 + 电池四字段落入 PerformanceMetrics
    - 两新命令失败（AdbError / 超时）→ 默认值降级，整次采样不毁、不抛
    - 旧替身仅实现 shell（无新方法）→ AttributeError 同样降级
    - BatteryInfo.health=None → 采集层归一为 "UNKNOWN"
    - PerformanceMetrics 旧构造兼容（新字段带默认值）
"""

import asyncio

from app.core.exceptions import AdbError
from app.domain.runtime import BatteryInfo, CpuProcessInfo
from app.infrastructure.performance.sampler import PerformanceMetrics, PerformanceSampler

# FakeAdb 的固定 shell 响应
PROC_STAT = "cpu  100 10 5 200 0 0 0 0 0 0\n"
MEMINFO = "MemTotal: 4096000 kB\nMemAvailable: 2048000 kB\n"
ACTIVITY = "mResumedActivity: ActivityRecord{a1b2c3 u0 com.example.app/.MainActivity t42}\n"
GFXINFO = "Total frames rendered: 100\nJanky frames: 5\n"


def shell_respond(cmd: str) -> str:
    """按命令前缀返回固定输出（两假 ADB 共用）。"""
    if cmd.startswith("cat /proc/stat"):
        return PROC_STAT
    if cmd.startswith("cat /proc/meminfo"):
        return MEMINFO
    if cmd.startswith("dumpsys activity"):
        return ACTIVITY
    if cmd.startswith("dumpsys gfxinfo"):
        return GFXINFO
    return ""


class FakeAdb:
    """假 ADB：shell 按命令返回固定输出；两新命令可注入异常或定制返回值。"""

    def __init__(
        self,
        cpu_exc: BaseException | None = None,
        batt_exc: BaseException | None = None,
        batt: BatteryInfo | None = None,
    ) -> None:
        self.cpu_exc = cpu_exc
        self.batt_exc = batt_exc
        self.batt = batt
        self.calls: list[str] = []

    async def shell(self, device_id: str, cmd: str) -> str:
        return shell_respond(cmd)

    async def get_cpu_usage(self, device_id: str) -> list[CpuProcessInfo]:
        self.calls.append("cpu")
        if self.cpu_exc is not None:
            raise self.cpu_exc
        return [CpuProcessInfo("kworker/0:1", 1.2, 0.3)]

    async def get_battery_info(self, device_id: str) -> BatteryInfo:
        self.calls.append("batt")
        if self.batt_exc is not None:
            raise self.batt_exc
        return self.batt if self.batt is not None else BatteryInfo(
            level=80, temperature=31.0, voltage=4200, health="GOOD"
        )


class ShellOnlyAdb:
    """方案 17/18 时代的旧替身形态：仅实现 shell（两新方法缺失）。"""

    async def shell(self, device_id: str, cmd: str) -> str:
        return shell_respond(cmd)


async def test_collect_once_fills_cpu_processes_and_battery():
    """五路 gather：进程 CPU 明细与电池四字段随一次采集落入 metrics。"""
    adb = FakeAdb()
    sampler = PerformanceSampler(adb, "dev-1")

    m = await sampler._collect_once()

    assert m.cpu_processes == [CpuProcessInfo("kworker/0:1", 1.2, 0.3)]
    assert m.battery_temp == 31.0
    assert m.battery_voltage == 4200
    assert m.battery_health == "GOOD"
    # 既有三命令结果不受影响
    assert m.total_memory_mb == 4000.0
    assert m.used_memory_mb == 2000.0
    assert m.current_activity == "com.example.app/.MainActivity"
    assert m.top_package == "com.example.app"
    # 两新命令确在同轮被驱动
    assert sorted(adb.calls) == ["batt", "cpu"]


async def test_collect_once_new_commands_fail_degrade_to_defaults():
    """新命令失败（AdbError/超时）→ 默认值降级，整次采样不毁、不抛。"""
    adb = FakeAdb(cpu_exc=AdbError("ADB command failed"), batt_exc=asyncio.TimeoutError())
    sampler = PerformanceSampler(adb, "dev-1")

    m = await sampler._collect_once()  # 不抛

    assert m.cpu_processes == []
    assert m.battery_temp is None
    assert m.battery_voltage is None
    assert m.battery_health == "UNKNOWN"
    assert m.total_memory_mb == 4000.0
    assert m.used_memory_mb == 2000.0
    assert m.top_package == "com.example.app"


async def test_collect_once_missing_new_methods_degrade():
    """旧替身（仅 shell、无新方法）→ AttributeError 同样降级为默认值。"""
    sampler = PerformanceSampler(ShellOnlyAdb(), "dev-1")

    m = await sampler._collect_once()

    assert m.cpu_processes == []
    assert m.battery_temp is None
    assert m.battery_voltage is None
    assert m.battery_health == "UNKNOWN"


async def test_collect_once_normalizes_health_none():
    """BatteryInfo.health=None → 采集层归一为 "UNKNOWN"。"""
    adb = FakeAdb(batt=BatteryInfo(level=80, temperature=None, voltage=None, health=None))
    sampler = PerformanceSampler(adb, "dev-1")

    m = await sampler._collect_once()

    assert m.battery_temp is None
    assert m.battery_voltage is None
    assert m.battery_health == "UNKNOWN"


def test_metrics_old_construction_compatible():
    """旧构造（不带新字段 kwarg）不炸：新字段落到工厂/标量默认值。"""
    m = PerformanceMetrics(
        ts=1.0, cpu_percent=0.0, total_memory_mb=4096.0, used_memory_mb=2048.0,
        fps=None, jank_count=0, current_activity="", top_package="",
    )
    assert m.cpu_processes == []
    assert m.battery_temp is None
    assert m.battery_voltage is None
    assert m.battery_health == "UNKNOWN"