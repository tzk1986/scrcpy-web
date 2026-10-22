"""
指标批写录制器
==============

层：基础设施 → 持久化。

录制期间把设备 Perf/Network 采样点缓冲在内存，满 batch_size 条
或每 flush_interval 秒用 save_*_samples_bulk 单事务 executemany
落库（缓存态），把逐条 commit 的开销摊薄（复用 BatchLogWriter
范式）。停止录制 / 失联 / 服务关闭时必须 stop()，否则缓冲点丢失。

参考方案文档：方案/18-Perf与Network采样数据持久化与导出.md §3.4
"""

import asyncio
import json
from typing import Any

from app.core.logging import get_logger
from app.domain.ports import MetricsRepository
from app.domain.runtime import NetworkStats, PerformanceMetrics

logger = get_logger(__name__)


class MetricRecorder:
    """单设备指标录制器：Perf/Network 两路缓冲共用同一落库节拍与事务策略。"""

    def __init__(
        self,
        repo: MetricsRepository,
        batch_size: int = 100,
        flush_interval: float = 0.1,
    ):
        self._repo = repo
        self._batch_size = max(1, batch_size)
        self._interval = flush_interval
        self._perf_rows: list[tuple[Any, ...]] = []
        self._network_rows: list[tuple[Any, ...]] = []
        self._task: asyncio.Task[None] | None = None
        self._written = 0
        # 方案 51 D12：即时冲刷任务持引用（done 回调 discard），防裸 create_task 被 GC
        self._flush_tasks: set[asyncio.Task[None]] = set()

    @property
    def written(self) -> int:
        """已成功落盘的行数（Perf+Network 合计，供 stop/status 回显）。"""
        return self._written

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._interval)
                await self.flush()
        except asyncio.CancelledError:
            pass

    def submit_perf(self, device_id: str, m: PerformanceMetrics) -> None:
        """性能采样入批（方案 37 D4：cpu_processes 落库前 JSON 序列化为
        数组串，空表 "[]"；电池三字段直存，None 也直存为 NULL）。"""
        self._perf_rows.append((
            device_id, m.ts, m.cpu_percent, m.total_memory_mb, m.used_memory_mb,
            m.fps, m.jank_count, m.current_activity, m.top_package,
            json.dumps([
                {"proc": p.proc, "user": p.user, "kernel": p.kernel}
                for p in m.cpu_processes
            ]),
            m.battery_temp,
            m.battery_voltage,
            m.battery_health,
        ))
        self._kick_if_full()

    def submit_network(self, device_id: str, s: NetworkStats) -> None:
        self._network_rows.append((
            device_id, s.ts, s.rx_bytes, s.tx_bytes, s.rx_rate_kbps,
            s.tx_rate_kbps, s.active_connections,
            1 if s.wifi_connected else 0, s.wifi_ssid,
        ))
        self._kick_if_full()

    def _kick_if_full(self) -> None:
        if len(self._perf_rows) + len(self._network_rows) >= self._batch_size:
            # 事件循环内无法 await，交给后台任务即时冲刷（D12 持引用）
            task = asyncio.create_task(self.flush())
            self._flush_tasks.add(task)
            task.add_done_callback(self._flush_tasks.discard)

    async def flush(self) -> None:
        perf_batch, self._perf_rows = self._perf_rows, []
        network_batch, self._network_rows = self._network_rows, []
        try:
            if perf_batch:
                await self._repo.save_perf_samples_bulk(perf_batch)
                self._written += len(perf_batch)
            if network_batch:
                await self._repo.save_network_samples_bulk(network_batch)
                self._written += len(network_batch)
        except Exception as e:
            # 落库失败不计数：本批数据丢弃（与 BatchLogWriter 语义一致）
            logger.error(
                "metric_recorder_flush_failed",
                perf=len(perf_batch),
                network=len(network_batch),
                error=str(e),
            )

    async def stop(self) -> int:
        """停录：取消后台冲刷任务并 final flush，返回累计落盘行数。"""
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self.flush()
        return self._written