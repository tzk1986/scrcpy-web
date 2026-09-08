"""
调试服务 — 无限调试能力
========================

层：应用层。

这是实现 OpenScrcpy "无限调试" 愿景的核心服务。它管理调试会话并提供：

    1. 会话生命周期：创建、获取、关闭
    2. 持续 logcat 收集：后台 asyncio 任务从设备流式传输 logcat
       并在内存中缓冲最近 N 条记录
    3. 日志查询：从内存缓冲区过滤（快速）或从数据库（慢速）
    4. Shell 执行：通过 ADB 在设备上运行命令
    5. Shell 历史：持久化和检索命令历史

关键设计决策：
    - 日志缓冲区在内存中（每个会话最多 MAX_LOG_BUFFER=50,000 条），
      用于快速过滤。数据库是用于会话持久化的预写日志。
    - Logcat 收集作为每个会话的 asyncio.Task 运行。当会话关闭时，
      任务被取消。
    - 日志解析处理 "threadtime" logcat 格式：
      "MM-DD HH:MM:SS.mmm  PID  TID LEVEL TAG: message"

依赖（通过 Protocol 类注入）：
    - AdbDriver：用于 shell 命令和 logcat 流式传输
    - DebugRepository：用于持久化会话、日志和 shell 历史
"""

import asyncio
import time
from typing import Optional

from app.core.logging import get_logger
from app.domain.ports import AdbDriver, DebugRepository, LogEntry, LogFilter
from app.domain.session import DebugSession

logger = get_logger(__name__)


class DebugService:
    """无限调试用例。"""

    # 每个会话在内存中保留的最大日志条目数。
    # 超过限制时旧条目按 FIFO 顺序淘汰。数据库保留所有记录。
    MAX_LOG_BUFFER = 50_000

    def __init__(self, adb: AdbDriver, repo: DebugRepository):
        """
        参数：
            adb: 用于设备通信的 ADB 驱动。
            repo: 用于持久化的调试仓库。
        """
        self.adb = adb
        self.repo = repo
        # 活跃会话，按 session_id 索引。内存中用于快速访问。
        self.sessions: dict[str, DebugSession] = {}
        # 后台 logcat 收集任务，按 session_id 索引。
        self.logcat_tasks: dict[str, asyncio.Task] = {}

    async def create_session(self, device_id: str, user_id: str) -> DebugSession:
        """
        创建新的调试会话并启动 logcat 收集。

        会话 ID 生成为 "{device_id}_{user_id}_{timestamp}"。
        启动后台 asyncio 任务持续从设备收集 logcat 输出。

        参数：
            device_id: 目标设备的 ADB 序列号。
            user_id: 创建会话的用户标识。

        返回：
            新创建的 DebugSession。
        """
        logger.info("creating_debug_session", device=device_id, user=user_id)
        session_id = f"{device_id}_{user_id}_{int(time.time())}"
        session = DebugSession(
            id=session_id,
            device_id=device_id,
            user_id=user_id,
        )
        self.sessions[session_id] = session
        await self.repo.save_session(session)

        # 启动后台 logcat 收集
        self.logcat_tasks[session_id] = asyncio.create_task(
            self._collect_logcat(session)
        )

        return session

    async def get_session(self, session_id: str) -> Optional[DebugSession]:
        """
        通过 ID 检索调试会话。

        先检查内存字典（快速），然后回退到数据库
        （用于在上一个进程生命周期中创建的会话）。

        参数：
            session_id: 唯一的会话标识符。

        返回：
            找到则返回 DebugSession，否则返回 None。
        """
        return self.sessions.get(session_id) or await self.repo.get_session(session_id)

    async def close_session(self, session_id: str):
        """
        关闭调试会话并停止 logcat 收集。

        取消后台 logcat 任务并从内存字典中移除会话。
        数据库记录被保留用于历史查询。

        参数：
            session_id: 要关闭的会话。
        """
        logger.info("closing_debug_session", session=session_id)
        if task := self.logcat_tasks.pop(session_id, None):
            task.cancel()
        self.sessions.pop(session_id, None)

    async def _collect_logcat(self, session: DebugSession):
        """
        后台任务：持续流式传输 logcat 并缓冲条目。

        运行直到被取消（当会话关闭时）。每行日志被：
            1. 解析为 LogEntry
            2. 追加到内存环形缓冲区（最多 50K 条）
            3. 持久化到数据库用于历史查询
            4. 更新会话的 last_active 时间戳

        参数：
            session: 要收集日志的调试会话。
        """
        try:
            async for line in self.adb.stream_logcat(session.device_id):
                entry = self._parse_logcat_line(line)
                session.log_buffer.append(entry.__dict__)
                # 环形缓冲区淘汰：超过容量时移除最旧的
                if len(session.log_buffer) > self.MAX_LOG_BUFFER:
                    session.log_buffer.pop(0)
                session.touch()
                await self.repo.save_log(session.id, entry)
        except asyncio.CancelledError:
            logger.info("logcat_collection_cancelled", session=session.id)
        except Exception as e:
            logger.error("logcat_collection_error", session=session.id, error=str(e))

    def _parse_logcat_line(self, line: str) -> LogEntry:
        """
        解析 "threadtime" 格式的 logcat 行。

        预期格式：
            "MM-DD HH:MM:SS.mmm  PID  TID LEVEL TAG: message"
        示例：
            "09-08 14:23:45.678  1234  5678 I ActivityManager: Starting activity..."

        如果行不符合预期格式（如格式错误或二进制垃圾），
        返回一个尽力而为的 LogEntry，将原始行作为消息。

        参数：
            line: 原始 logcat 输出行。

        返回：
            解析后的 LogEntry。
        """
        parts = line.strip().split(None, 6)
        if len(parts) < 6:
            # 格式错误的行——返回原始内容
            return LogEntry(
                ts=time.time(), level="I", pid=0, tid=0, tag="", message=line, raw=line
            )
        return LogEntry(
            ts=time.time(),
            level=parts[2] if len(parts[2]) == 1 else "I",
            pid=int(parts[3]) if parts[3].isdigit() else 0,
            tid=int(parts[4]) if parts[4].isdigit() else 0,
            tag=parts[5].rstrip(":"),
            message=parts[6] if len(parts) > 6 else "",
            raw=line,
        )

    async def get_logs(
        self,
        session_id: str,
        level: Optional[str] = None,
        tag: Optional[str] = None,
        limit: int = 1000,
    ) -> list[dict]:
        """
        带可选过滤的日志条目查询。

        当会话活跃时使用内存缓冲区（快速路径），
        对已关闭/历史会话回退到数据库（慢速路径）。

        参数：
            session_id: 要查询的会话。
            level: 按日志级别过滤（V/D/I/W/E/F）。None = 所有级别。
            tag: 按标签子串过滤。None = 所有标签。
            limit: 最大返回条目数。

        返回：
            日志条目字典列表（键：ts, level, pid, tid, tag, message, raw）。
        """
        session = self.sessions.get(session_id)
        if session:
            # 快速路径：从内存缓冲区过滤
            logs = session.log_buffer
            if level:
                logs = [log for log in logs if log.get("level") == level]
            if tag:
                logs = [log for log in logs if tag in log.get("tag", "")]
            return logs[-limit:]
        else:
            # 慢速路径：从数据库查询
            filter_obj = LogFilter(level=level, tag=tag, limit=limit)
            entries = await self.repo.query_logs(session_id, filter_obj)
            return [e.__dict__ for e in entries]

    async def exec_shell(self, session_id: str, cmd: str) -> str:
        """
        在设备上执行 shell 命令并记录到历史。

        参数：
            session_id: 活跃的调试会话。
            cmd: 要执行的 shell 命令。

        返回：
            命令输出（stdout）。

        异常：
            ValueError: 如果找不到 session_id。
            AdbError: 如果 ADB 命令失败。
        """
        session = self.sessions.get(session_id)
        if not session:
            raise ValueError(f"Session not found: {session_id}")

        output = await self.adb.shell(session.device_id, cmd)
        await self.repo.save_shell_history(session_id, cmd, output)
        session.shell_history.append(cmd)
        session.touch()
        return output
