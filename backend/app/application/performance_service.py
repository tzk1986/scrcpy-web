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
from collections import deque
from typing import Any, AsyncIterator

from app.core.config import settings
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

    def __init__(self, adb: AdbDriver) -> None:
        """
        初始化服务。

        参数：
            adb: ADB 驱动实例。
        """
        self.adb = adb
        # 内存缓冲区容量（@1s 采样约 1 小时/设备）；deque 构造后不可热伸缩，
        # 热重载仅对新建缓冲生效（方案 18 S3）
        self.buffer_capacity: int = settings().metrics.buffer_size
        self._samplers: dict[str, PerformanceSampler] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._buffers: dict[str, deque[PerformanceMetrics]] = {}
        self._subscribers: dict[str, list[asyncio.Queue[PerformanceMetrics | None]]] = {}
        self._idle_guards: dict[str, asyncio.Task[None]] = {}
        # 录制器挂接点（Step 3 填充）：空闲守卫以「无录制」为停采前提
        self._recorders: dict[str, Any] = {}

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
        self._buffers[device_id] = deque(maxlen=self.buffer_capacity)
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
            # 统一释放（B1：离线/自然结束路径同样释放暂存缓冲）
            self._release_device(device_id)

    async def stop_monitoring(self, device_id: str) -> None:
        """
        停止设备性能监控并释放内存暂存缓冲（幂等）。

        参数：
            device_id: 设备 ID。
        """
        await self._cancel_idle_guard(device_id)

        task = self._tasks.pop(device_id, None)
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        self._release_device(device_id)

        logger.info("performance_monitoring_stopped", device=device_id)

    def _release_device(self, device_id: str) -> None:
        """统一释放设备的采样任务、采样器与暂存缓冲（幂等）。

        stop_monitoring 与采样循环 finally（含设备失联自然结束路径）
        都走这里，保证暂存态「即用即清」（方案 18 §3.1）。
        """
        self._tasks.pop(device_id, None)
        self._samplers.pop(device_id, None)
        self._buffers.pop(device_id, None)

        # 设备失联自动停采（sample() 自然结束，非 cancel 路径）时，
        # 通知订阅者结束；stop_monitoring 的 cancel 路径会先 pop 订阅者，
        # 此处为空表不重复通知
        for queue in self._subscribers.pop(device_id, []):
            try:
                queue.put_nowait(None)  # None 作为停止信号
            except Exception:
                pass

    async def get_metrics(self, device_id: str, limit: int = 100) -> list[dict[str, Any]]:
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

        # 新订阅到来即取消空闲守卫（方案 18 Step 2）
        await self._cancel_idle_guard(device_id)

        # 竞态兜底：取消守卫期间守卫可能恰好完成停采（_tasks 已清），
        # 须重新拉起监控，否则订阅者挂死或 KeyError。
        if device_id not in self._tasks:
            await self.start_monitoring(device_id)

        # 创建订阅队列
        queue: asyncio.Queue[PerformanceMetrics | None] = asyncio.Queue(maxsize=60)
        self._subscribers.setdefault(device_id, []).append(queue)

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
            # 订阅者清空且无录制 → 安排空闲守卫（idle_ttl 后复查停采）
            if not self._subscribers.get(device_id) and device_id not in self._recorders:
                self._schedule_idle_guard(device_id)

    def _metrics_to_dict(self, m: PerformanceMetrics) -> dict[str, Any]:
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

    # ------------------------------------------------------------------
    # 空闲守卫（暂存态清零）
    # ------------------------------------------------------------------

    def _schedule_idle_guard(self, device_id: str) -> None:
        """安排空闲守卫任务（同设备已有未完成守卫时跳过）。"""
        existing = self._idle_guards.get(device_id)
        if existing is not None and not existing.done():
            return
        self._idle_guards[device_id] = asyncio.create_task(
            self._idle_guard(device_id)
        )

    async def _cancel_idle_guard(self, device_id: str) -> None:
        """取消并回收空闲守卫任务（幂等）。

        守卫复查路径的调用者就是守卫任务自身：此时只注销、不取消，
        否则 cancel 自身再 await 自身会自死锁。

        等待守卫退出时不用 await guard：Py 3.10 会把调用者的取消经
        _fut_waiter 转嫁给被等待的 guard 任务，其 CancelledError 抛回
        调用者后若被吞掉，调用者的取消请求也随之丢失（订阅不停止）。
        改为轮询 done，挂起点是 sleep(0) 而非 guard 任务，取消可正常
        注入调用者。
        """
        guard = self._idle_guards.pop(device_id, None)
        if guard is None or guard.done():
            return
        if guard is asyncio.current_task():
            return
        guard.cancel()
        while not guard.done():
            await asyncio.sleep(0)

    async def _idle_guard(self, device_id: str) -> None:
        """空闲复查：idle_ttl 后仍无订阅者且无录制 → stop_monitoring。"""
        try:
            await asyncio.sleep(float(settings().metrics.idle_ttl_seconds))
            # 复查（方案 18 §3.6：无订阅且无录制才停采；录制中禁止空闲停采）
            if device_id not in self._tasks:
                return
            if self._subscribers.get(device_id):
                return
            if device_id in self._recorders:
                # 继续观察：录制结束后仍可停采
                self._requeue_idle_guard(device_id)
                return
            await self.stop_monitoring(device_id)
        finally:
            current = asyncio.current_task()
            if self._idle_guards.get(device_id) is current:
                self._idle_guards.pop(device_id, None)

    def _requeue_idle_guard(self, device_id: str) -> None:
        """守卫自查路径的重安排：先注销自身注册，否则 schedule 的去重判断
        会被「自己仍在注册表且未完成」挡住，导致守卫链断裂。"""
        current = asyncio.current_task()
        if self._idle_guards.get(device_id) is current:
            self._idle_guards.pop(device_id, None)
        self._schedule_idle_guard(device_id)

    async def cleanup(self) -> None:
        """清理所有监控任务并释放暂存（服务关闭时调用，幂等）。"""
        for device_id in list(self._tasks.keys()):
            await self.stop_monitoring(device_id)
        for device_id in list(self._idle_guards.keys()):
            await self._cancel_idle_guard(device_id)

        logger.info("performance_service_cleaned_up")
