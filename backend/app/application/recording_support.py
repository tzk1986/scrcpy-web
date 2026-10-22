"""
录制与空闲守卫公共支撑（方案 51 E11）
======================================

层：应用层（横切共享件）。

Perf/Network 两监控服务在「录制三态（start/stop/status）+ finalize +
空闲守卫调度三件套 + 关闭清理」上近乎逐行重复（走查实测 ~150 行）。
本模块以 Mixin 收敛公共实现，宿主服务差异仅剩：

    - `_recording_domain`（日志事件名前缀 "network"/"performance"）；
    - `_sample_stats`（perf/network 两表的聚合统计查询）；
    - `_idle_guard` 复查谓词（perf 看订阅者、network 看 HTTP 轮询时间戳），
      语义不同故保留在各自服务内。

宿主契约（Mixin 依赖的属性/方法）：
    _tasks / _recorders / _record_reasons / _idle_guards 实例字典，
    start_monitoring / stop_monitoring（record_reason 参数）由宿主实现。

参考方案文档：方案/18-Perf与Network采样数据持久化与导出.md §3.1/§3.6/§3.7
"""

import asyncio
from typing import Any, ClassVar

from app.application.metric_recorder import MetricRecorder
from app.core.config import settings
from app.core.exceptions import RecordingDisabledError
from app.core.logging import get_logger
from app.domain.ports import MetricsRepository

logger = get_logger(__name__)


class RecordingSupportMixin:
    """录制生命周期 + 空闲守卫调度 + 关闭清理的公共实现。"""

    _recorders: dict[str, MetricRecorder]
    _record_reasons: dict[str, str]
    _idle_guards: dict[str, asyncio.Task[None]]
    _tasks: dict[str, asyncio.Task[None]]
    _metrics_repo: MetricsRepository
    _recording_domain: ClassVar[str]

    # ------------------------------------------------------------------
    # 宿主必须实现的挂点（Mixin 声明供静态检查；实例方法按 MRO 覆盖之）
    # ------------------------------------------------------------------

    async def start_monitoring(self, device_id: str) -> None:
        raise NotImplementedError

    async def stop_monitoring(self, device_id: str, record_reason: str = "stopped") -> None:
        raise NotImplementedError

    async def _sample_stats(self, device_id: str) -> tuple[int, float | None, float | None]:
        """该设备采样聚合 (rows, oldest_ts, newest_ts)（§3.3 S13，跨批次叠加）。"""
        raise NotImplementedError

    async def _idle_guard(self, device_id: str) -> None:
        """空闲复查循环体（按域语义由宿主实现：perf 看订阅者、network 看访问时间）。"""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # 录制（缓存态落盘，方案 18 Step 3）
    # ------------------------------------------------------------------

    async def _finalize_recording(self, device_id: str, reason: str) -> int:
        """结束录制：stop 录制器（final flush）并记录停止原因（幂等）。

        返回本次录制落盘行数；未在录制时返回 0。
        """
        recorder = self._recorders.pop(device_id, None)
        if recorder is None:
            return 0
        rows = await recorder.stop()
        self._record_reasons[device_id] = reason
        logger.info(
            f"{self._recording_domain}_recording_stopped",
            device=device_id,
            reason=reason,
            rows=rows,
        )
        return rows

    async def record_start(self, device_id: str) -> dict[str, Any]:
        """
        开启录制（幂等）：已录制时返回现状态；未录制时拉起采样循环
        并挂接批写录制器。metrics.recording=false 时抛
        RecordingDisabledError（不静默，§3.6）。
        """
        if not settings().metrics.recording:
            raise RecordingDisabledError()
        if device_id in self._recorders:
            return await self.record_status(device_id)
        if device_id not in self._tasks:
            await self.start_monitoring(device_id)

        recorder = MetricRecorder(self._metrics_repo)
        await recorder.start()
        self._recorders[device_id] = recorder
        self._record_reasons.pop(device_id, None)
        logger.info(f"{self._recording_domain}_recording_started", device=device_id)
        return await self.record_status(device_id)

    async def record_stop(self, device_id: str) -> dict[str, Any]:
        """
        停止录制并停采（幂等）。返回 {recording, reason, rows}，
        rows 为本次录制落盘行数；未在录制时 rows=0。
        """
        rows = await self._finalize_recording(device_id, "stopped")
        await self.stop_monitoring(device_id)
        return {
            "recording": False,
            "reason": self._record_reasons.get(device_id),
            "rows": rows,
        }

    async def record_status(self, device_id: str) -> dict[str, Any]:
        """
        录制状态：{recording, reason, rows, oldest_ts, newest_ts}。

        rows/oldest_ts/newest_ts 为跨批次聚合值（§3.3 S13）；
        进程重启后运行态丢失 → recording:false。
        录制中先 flush 批写缓冲，保证行数与区间口径准确。
        """
        recording = device_id in self._recorders
        if recording:
            await self._recorders[device_id].flush()
        rows, oldest, newest = await self._sample_stats(device_id)
        return {
            "recording": recording,
            "reason": None if recording else self._record_reasons.get(device_id),
            "rows": rows,
            "oldest_ts": oldest,
            "newest_ts": newest,
        }

    # ------------------------------------------------------------------
    # 空闲守卫调度三件套（_idle_guard 复查语义按域保留在宿主服务内）
    # ------------------------------------------------------------------

    def _schedule_idle_guard(self, device_id: str) -> None:
        """安排空闲守卫任务（同设备已有未完成守卫时跳过）。"""
        existing = self._idle_guards.get(device_id)
        if existing is not None and not existing.done():
            return
        self._idle_guards[device_id] = asyncio.create_task(self._idle_guard(device_id))

    async def _cancel_idle_guard(self, device_id: str) -> None:
        """取消并回收空闲守卫任务（幂等）。

        守卫复查路径的调用者就是守卫任务自身：此时只注销、不取消，
        否则 cancel 自身再 await 自身会自死锁。

        等待守卫退出时不用 await guard：Py 3.10 会把调用者的取消经
        _fut_waiter 转嫁给被等待的 guard 任务，其 CancelledError 抛回
        调用者后若被吞掉，调用者的取消请求也随之丢失。改为轮询 done，
        挂起点是 sleep(0) 而非 guard 任务，取消可正常注入调用者。
        """
        guard = self._idle_guards.pop(device_id, None)
        if guard is None or guard.done():
            return
        if guard is asyncio.current_task():
            return
        guard.cancel()
        while not guard.done():
            await asyncio.sleep(0)

    def _requeue_idle_guard(self, device_id: str) -> None:
        """守卫自查路径的重安排：先注销自身注册，否则 schedule 的去重判断
        会被「自己仍在注册表且未完成」挡住，导致守卫链断裂。"""
        current = asyncio.current_task()
        if self._idle_guards.get(device_id) is current:
            self._idle_guards.pop(device_id, None)
        self._schedule_idle_guard(device_id)

    # ------------------------------------------------------------------
    # 关闭
    # ------------------------------------------------------------------

    async def cleanup(self) -> None:
        """清理所有监控任务并释放暂存（服务关闭时调用，幂等）。

        录制中设备 → stopped(reason='shutdown')（§3.6），final flush
        挂在此处（§3.7）。
        """
        for device_id in list(self._tasks.keys()):
            await self.stop_monitoring(device_id, record_reason="shutdown")
        # 兜底：无采样任务但仍在录制的孤儿状态
        for device_id in list(self._recorders.keys()):
            await self._finalize_recording(device_id, "shutdown")
        for device_id in list(self._idle_guards.keys()):
            await self._cancel_idle_guard(device_id)

        logger.info(f"{self._recording_domain}_service_cleaned_up")
