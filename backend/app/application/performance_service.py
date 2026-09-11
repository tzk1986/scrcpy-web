"""
性能监控服务
============

层：应用层。

管理设备性能数据采集任务，提供：
- 启动/停止监控
- 查询历史指标
- 实时指标流

参考方案文档：方案/14-调试面板其他标签完善.md
"""

import asyncio
import time
from collections import deque
from typing import AsyncIterator

from app.core.logging import get_logger
from app.domain.ports import AdbDriver
from app.infrastructure.performance.sampler import PerformanceSampler, PerformanceMetrics

logger = get_logger(__name__)


class PerformanceService:
    """
    性能监控服务。

    管理多个设备的性能数据采集任务，维护内存环形缓冲区存储最近指标。
    支持实时推送（通过 async generator）。

    使用示例：
        service = PerformanceService(adb_driver)
        await service.start_monitoring("192.168.1.100")

        # 查询历史
        metrics = await service.get_metrics("192.168.1.100", limit=100)

        # 实时推送
        async for m in service.stream_metrics("192.168.1.100"):
            print(m.cpu_percent)
    """

    # 内存缓冲区大小（最多保存 1 小时数据，@1s 采样 = 3600 条）
    MAX_BUFFER_SIZE = 3600

    def __init__(self, adb: AdbDriver):
        """
        初始化服务。

        参数：
            adb: ADB 驱动实例。
        """
        self.adb = adb
        self._samplers: dict[str, PerformanceSampler] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._buffers: dict[str, deque[PerformanceMetrics]] = {}
        self._subscribers: dict[str, list[asyncio.Queue]] = {}

    async def start_monitoring(self, device_id: str, interval: float = 1.0) -> None:
        """
        启动设备性能监控。

        如果已在监控，则忽略。

        参数：
            device_id: 设备 ID。
            interval: 采样间隔（秒）。
        """
        if device_id in self._tasks:
            logger.debug("monitoring_already_started", device=device_id)
            return

        sampler = PerformanceSampler(self.adb, device_id)
        self._samplers[device_id] = sampler
        self._buffers[device_id] = deque(maxlen=self.MAX_BUFFER_SIZE)
        self._subscribers[device_id] = []

        # 启动采集任务
        task = asyncio.create_task(self._sampling_loop(device_id, sampler, interval))
        self._tasks[device_id] = task

        logger.info("performance_monitoring_started", device=device_id, interval=interval)

    async def _sampling_loop(
        self,
        device_id: str,
        sampler: PerformanceSampler,
        interval: float,
    ) -> None:
        """采样循环（后台任务）。"""
        try:
            async for metrics in sampler.sample(interval):
                # 存入缓冲区
                self._buffers[device_id].append(metrics)

                # 推送给订阅者
                for queue in self._subscribers.get(device_id, []):
                    try:
                        queue.put_nowait(metrics)
                    except asyncio.QueueFull:
                        # 队列满时丢弃旧数据
                        try:
                            queue.get_nowait()
                            queue.put_nowait(metrics)
                        except Exception:
                            pass

        except asyncio.CancelledError:
            logger.info("performance_monitoring_stopped", device=device_id)
        except Exception as e:
            logger.error("performance_monitoring_error", device=device_id, error=str(e))
        finally:
            self._tasks.pop(device_id, None)
            self._samplers.pop(device_id, None)

    async def stop_monitoring(self, device_id: str) -> None:
        """
        停止设备性能监控。

        参数：
            device_id: 设备 ID。
        """
        if task := self._tasks.pop(device_id, None):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        self._samplers.pop(device_id, None)
        self._buffers.pop(device_id, None)

        # 通知订阅者监控已停止
        for queue in self._subscribers.pop(device_id, []):
            try:
                queue.put_nowait(None)  # None 作为停止信号
            except Exception:
                pass

        logger.info("performance_monitoring_stopped", device=device_id)

    async def get_metrics(self, device_id: str, limit: int = 100) -> list[dict]:
        """
        获取历史性能指标。

        参数：
            device_id: 设备 ID。
            limit: 返回的最大条数。

        返回：
            指标字典列表（最新的 limit 条）。
        """
        buffer = self._buffers.get(device_id)
        if not buffer:
            return []

        # 返回最新的 limit 条
        recent = list(buffer)[-limit:]
        return [self._metrics_to_dict(m) for m in recent]

    async def stream_metrics(self, device_id: str) -> AsyncIterator[PerformanceMetrics]:
        """
        实时推送性能指标。

        如果未启动监控，自动启动。

        参数：
            device_id: 设备 ID。

        产出：
            PerformanceMetrics 对象。
        """
        # 自动启动监控
        if device_id not in self._tasks:
            await self.start_monitoring(device_id)

        # 创建订阅队列
        queue: asyncio.Queue[PerformanceMetrics | None] = asyncio.Queue(maxsize=60)
        self._subscribers[device_id].append(queue)

        try:
            while True:
                metrics = await queue.get()
                if metrics is None:
                    # 监控已停止
                    break
                yield metrics
        finally:
            # 清理订阅
            if device_id in self._subscribers:
                try:
                    self._subscribers[device_id].remove(queue)
                except ValueError:
                    pass

    def _metrics_to_dict(self, m: PerformanceMetrics) -> dict:
        """将 PerformanceMetrics 转为字典（用于 JSON 序列化）。"""
        return {
            "ts": m.ts,
            "cpu_percent": m.cpu_percent,
            "total_memory_mb": m.total_memory_mb,
            "used_memory_mb": m.used_memory_mb,
            "fps": m.fps,
            "jank_count": m.jank_count,
            "current_activity": m.current_activity,
            "top_package": m.top_package,
        }

    async def cleanup(self) -> None:
        """清理所有监控任务（服务关闭时调用）。"""
        device_ids = list(self._tasks.keys())
        for device_id in device_ids:
            await self.stop_monitoring(device_id)

        logger.info("performance_service_cleaned_up")
