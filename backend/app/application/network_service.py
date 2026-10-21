"""
网络监控服务
============

层：应用层。

管理设备网络数据采集任务，维护内存环形缓冲（暂存态），提供：
- 采样循环（`metrics.network_interval`，默认 2s）+ 冷启动内联采样
- 失联判定：连续 `metrics.lost_failures` 次 strict 采样失败 → 停采并释放缓冲
- 空闲守卫：无订阅者/HTTP 访问/录制，`idle_ttl_seconds` 后复查仍空闲才停采
- 停止/失联/关闭统一走 `stop_monitoring` 释放（暂存即用即清）

参考方案文档：方案/18-Perf与Network采样数据持久化与导出.md
"""

import asyncio
import time
from collections import deque
from typing import Any

from app.application.alert_engine import AlertEngine, build_rules
from app.application.recording_support import RecordingSupportMixin
from app.core.config import settings
from app.core.logging import get_logger
from app.domain.ports import AdbDriver, MetricsRepository
from app.infrastructure.network.sampler import (
    NetworkConnection,
    NetworkSampler,
    NetworkStats,
)
from app.infrastructure.persistence.metric_recorder import MetricRecorder
from app.infrastructure.persistence.sqlite import SqliteMetricsRepository

logger = get_logger(__name__)


class NetworkService(RecordingSupportMixin):
    """
    网络监控服务。

    每个设备一个采样任务 + 一个 `deque(maxlen=network_buffer_size)` 环形缓冲。
    HTTP 轮询（/stats、/connections）刷新 `_last_access`，空闲守卫据此判断
    页面是否还在使用；停止监控/设备失联统一走 `_release_device` 释放暂存。

    使用示例：
        service = NetworkService(adb_driver)
        stats = await service.get_stats("192.168.1.100")  # 冷启动自动拉起采样
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
        self._metrics_repo = metrics_repo or SqliteMetricsRepository()
        self._samplers: dict[str, NetworkSampler] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._buffers: dict[str, deque[NetworkStats]] = {}
        self._idle_guards: dict[str, asyncio.Task[None]] = {}
        self._last_access: dict[str, float] = {}
        # 录制器挂接点（方案 18 Step 3）：空闲守卫以「无录制」为停采前提
        self._recorders: dict[str, MetricRecorder] = {}
        # 最近一次停止原因（stopped/device_lost/config/shutdown），供 status 回显
        self._record_reasons: dict[str, str] = {}
        # 告警引擎挂点（方案 24）：按设备实例化，随采样任务释放
        self._alert_engines: dict[str, AlertEngine] = {}

    # 录制/守卫三件套/清理的公共实现挂在 RecordingSupportMixin（方案 51 E11），
    # 事件名前缀与采样统计表由此声明。
    _recording_domain = "network"

    async def _sample_stats(self, device_id: str) -> tuple[int, float | None, float | None]:
        return await self._metrics_repo.network_sample_stats(device_id)

    # ------------------------------------------------------------------
    # 监控生命周期
    # ------------------------------------------------------------------

    async def start_monitoring(self, device_id: str) -> None:
        """
        启动设备网络监控。

        如果已在监控，则忽略。

        参数：
            device_id: 设备 ID。
        """
        if device_id in self._tasks:
            logger.debug("network_monitoring_already_started", device=device_id)
            return

        sampler = NetworkSampler(self.adb, device_id)
        self._samplers[device_id] = sampler
        self._buffers[device_id] = deque(
            maxlen=settings().metrics.network_buffer_size
        )
        self._last_access.setdefault(device_id, time.time())
        self._alert_engines[device_id] = AlertEngine(
            notify_window=max(3 * float(settings().metrics.network_interval), 5.0)
        )

        task = asyncio.create_task(self._sampling_loop(device_id, sampler))
        self._tasks[device_id] = task
        self._schedule_idle_guard(device_id)

        logger.info("network_monitoring_started", device=device_id)

    async def _sampling_loop(self, device_id: str, sampler: NetworkSampler) -> None:
        """采样循环（后台任务），strict 模式计连续失败做失联判定。"""
        failures = 0
        cancelled = False
        try:
            while True:
                interval = float(settings().metrics.network_interval)
                lost_failures = int(settings().metrics.lost_failures)
                try:
                    stats = await sampler.get_stats(device_id, strict=True)
                except Exception:
                    failures += 1
                    logger.debug(
                        "network_sample_failed",
                        device=device_id,
                        failures=failures,
                    )
                    if failures >= lost_failures:
                        logger.warning(
                            "network_device_lost",
                            device=device_id,
                            failures=failures,
                        )
                        return  # finally 统一释放
                    await asyncio.sleep(interval)
                    continue

                failures = 0
                self._buffers[device_id].append(stats)

                # 告警评估（方案 24）：每轮实测，fresh 恒 True
                engine = self._alert_engines.get(device_id)
                if engine is not None:
                    engine.sync(build_rules())
                    engine.evaluate("rx_rate_kbps", stats.rx_rate_kbps, stats.ts)
                    engine.evaluate("tx_rate_kbps", stats.tx_rate_kbps, stats.ts)

                # 录制落盘（缓存态，方案 18 Step 3）；总开关热重载
                # true→false 时停录并停采（§3.6 状态机）
                recorder = self._recorders.get(device_id)
                if recorder is not None:
                    if settings().metrics.recording:
                        recorder.submit_network(device_id, stats)
                    else:
                        await self._finalize_recording(device_id, "config")
                        return

                await asyncio.sleep(interval)

        except asyncio.CancelledError:
            cancelled = True
            logger.info("network_monitoring_stopped", device=device_id)
        finally:
            # 失联（return）/异常路径 → 录制置 stopped(device_lost)。
            # cancel 路径由 stop_monitoring 统一 finalize。
            if not cancelled:
                await self._finalize_recording(device_id, "device_lost")
            # 方案 51 E11：释放改走 _release_device 单点（对齐 Perf，§3.1 即用即清）
            self._release_device(device_id)

    async def stop_monitoring(self, device_id: str, record_reason: str = "stopped") -> None:
        """
        停止设备网络监控并释放内存暂存缓冲（幂等）。

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

        logger.info("network_monitoring_stopped", device=device_id)

    def _release_device(self, device_id: str) -> None:
        """统一释放设备的采样任务、采样器与暂存缓冲（幂等，方案 51 E11）。

        stop_monitoring 与采样循环 finally（失联自然结束路径）都走这里，
        保证暂存态即用即清（方案 18 §3.1，口径对齐 PerformanceService）。
        """
        self._tasks.pop(device_id, None)
        self._samplers.pop(device_id, None)
        self._buffers.pop(device_id, None)
        self._alert_engines.pop(device_id, None)

    # ------------------------------------------------------------------
    # 读取（HTTP 层入口）
    # ------------------------------------------------------------------

    def _touch(self, device_id: str) -> None:
        """刷新最近访问时间（HTTP 轮询存活信号，供空闲守卫复查）。"""
        self._last_access[device_id] = time.time()

    async def get_stats(self, device_id: str) -> NetworkStats:
        """
        获取最新网络统计。

        冷启动策略：无采样任务时先拉起监控再内联采一次；缓冲最新点
        新鲜（age < network_interval）时直接复用，否则内联采一次。
        首包速率必为 0（速率需两次采样计算，属既有语义）。

        参数：
            device_id: 设备 ID。

        返回：
            NetworkStats 对象。
        """
        self._touch(device_id)
        interval = float(settings().metrics.network_interval)

        buffer = self._buffers.get(device_id)
        # 方案 51 E3：buffer 可能已建但尚未出首样本（空 deque），判空对齐
        # get_buffer_snapshot 守卫，空缓冲走下方内联采样兜底
        if buffer and (time.time() - buffer[-1].ts) < interval:
            return buffer[-1]

        if device_id not in self._tasks:
            await self.start_monitoring(device_id)
        sampler = self._samplers[device_id]
        return await sampler.get_stats(device_id)

    async def get_connections(self, device_id: str) -> list[NetworkConnection]:
        """
        获取活跃连接列表（即时快照，不入缓冲、不拉起采样任务）。

        参数：
            device_id: 设备 ID。

        返回：
            NetworkConnection 列表。
        """
        self._touch(device_id)
        sampler = self._samplers.get(device_id)
        if sampler is None:
            sampler = NetworkSampler(self.adb, device_id)
        return await sampler.get_connections(device_id)

    def get_alerts(self, device_id: str, sample_ts: float) -> list[dict[str, Any]]:
        """当前活动告警快照（纯内存读取；无引擎时为空，方案 24 D5）。"""
        engine = self._alert_engines.get(device_id)
        if engine is None:
            return []
        return engine.snapshot(sample_ts)

    def get_buffer_snapshot(self, device_id: str, limit: int = 50000) -> list[dict[str, Any]]:
        """
        暂存态快照（source=buffer 导出用）：缓冲最新 limit 条，
        序列化为字典列表（键与 CSV/JSON 导出字段一致）。
        """
        buffer = self._buffers.get(device_id)
        if not buffer:
            return []
        return [self._stats_to_dict(s) for s in list(buffer)[-limit:]]

    def _stats_to_dict(self, s: NetworkStats) -> dict[str, Any]:
        return {
            "ts": s.ts,
            "rx_bytes": s.rx_bytes,
            "tx_bytes": s.tx_bytes,
            "rx_rate_kbps": s.rx_rate_kbps,
            "tx_rate_kbps": s.tx_rate_kbps,
            "active_connections": s.active_connections,
            "wifi_connected": s.wifi_connected,
            "wifi_ssid": s.wifi_ssid,
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
        return await self._metrics_repo.query_network_samples(
            device_id, from_ts=from_ts, to_ts=to_ts, limit=limit
        )

    # ------------------------------------------------------------------
    # 空闲守卫（暂存态清零）
    # ------------------------------------------------------------------
    # schedule/cancel/requeue 三件套走 RecordingSupportMixin（方案 51 E11）；
    # 复查谓词按域特化（HTTP 轮询时间戳），保留于此。

    async def _idle_guard(self, device_id: str) -> None:
        """空闲复查：idle_ttl 后仍无录制且无访问 → stop_monitoring。"""
        try:
            await asyncio.sleep(float(settings().metrics.idle_ttl_seconds))
            # 复查（方案 18 §3.6：录制中禁止空闲停采）
            if device_id not in self._tasks:
                return
            if device_id in self._recorders:
                # 录制中禁止空闲停采（§3.6）；继续观察，录制结束后仍可停采
                self._requeue_idle_guard(device_id)
                return
            ttl = float(settings().metrics.idle_ttl_seconds)
            if time.time() - self._last_access.get(device_id, 0.0) < ttl:
                # 仍在被 HTTP 轮询使用：继续观察
                self._requeue_idle_guard(device_id)
                return
            await self.stop_monitoring(device_id)
        finally:
            current = asyncio.current_task()
            if self._idle_guards.get(device_id) is current:
                self._idle_guards.pop(device_id, None)

