"""
性能监控服务测试（方案 17 实施项 3 + 方案 18 Step 2）
====================================================

覆盖：
    - 设备失联时采样连续失败 → 监控任务自动结束并清理 _tasks/_samplers
    - 订阅者收到 None 停止信号，stream_metrics 正常退出
    - 失联/停止后暂存缓冲释放（方案 18 B1 验收：停止后缓冲为空）
    - stop_monitoring 幂等；buffer_capacity 来自配置
    - 空闲守卫：订阅断开 → idle_ttl 后复查停采；订阅取消守卫；
      录制中禁止空闲停采（守卫继续观察，录制结束后停采）
    - cleanup 全设备停采释放
"""

import asyncio
import time
from collections import deque

import pytest

from app.application.performance_service import PerformanceService
from app.core.config import settings
from app.core.exceptions import AdbError


class DeadAdb:
    """所有 shell 都失败的假 ADB 驱动（模拟设备失联）。"""

    def __init__(self):
        self.calls = 0

    async def shell(self, device_id: str, cmd: str) -> str:
        self.calls += 1
        raise AdbError("ADB command failed: device offline")


async def test_monitoring_stops_and_notifies_subscribers_when_unreachable():
    service = PerformanceService(DeadAdb())
    await service.start_monitoring("device-X", interval=0)

    received: list[object] = []

    async def consume() -> None:
        async for m in service.stream_metrics("device-X"):
            received.append(m)

    consumer = asyncio.create_task(consume())

    # 等采样任务完成 MAX_FAILURES 次连续失败并自动退出
    for _ in range(500):
        if "device-X" not in service._tasks and consumer.done():
            break
        await asyncio.sleep(0.01)

    assert "device-X" not in service._tasks  # 任务已自动清理
    assert "device-X" not in service._samplers
    assert "device-X" not in service._buffers  # 失联同样释放暂存缓冲（B1）
    assert received == []  # 无任何度量产出
    assert consumer.done()  # 订阅者收到 None 后正常结束


async def test_stop_monitoring_releases_buffer_and_is_idempotent():
    """stop 释放暂存缓冲并幂等；未监控设备重复调用不抛异常。"""
    service = PerformanceService(DeadAdb())

    await service.stop_monitoring("device-X")
    await service.stop_monitoring("device-X")

    service._buffers["device-X"] = deque(maxlen=10)
    await service.stop_monitoring("device-X")
    assert "device-X" not in service._buffers


def test_buffer_capacity_reads_from_config():
    """缓冲容量来自 settings().metrics.buffer_size（S3），不再硬编码。"""
    service = PerformanceService(DeadAdb())

    assert service.buffer_capacity == settings().metrics.buffer_size
    assert service.buffer_capacity == 3600


# ---------------------------------------------------------------------------
# 空闲守卫（方案 18 Step 2）
# ---------------------------------------------------------------------------

@pytest.fixture
def guarded(monkeypatch: pytest.MonkeyPatch) -> None:
    """空闲守卫快速配置：ttl 0.05s。"""
    monkeypatch.setattr(settings().metrics, "idle_ttl_seconds", 0.05)


def place_holder_running_device(service: PerformanceService, device_id: str) -> None:
    """用挂起占位任务模拟「采样进行中」（守卫测试不依赖真采样器）。"""
    service._tasks[device_id] = asyncio.create_task(asyncio.sleep(30))
    service._buffers[device_id] = deque(maxlen=1)
    service._subscribers[device_id] = []


async def test_guard_stops_monitoring_after_idle(guarded) -> None:
    """无订阅且无录制 → idle_ttl 后停采并释放缓冲。"""
    service = PerformanceService(DeadAdb())
    place_holder_running_device(service, "device-X")
    service._schedule_idle_guard("device-X")

    await asyncio.sleep(0.2)

    assert "device-X" not in service._tasks
    assert "device-X" not in service._buffers
    assert "device-X" not in service._idle_guards


async def test_guard_skips_when_subscriber_present(guarded) -> None:
    """有订阅者时守卫不触发停采。"""
    service = PerformanceService(DeadAdb())
    place_holder_running_device(service, "device-X")
    service._subscribers["device-X"] = [asyncio.Queue()]
    service._schedule_idle_guard("device-X")

    await asyncio.sleep(0.2)

    assert "device-X" in service._tasks
    service._subscribers["device-X"].clear()  # 清理替身，避免 pending 告警


async def test_guard_recording_inhibits_stop_then_resumes(guarded) -> None:
    """录制中禁止空闲停采；录制结束后守卫继续观察并停采。"""
    service = PerformanceService(DeadAdb())
    place_holder_running_device(service, "device-X")
    service._recorders["device-X"] = object()
    service._schedule_idle_guard("device-X")

    await asyncio.sleep(0.2)
    assert "device-X" in service._tasks  # 录制中未停采

    del service._recorders["device-X"]
    await asyncio.sleep(0.2)
    assert "device-X" not in service._tasks


async def test_subscribe_cancels_guard_and_disconnect_schedules(guarded) -> None:
    """流订阅到来取消守卫；订阅断开后安排守卫并最终停采。"""
    service = PerformanceService(DeadAdb())
    place_holder_running_device(service, "device-X")
    service._schedule_idle_guard("device-X")
    await asyncio.sleep(0.01)
    assert "device-X" in service._idle_guards  # 守卫已挂起

    async def first_item(gen) -> None:
        try:
            await anext(gen)
        except StopAsyncIteration:
            pass

    gen = service.stream_metrics("device-X")
    consumer = asyncio.create_task(first_item(gen))
    await asyncio.sleep(0.01)  # 订阅注册（stream_metrics 执行到 queue.get()）
    assert "device-X" not in service._idle_guards  # 订阅已取消守卫

    # 模拟 WS 断开：取消消费任务，CancelledError 穿入 generator 触发 finally
    consumer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumer

    await asyncio.sleep(0.01)  # finally 已执行：订阅清空 → 守卫重新安排
    assert "device-X" in service._idle_guards

    await asyncio.sleep(0.2)
    assert "device-X" not in service._tasks  # 守卫复查后停采
    assert "device-X" not in service._buffers


async def test_cleanup_stops_all_devices() -> None:
    """cleanup 清理所有设备任务与缓冲（服务关闭路径）。"""
    service = PerformanceService(DeadAdb())
    for did in ("device-X", "device-Y"):
        place_holder_running_device(service, did)

    await service.cleanup()

    assert service._tasks == {}
    assert service._buffers == {}
    assert service._samplers == {}


# ---------------------------------------------------------------------------
# 告警挂点（方案 24 T2）
# ---------------------------------------------------------------------------

class HighCpuAdb:
    """CPU 恒 100%（user 递增、idle 不动）、内存 50% 的假 ADB。"""

    def __init__(self) -> None:
        self._user = 100
        self.calls: list[str] = []

    async def shell(self, device_id: str, cmd: str) -> str:
        self.calls.append(cmd)
        if cmd.startswith("cat /proc/stat"):
            self._user += 100
            return f"cpu  {self._user} 0 0 100 0 0 0 0 0 0\n"
        if cmd.startswith("cat /proc/meminfo"):
            return "MemTotal: 2048000 kB\nMemAvailable: 1024000 kB\n"
        if cmd.startswith("dumpsys activity"):
            return "mResumedActivity: ActivityRecord{abc123 u0 com.example.app/.MainActivity t123}\n"
        if cmd.startswith("dumpsys gfxinfo"):
            return "Total frames rendered: 100\nJanky frames: 5\n"
        return ""


async def test_get_alerts_snapshot_after_consecutive_breach():
    """连续 CPU 越限（默认 above=80）→ get_alerts 返回活动快照；stop 后清空（方案 24 §7）。"""
    service = PerformanceService(HighCpuAdb())
    await service.start_monitoring("dev-A", interval=0)

    alerts: list[dict] = []
    for _ in range(500):
        alerts = service.get_alerts("dev-A", time.time())
        if alerts:
            break
        await asyncio.sleep(0.01)

    assert alerts, "连续越限后应有活动告警"
    assert alerts[0]["id"] == "cpu_percent"
    assert alerts[0]["direction"] == "above"
    assert alerts[0]["threshold"] == 80.0
    assert alerts[0]["notify"] is True          # 首次触发，窗口内
    assert isinstance(alerts[0]["since"], float)

    await service.stop_monitoring("dev-A")
    assert service.get_alerts("dev-A", time.time()) == []   # D5：释放即清空


async def test_alert_engine_released_when_device_lost():
    """失联自然结束路径（_release_device 单点）同样释放引擎。"""
    service = PerformanceService(DeadAdb())
    await service.start_monitoring("dev-B", interval=0)

    for _ in range(500):
        if "dev-B" not in service._tasks:
            break
        await asyncio.sleep(0.01)

    assert "dev-B" not in service._alert_engines
    assert service.get_alerts("dev-B", time.time()) == []


async def test_sampling_loop_evaluates_each_round():
    """采样循环每轮 sync + evaluate 各指标（先评估后推送）。"""
    service = PerformanceService(HighCpuAdb())
    await service.start_monitoring("dev-C", interval=0)

    class SpyEngine:
        def __init__(self) -> None:
            self.synced = 0
            self.evaluated: list[str] = []

        def sync(self, rules: list) -> None:
            self.synced += 1

        def evaluate(self, metric_id: str, value: object, ts: float, fresh: bool = True) -> None:
            self.evaluated.append(metric_id)

        def snapshot(self, sample_ts: float) -> list:
            return []

    spy = SpyEngine()
    service._alert_engines["dev-C"] = spy  # type: ignore[assignment]

    for _ in range(500):
        if spy.synced >= 2:
            break
        await asyncio.sleep(0.01)

    await service.stop_monitoring("dev-C")
    assert spy.synced >= 2
    assert {"cpu_percent", "memory_percent", "fps"} <= set(spy.evaluated)
