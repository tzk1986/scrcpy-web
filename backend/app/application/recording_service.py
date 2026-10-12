"""
录屏服务
========

层：应用层。

方案 37 子项 B3：单设备屏幕录制会话编排。

职责：
    1. 会话创建与单设备互斥（同一设备同时至多一个进行态会话）
    2. 五态状态机：recording → stopping → pulling → done / failed
    3. 设备端产物拉取与即清（收尾末端对会话自身 remote_path 兜底 rm）
    4. 主机落盘目录时间/容量双限（mtime 超 retention_days / 总字节超 max_total_mb）
    5. 超期会话自愈收尾（周期 cleanup task 全表巡检）

依赖（通过 Protocol 注入）：
    - AdbDriver：start_recording / stop_recording / pull_recording / rm_remote

设计决策（B2 评审跨任务约束）：
    - 服务自记 remote_path：stop 收尾的 pull 与 rm 一律使用会话自身
      remote_path，不依赖 stop_recording 返回值（其「空串=无产物」仅作
      是否尝试 pull 的信号，返回值内容只在与新起会话互操作场景续用）；
    - 每次 stop 收尾（无论 done/failed）路径末端都对会话自身 remote_path
      兜底 rm_remote，防「stop 返回空串但设备端仍有 MP4 残留」（自然结束）。
"""

import asyncio
import time
from pathlib import Path
from uuid import uuid4

from app.core.config import settings
from app.core.exceptions import AdbError, ConflictError
from app.core.logging import get_logger
from app.domain.ports import AdbDriver
from app.domain.recording import RecordingSession, RecordingState
from config.settings import app_base_dir

logger = get_logger(__name__)

# 进行态（非终态）集合：存在即拒绝新的 start
_IN_PROGRESS_STATES = (
    RecordingState.RECORDING.value,
    RecordingState.STOPPING.value,
    RecordingState.PULLING.value,
)
_TERMINAL_STATES = (RecordingState.DONE.value, RecordingState.FAILED.value)


class RecordingService:
    """单设备屏幕录制用例（方案 37 B3）。"""

    # 设备端产物固定前缀（id 为 12 位十六进制）
    REMOTE_PREFIX = "/sdcard/openscrcpy_rec_"
    # 周期清理间隔（秒）：目录双限 + 会话自愈巡检
    CLEANUP_INTERVAL_SECONDS = 3600.0
    # 自愈宽限（秒）：started_at 超 time_limit + 宽限仍未收尾 → 自动 stop
    SELF_HEAL_GRACE_SECONDS = 30

    def __init__(self, adb: AdbDriver) -> None:
        """
        参数：
            adb: 用于设备通信的 ADB 驱动。
        """
        self.adb = adb
        # 每设备至多一个会话（key: device_id）；终态会话在下一次 start 时被覆盖
        self.sessions: dict[str, RecordingSession] = {}
        # 周期清理任务（lifespan 启动/停止）
        self.cleanup_task: asyncio.Task[None] | None = None

    # -------------------------------------------------------------------
    # 会话生命周期
    # -------------------------------------------------------------------

    async def start(self, device_id: str) -> RecordingSession:
        """
        启动设备端录屏并落会话表。

        已有进行态会话（recording/stopping/pulling）→ ConflictError（409）。
        设备端启动失败（AdbError）向上冒泡且不落表（无副作用，可重试）。

        参数：
            device_id: 目标设备的 ADB 序列号。

        返回：
            新创建的 RecordingSession（state=recording）。

        异常：
            ConflictError: 该设备已有进行态会话。
            AdbError: 设备端 screenrecord 启动失败。
        """
        existing = self.sessions.get(device_id)
        if existing is not None and existing.state in _IN_PROGRESS_STATES:
            raise ConflictError()
        cfg = settings().recording
        session_id = uuid4().hex[:12]
        remote_path = f"{self.REMOTE_PREFIX}{session_id}.mp4"
        # 先启动设备端再落表：失败时本 start 无副作用（表内无会话可收敛）
        await self.adb.start_recording(
            device_id, remote_path, cfg.bit_rate, cfg.time_limit
        )
        session = RecordingSession(
            id=session_id,
            device_id=device_id,
            state=RecordingState.RECORDING.value,
            remote_path=remote_path,
        )
        self.sessions[device_id] = session
        logger.info("recording_started", session=session_id, device=device_id)
        return session

    async def stop(self, device_id: str) -> RecordingSession | None:
        """
        停止录屏并按五态机收尾（幂等，不抛 ADB 异常）。

        无会话 → None（端点层定 404 语义）；终态会话幂等直返。
        进行态会话：recording → stopping → pulling → done / failed。
        每次收尾（无论 done/failed）末端都对会话自身 remote_path 做
        设备端兜底 rm（rm_remote 驱动内幂等不抛）。

        参数：
            device_id: 目标设备的 ADB 序列号。

        返回：
            终态会话；无会话时 None。

        异常：
            AdbError 均被捕住收敛 failed，不冒泡。
        """
        session = self.sessions.get(device_id)
        if session is None:
            return None
        if session.state in _TERMINAL_STATES:
            return session  # 幂等：重复 stop 直返终态会话
        session.state = RecordingState.STOPPING.value
        logger.info("recording_stopping", session=session.id, device=device_id)
        try:
            result = await self.adb.stop_recording(device_id)
        except AdbError as exc:
            logger.warning(
                "recording_stop_failed",
                session=session.id,
                device=device_id,
                error=str(exc),
            )
            await self.adb.rm_remote(device_id, session.remote_path)
            session.state = RecordingState.FAILED.value
            return session
        if not result:
            # 无产物（进程早已退出）：兜底清设备端后收敛 failed
            logger.warning("recording_no_product", session=session.id, device=device_id)
            await self.adb.rm_remote(device_id, session.remote_path)
            session.state = RecordingState.FAILED.value
            return session
        session.state = RecordingState.PULLING.value
        local_dir = self._resolve_dir()
        local_dir.mkdir(parents=True, exist_ok=True)
        local_path: str | None
        try:
            local_path = await self.adb.pull_recording(
                device_id, session.remote_path, str(local_dir)
            )
        except AdbError as exc:
            logger.warning(
                "recording_pull_failed",
                session=session.id,
                device=device_id,
                error=str(exc),
            )
            local_path = None
        # 无论拉取结果，设备端产物都即清（pull 使用会话自身 remote_path）
        await self.adb.rm_remote(device_id, session.remote_path)
        if local_path is None:
            session.state = RecordingState.FAILED.value
            return session
        logger.info(
            "recording_saved", session=session.id, device=device_id, local=local_path
        )
        session.local_path = local_path
        session.state = RecordingState.DONE.value
        return session

    def get(self, device_id: str) -> RecordingSession | None:
        """
        按设备 ID 检索会话。

        参数：
            device_id: 目标设备的 ADB 序列号。

        返回：
            找到则返回 RecordingSession，否则 None。
        """
        return self.sessions.get(device_id)

    def list_all(self) -> list[RecordingSession]:
        """返回会话全表（每设备至多一条；B4 端点层自行过滤活跃态）。"""
        return list(self.sessions.values())

    # -------------------------------------------------------------------
    # 目录双限与会话自愈
    # -------------------------------------------------------------------

    def _resolve_dir(self) -> Path:
        """录屏落盘目录：配置相对路径锚定 app_base_dir()，绝对路径原样。"""
        raw = Path(settings().recording.dir)
        return raw if raw.is_absolute() else app_base_dir() / raw

    @staticmethod
    def _enforce_limits(directory: Path) -> None:
        """
        录屏目录时间/容量双限（方案 37 B3）。

        时间维：mtime 超 retention_days 的产物删除；
        容量维：目录总字节超 max_total_mb 时按 mtime 排序删旧，直至降到限下。

        参数：
            directory: 主机落盘目录。
        """
        cfg = settings().recording
        if not directory.is_dir():
            return
        cutoff = time.time() - cfg.retention_days * 86400
        for path in directory.iterdir():
            if not path.is_file():
                continue
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)
            except OSError:
                logger.warning("recording_time_limit_unlink_failed", path=str(path))
        max_bytes = cfg.max_total_mb * 1024 * 1024
        files = sorted(
            (p for p in directory.iterdir() if p.is_file()),
            key=lambda p: p.stat().st_mtime,
        )
        total = sum(p.stat().st_size for p in files)
        for path in files:
            if total <= max_bytes:
                break
            try:
                size = path.stat().st_size
                path.unlink(missing_ok=True)
            except OSError:
                logger.warning("recording_size_limit_unlink_failed", path=str(path))
                continue
            total -= size

    async def cleanup(self) -> None:
        """一轮清理：目录双限 + 会话自愈巡检（周期任务与手动触发共用）。"""
        self._enforce_limits(self._resolve_dir())
        await self._self_heal()

    async def _self_heal(self) -> None:
        """
        会话自愈：started_at 超 time_limit + 宽限仍 recording 的会话自动走
        stop 收尾（每次周期 tick 全表巡检）。
        """
        cfg = settings().recording
        cutoff = time.time() - (cfg.time_limit + self.SELF_HEAL_GRACE_SECONDS)
        stale_ids = [
            s.device_id
            for s in self.sessions.values()
            if s.state == RecordingState.RECORDING.value and s.started_at < cutoff
        ]
        for device_id in stale_ids:
            logger.warning("recording_self_heal", device=device_id)
            await self.stop(device_id)

    # -------------------------------------------------------------------
    # 周期清理任务（lifespan 启动/停止）
    # -------------------------------------------------------------------

    async def start_cleanup_task(self) -> None:
        """启动周期清理任务（1h：目录双限 + 自愈巡检）。"""
        if self.cleanup_task and not self.cleanup_task.done():
            logger.warning("recording_cleanup_task_already_running")
            return
        self.cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info(
            "recording_cleanup_task_started",
            interval_seconds=self.CLEANUP_INTERVAL_SECONDS,
        )

    async def stop_cleanup_task(self) -> None:
        """停止周期清理任务（幂等）。"""
        if self.cleanup_task and not self.cleanup_task.done():
            self.cleanup_task.cancel()
            try:
                await self.cleanup_task
            except asyncio.CancelledError:
                pass
        logger.info("recording_cleanup_task_stopped")

    async def _cleanup_loop(self) -> None:
        """后台清理循环：每 3600s 一轮 cleanup()；单轮异常记警告续跑。"""
        try:
            while True:
                await asyncio.sleep(self.CLEANUP_INTERVAL_SECONDS)
                try:
                    await self.cleanup()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("recording_cleanup_iteration_failed", error=str(exc))
        except asyncio.CancelledError:
            logger.info("recording_cleanup_loop_cancelled")