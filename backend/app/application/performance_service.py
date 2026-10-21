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
import json
from collections import deque
from typing import Any, AsyncGenerator

from app.application.alert_engine import AlertEngine, build_rules
from app.application.recording_support import RecordingSupportMixin
from app.core.config import settings
from app.core.logging import get_logger
from app.domain.ports import AdbDriver, MetricsRepository
from app.infrastructure.performance.sampler import PerformanceSampler, PerformanceMetrics
from app.infrastructure.persistence.metric_recorder import MetricRecorder
from app.infrastructure.persistence.sqlite import SqliteMetricsRepository

logger = get_logger(__name__)


class PerformanceService(RecordingSupportMixin):
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

    def __init__(self, adb: AdbDriver, metrics_repo: MetricsRepository | None = None) -> None:
        """
        初始化服务。

        参数：
            adb: ADB 驱动实例。
            metrics_repo: 指标仓储（录制落盘与缓存态导出用），
                默认 SqliteMetricsRepository，测试可注入替身。
        """
        self.adb = adb
        # 内存缓冲区容量（@1s 采样约 1 小时/设备）；deque 构造后不可热伸缩，
        # 热重载仅对新建缓冲生效（方案 18 S3）
        self.buffer_capacity: int = settings().metrics.buffer_size
        self._metrics_repo = metrics_repo or SqliteMetricsRepository()
        self._samplers: dict[str, PerformanceSampler] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._buffers: dict[str, deque[PerformanceMetrics]] = {}
        self._subscribers: dict[str, list[asyncio.Queue[PerformanceMetrics | None]]] = {}
        self._idle_guards: dict[str, asyncio.Task[None]] = {}
        # 录制器挂接点（方案 18 Step 3）：空闲守卫以「无录制」为停采前提
        self._recorders: dict[str, MetricRecorder] = {}
        # 最近一次停止原因（stopped/device_lost/config/shutdown），供 status 回显
        self._record_reasons: dict[str, str] = {}
        # 告警引擎挂点（方案 24）：按设备实例化，随采样任务释放
        self._alert_engines: dict[str, AlertEngine] = {}

    # 录制/守卫三件套/清理的公共实现挂在 RecordingSupportMixin（方案 51 E11），
    # 事件名前缀与采样统计表由此声明。
    _recording_domain = "performance"

    async def _sample_stats(self, device_id: str) -> tuple[int, float | None, float | None]:
        return await self._metrics_repo.perf_sample_stats(device_id)

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
        self._alert_engines[device_id] = AlertEngine(notify_window=max(3 * interval, 5.0))

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
        cancelled = False
        try:
            async for metrics in sampler.sample(interval):
                # 存入缓冲区
                self._buffers[device_id].append(metrics)

                # 告警评估（方案 24）：先评估后推送，订阅者唤醒时快照已就绪
                engine = self._alert_engines.get(device_id)
                if engine is not None:
                    engine.sync(build_rules())
                    engine.evaluate("cpu_percent", metrics.cpu_percent, metrics.ts)
                    engine.evaluate(
                        "memory_percent", self._memory_percent(metrics), metrics.ts
                    )
                    engine.evaluate(
                        "fps", metrics.fps, metrics.ts, fresh=metrics.fps_fresh
                    )

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

                # 录制落盘（缓存态，方案 18 Step 3）；总开关热重载
                # true→false 时停录并停采（§3.6 状态机）
                recorder = self._recorders.get(device_id)
                if recorder is not None:
                    if settings().metrics.recording:
                        recorder.submit_perf(device_id, metrics)
                    else:
                        await self._finalize_recording(device_id, "config")
                        return

        except asyncio.CancelledError:
            cancelled = True
            logger.info("performance_monitoring_stopped", device=device_id)
        except Exception as e:
            logger.error("performance_monitoring_error", device=device_id, error=str(e))
        finally:
            # 非 cancel 结束（sample() 自然结束 = 失联判定路径，或 bug 异常）
            # → 录制置 stopped(device_lost)。cancel 路径由 stop_monitoring
            # 统一 finalize（reason='stopped'/'shutdown'）。
            if not cancelled:
                await self._finalize_recording(device_id, "device_lost")
            # 统一释放（B1：离线/自然结束路径同样释放暂存缓冲）
            self._release_device(device_id)

    async def stop_monitoring(self, device_id: str, record_reason: str = "stopped") -> None:
        """
        停止设备性能监控并释放内存暂存缓冲（幂等）。

        参数：
            device_id: 设备 ID。
            record_reason: 若该设备正录制，录制停止原因（§3.6 状态机
                reason 字段：stopped/device_lost/config/shutdown）。
        """
        await self._cancel_idle_guard(device_id)

        task = self._tasks.pop(device_id, None)
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        await self._finalize_recording(device_id, record_reason)
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
        self._alert_engines.pop(device_id, None)

        # 设备失联自动停采（sample() 自然结束，非 cancel 路径）时，
        # 通知订阅者结束；stop_monitoring 的 cancel 路径会先 pop 订阅者，
        # 此处为空表不重复通知
        for queue in self._subscribers.pop(device_id, []):
            try:
                queue.put_nowait(None)  # None 作为停止信号
            except Exception:
                pass

    @staticmethod
    def _memory_percent(m: PerformanceMetrics) -> float | None:
        """内存使用率（百分比）；total=0 时返回 None（引擎跳过该样本）。"""
        if m.total_memory_mb <= 0:
            return None
        return m.used_memory_mb / m.total_memory_mb * 100

    def get_alerts(self, device_id: str, sample_ts: float) -> list[dict[str, Any]]:
        """当前活动告警快照（纯内存读取；无引擎时为空，方案 24 D5）。"""
        engine = self._alert_engines.get(device_id)
        if engine is None:
            return []
        return engine.snapshot(sample_ts)

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
        return [self.metrics_to_dict(m) for m in recent]

    async def stream_metrics(self, device_id: str) -> AsyncGenerator[PerformanceMetrics, None]:
        # 方案 51 E15：返回类型改准为 AsyncGenerator——ws 侧 aclosing 需 aclose 契约
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

    def metrics_to_dict(self, m: PerformanceMetrics) -> dict[str, Any]:
        """将 PerformanceMetrics 转为字典（用于 JSON 序列化）。

        方案 51 E15：转公开——ws 层跨层直调私有方法的形态消除。

        方案 37 D3：旧 8 键相对序与值零变化，末尾追加 4 新键。
        cpu_processes 必须纯值 dict 化——dataclass 列表无法被
        WS send_json / json.dumps 直接序列化。
        """
        return {
            "ts": m.ts,
            "cpu_percent": m.cpu_percent,
            "total_memory_mb": m.total_memory_mb,
            "used_memory_mb": m.used_memory_mb,
            "fps": m.fps,
            "jank_count": m.jank_count,
            "current_activity": m.current_activity,
            "top_package": m.top_package,
            "cpu_processes": [
                {"proc": p.proc, "user": p.user, "kernel": p.kernel}
                for p in m.cpu_processes
            ],
            "battery_temp": m.battery_temp,
            "battery_voltage": m.battery_voltage,
            "battery_health": m.battery_health,
        }

    # 录制三态（record_start/stop/status）与 _finalize_recording 走
    # RecordingSupportMixin 公共实现（方案 51 E11），本域差异仅
    # _sample_stats / _recording_domain（见类头）。

    # ------------------------------------------------------------------
    # 导出（交付态，方案 18 Step 4）
    # ------------------------------------------------------------------

    async def export_cached(
        self,
        device_id: str,
        from_ts: float | None = None,
        to_ts: float | None = None,
        limit: int = 50000,
    ) -> list[dict[str, Any]]:
        """
        缓存态区间查询（source=cache）。查询前先 flush 录制批写缓冲
        （S14：避免缓冲内已采未落盘的边界丢数）。
        """
        recorder = self._recorders.get(device_id)
        if recorder is not None:
            await recorder.flush()
        rows = await self._metrics_repo.query_perf_samples(
            device_id, from_ts=from_ts, to_ts=to_ts, limit=limit
        )
        # 方案 37 D4：缓存源 cpu_processes 为 JSON 串（TEXT 列），parse 回
        # list 使 cache 源与 buffer 源（metrics_to_dict 纯值 list）JSON
        # 导出形态一致；坏数据/None/旧行缺列 → [] 容错，不炸导出。
        for row in rows:
            row["cpu_processes"] = self._parse_cpu_processes(
                row.get("cpu_processes")
            )
        return rows

    @staticmethod
    def _parse_cpu_processes(value: Any) -> list[Any]:
        """cpu_processes JSON 串 → list；str 才 parse，已 parse 的 list 原样放行，其余容错为 []。"""
        if isinstance(value, list):
            return value
        if not isinstance(value, str):
            return []
        try:
            parsed: Any = json.loads(value)
        except (ValueError, TypeError):
            return []
        return parsed if isinstance(parsed, list) else []

    # ------------------------------------------------------------------
    # 空闲守卫（暂存态清零）
    # ------------------------------------------------------------------
    # schedule/cancel/requeue 三件套走 RecordingSupportMixin（方案 51 E11）；
    # 复查谓词按域特化（订阅者存活），保留于此。

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

