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
    6. 自然结束自动收尾（方案 42 §1：cli 监听器 → 复用 stop 路径拉回产物）

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
import re
import time
from pathlib import Path
from uuid import uuid4

from app.core.config import settings
from app.core.exceptions import AdbError, ConflictError
from app.core.logging import get_logger
from app.domain.ports import AdbDriver
from app.domain.recording import RecordingSession, RecordingState
from config.settings import app_base_dir, device_recording_settings

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
    # 滞留态自愈阈值（秒）：stopping/pulling 超过 time_limit + 该值仍无推进
    # → 判悬挂收敛 failed（方案 42 §2；正常 60s 录制 + 收尾远不到此阈值）
    STUCK_STATE_TIMEOUT_SECONDS = 120.0
    # 主机落盘产物文件名模式（与 REMOTE_PREFIX basename 同源，方案 42 §3 清盘依据）
    LOCAL_FILE_GLOB = "openscrcpy_rec_*.mp4"

    # 新命名：openscrcpy_rec_{dev}__{12hex}.mp4；旧命名：openscrcpy_rec_{12hex}.mp4
    # dev 段 sanitize 后取 [A-Za-z0-9._-]+；__ 从右一次分割天然容纳 dev 内单下划线
    _NEW_NAME_RE = re.compile(r"^openscrcpy_rec_(?P<dev>[A-Za-z0-9._-]+)__(?P<sid>[0-9a-f]{12})\.mp4$")
    _OLD_NAME_RE = re.compile(r"^openscrcpy_rec_(?P<sid>[0-9a-f]{12})\.mp4$")

    @staticmethod
    def _sanitize_device(device_id: str) -> str:
        """设备 id → 文件名片段：非法字符替换为 `-`（方案 46 §3.3）。"""
        return re.sub(r"[^A-Za-z0-9._-]", "-", device_id)

    @staticmethod
    def parse_product_name(filename: str) -> tuple[str | None, str | None]:
        """
        落盘文件名 → (device, session_id)（方案 46 §3.3）。

        新格式返回 (dev, sid)；旧格式返回 (None, sid)；
        两者皆不匹配（含大写 hex、段数异常）返回 (None, None)。
        """
        m = RecordingService._NEW_NAME_RE.match(filename)
        if m:
            return m.group("dev"), m.group("sid")
        m = RecordingService._OLD_NAME_RE.match(filename)
        if m:
            return None, m.group("sid")
        return None, None

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
        # 自动收尾协程引用集：防事件循环 GC 中途回收 pending 任务（asyncio 官方告诫）
        self._finalize_tasks: set[asyncio.Task[None]] = set()
        # 自然结束 → 自动收尾监听器（方案 42 §1，依赖倒置注册，协议见 AdbDriver）
        adb.set_recording_natural_end_listener(self._on_recording_natural_end)

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
        # 设备级覆盖合并视图（方案 45 T8）：无覆盖的设备即全局 recording 对象本身
        cfg = device_recording_settings(device_id)
        session_id = uuid4().hex[:12]
        remote_path = (
            f"{self.REMOTE_PREFIX}{self._sanitize_device(device_id)}__{session_id}.mp4"
        )
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
        if session.state != RecordingState.RECORDING.value:
            # stopping/pulling 进行态再入：后进者直返当前态、绝不触达 adb，
            # 防二次 stop_recording pop 产物失败后 rm+FAILED 炸掉前进者的拉取（方案 51 E1）
            return session
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
        local_dir = self.resolve_dir()
        local_path: str | None
        try:
            local_dir.mkdir(parents=True, exist_ok=True)
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
        except OSError as exc:
            # 落盘目录不可写：收敛 failed 不冒泡（会话不得卡死 PULLING）
            logger.warning(
                "recording_mkdir_failed",
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

    # -------------------------------------------------------------------
    # 自然结束自动收尾（方案 42 §1）
    # -------------------------------------------------------------------

    def _on_recording_natural_end(self, device_id: str) -> None:
        """
        cli 自然结束监听器（事件循环线程内同步回调）：会话仍 recording 才调度
        收尾协程；非 recording（用户已手动 stop / 无会话）no-op。
        """
        session = self.sessions.get(device_id)
        if session is None or session.state != RecordingState.RECORDING.value:
            return
        task = asyncio.create_task(self._auto_finalize(device_id, session.id))
        self._finalize_tasks.add(task)
        task.add_done_callback(self._finalize_tasks.discard)

    async def _auto_finalize(self, device_id: str, session_id: str) -> None:
        """
        自然结束自动收尾：等效系统代为执行一次 stop（复用五态机 pull→done）。

        执行时二次守卫（会话 id 匹配且仍 recording）——防协程排队期间会话
        被手动 stop 收尾并被新 start 覆盖后误停新段（Review Focus ②）。
        收尾自身异常不冒泡（stopping/pulling 悬挂由方案 42 §2 自愈兜底）。
        """
        session = self.sessions.get(device_id)
        if (
            session is None
            or session.id != session_id
            or session.state != RecordingState.RECORDING.value
        ):
            return
        logger.info("recording_natural_end_finalize", session=session_id, device=device_id)
        try:
            await self.stop(device_id)
        except Exception:  # noqa: BLE001
            logger.exception("recording_auto_finalize_failed", device=device_id)

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

    def resolve_dir(self) -> Path:
        """录屏落盘目录：配置相对路径锚定 app_base_dir()，绝对路径原样。

        公开方法（评审 M1）：HTTP file 端点同源解析 containment 基准目录。
        """
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
        self._enforce_limits(self.resolve_dir())
        await self._self_heal()

    def cleanup_all_recordings(self) -> None:
        """
        删除主机落盘目录全部录屏产物（方案 42 §3）。

        依据：会话表纯内存——进程退出后 done 会话即无下载入口，残留文件属
        纯垃圾；录像按磁盘保留策略定位为暂存（关服清空合规而非例外）。
        调用点：lifecycle 优雅退出挂钩 + 清理循环首轮（强杀/崩溃兜底）。
        best-effort：单文件失败（Windows 下载器持有等）仅告警，不阻塞退出。
        """
        directory = self.resolve_dir()
        if not directory.is_dir():
            return
        for path in directory.glob(self.LOCAL_FILE_GLOB):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.warning("recording_cleanup_all_failed", path=str(path))

    async def _self_heal(self) -> None:
        """
        会话自愈（方案 37 B3 + 方案 42 §2）：

        - recording：started_at 超 time_limit + 宽限仍未收尾 → 自动走 stop；
        - stopping/pulling：超 time_limit + STUCK 仍无推进（stop 中途被非
          AdbError 异常打断的罕见路径）→ 设备端兜底 rm 后收敛 failed，
          消除持续 409 的滞留。rm 等待期间复核状态，防与真实 stop 并发误杀
          已 done 会话（Review Focus ③）。
        - time_limit 按会话设备取设备级覆盖合并视图（方案 45 T8）。
        """
        now = time.time()
        # 方案 45 T8：time_limit 按会话设备取覆盖合并视图（无覆盖回落全局）
        stale_ids = [
            s.device_id
            for s in self.sessions.values()
            if s.state == RecordingState.RECORDING.value
            and s.started_at
            < now
            - (
                device_recording_settings(s.device_id).time_limit
                + self.SELF_HEAL_GRACE_SECONDS
            )
        ]
        for device_id in stale_ids:
            logger.warning("recording_self_heal", device=device_id)
            await self.stop(device_id)
        for s in list(self.sessions.values()):
            if s.state not in (RecordingState.STOPPING.value, RecordingState.PULLING.value):
                continue
            cutoff = now - (
                device_recording_settings(s.device_id).time_limit
                + self.STUCK_STATE_TIMEOUT_SECONDS
            )
            if s.started_at >= cutoff:
                continue
            logger.warning("recording_stuck_self_heal", device=s.device_id, state=s.state)
            await self.adb.rm_remote(s.device_id, s.remote_path)
            # rm 等待期间真实 stop 可能已完成收尾：复核后才落 failed
            if s.state in (RecordingState.STOPPING.value, RecordingState.PULLING.value):
                s.state = RecordingState.FAILED.value

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
        """
        后台清理循环（方案 37 B3 + 42 §3）：首轮**立即**执行——扫盘清上个
        进程遗留（强杀/崩溃无退出钩子，下次启动兜底）+ 常规 cleanup；此后
        每 3600s 一轮 cleanup()；单轮异常记警告续跑。
        """
        first = True
        try:
            while True:
                try:
                    if first:
                        self.cleanup_all_recordings()
                    else:
                        await asyncio.sleep(self.CLEANUP_INTERVAL_SECONDS)
                    await self.cleanup()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("recording_cleanup_iteration_failed", error=str(exc))
                first = False
        except asyncio.CancelledError:
            logger.info("recording_cleanup_loop_cancelled")
