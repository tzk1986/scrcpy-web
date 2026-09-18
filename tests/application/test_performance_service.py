"""
性能监控服务失联自动停采测试（方案 17 实施项 3）
==================================================

覆盖：
    - 设备失联时采样连续失败 → 监控任务自动结束并清理 _tasks/_samplers
    - 订阅者收到 None 停止信号，stream_metrics 正常退出
"""

import asyncio

from app.application.performance_service import PerformanceService
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
    assert received == []  # 无任何度量产出
    assert consumer.done()  # 订阅者收到 None 后正常结束