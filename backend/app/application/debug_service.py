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

from fastapi import WebSocket

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
        # WebSocket 订阅者，按 session_id 索引。
        # 每个订阅者有自己的过滤条件：{session_id: {ws: {"level": ..., "tag": ...}}}
        self.subscribers: dict[str, dict[WebSocket, dict]] = {}
        # 定期清理任务
        self.cleanup_task: asyncio.Task | None = None
        # 交互式 shell 会话，按 session_id 索引（PTY 模式）
        self.shell_sessions: dict[str, "ShellSession"] = {}
        # Shell 输出转发任务，按 session_id 索引
        self.shell_output_forwarding_tasks: dict[str, asyncio.Task] = {}

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
        同时关闭交互式 shell 会话。
        数据库记录被保留用于历史查询。

        参数：
            session_id: 要关闭的会话。
        """
        logger.info("closing_debug_session", session=session_id)
        if task := self.logcat_tasks.pop(session_id, None):
            task.cancel()
        # 停止 shell 输出转发任务
        if fwd_task := self.shell_output_forwarding_tasks.pop(session_id, None):
            fwd_task.cancel()
        # 关闭交互式 shell
        if shell := self.shell_sessions.pop(session_id, None):
            try:
                await shell.stop()
            except Exception as e:
                logger.warning("shell_stop_failed", session=session_id, error=str(e))
        self.sessions.pop(session_id, None)
        # 清理订阅者
        if session_id in self.subscribers:
            # 通知订阅者会话已关闭
            for ws in list(self.subscribers[session_id].keys()):
                try:
                    await ws.send_json({"type": "session_closed"})
                except Exception:
                    pass
            del self.subscribers[session_id]

    async def _collect_logcat(self, session: DebugSession):
        """
        后台任务：持续流式传输 logcat 并缓冲条目。

        运行直到被取消（当会话关闭时）。每行日志被：
            1. 解析为 LogEntry
            2. 根据配置过滤（最低级别、速率限制）
            3. 追加到内存环形缓冲区（最多 50K 条）
            4. 持久化到数据库用于历史查询
            5. 更新会话的 last_active 时间戳
            6. 推送给所有 WebSocket 订阅者

        智能过滤策略：
            - 只保留 >= min_log_level 的日志
            - 当日志速率超过 rate_limit 时，优先丢弃 V/D 级别
            - W/E/F 级别始终保留

        参数：
            session: 要收集日志的调试会话。
        """
        from app.core.config import settings as get_settings
        settings = get_settings()

        # 日志级别优先级（数字越大越重要）
        level_priority = {"V": 0, "D": 1, "I": 2, "W": 3, "E": 4, "F": 5}
        min_level = settings.debug.min_log_level
        min_priority = level_priority.get(min_level, 2)  # 默认 I

        # 速率限制
        rate_limit = settings.debug.log_rate_limit
        log_window_start = time.time()
        log_count_in_window = 0

        try:
            async for line in self.adb.stream_logcat(session.device_id):
                entry = self._parse_logcat_line(line)
                entry_priority = level_priority.get(entry.level, 2)

                # 策略 1: 过滤掉低于最低级别的日志
                if entry_priority < min_priority:
                    continue

                # 策略 2: 速率限制 - 超过阈值时丢弃低级别日志
                current_time = time.time()
                if current_time - log_window_start >= 1.0:
                    # 重置窗口
                    log_window_start = current_time
                    log_count_in_window = 0
                else:
                    log_count_in_window += 1
                    if log_count_in_window > rate_limit:
                        # 超过速率限制，只保留 W/E/F
                        if entry_priority < 3:  # W=3
                            continue

                # 通过过滤，存储和推送
                session.log_buffer.append(entry.__dict__)
                # 环形缓冲区淘汰：超过容量时移除最旧的
                if len(session.log_buffer) > self.MAX_LOG_BUFFER:
                    session.log_buffer.pop(0)
                session.touch()
                await self.repo.save_log(session.id, entry)

                # 推送给 WebSocket 订阅者
                await self._notify_subscribers(session.id, entry.__dict__)
        except asyncio.CancelledError:
            logger.info("logcat_collection_cancelled", session=session.id)
        except Exception as e:
            logger.error("logcat_collection_error", session=session.id, error=str(e))

    async def subscribe(self, session_id: str, websocket: WebSocket):
        """
        订阅会话的实时日志推送。

        参数：
            session_id: 要订阅的调试会话 ID。
            websocket: 客户端 WebSocket 连接。
        """
        if session_id not in self.subscribers:
            self.subscribers[session_id] = {}
        # 新订阅者默认无过滤（接收所有日志）
        self.subscribers[session_id][websocket] = {}
        logger.info("log_subscriber_added", session=session_id, count=len(self.subscribers[session_id]))

    async def unsubscribe(self, session_id: str, websocket: WebSocket):
        """
        取消订阅会话的实时日志推送。

        参数：
            session_id: 要取消订阅的调试会话 ID。
            websocket: 客户端 WebSocket 连接。
        """
        if session_id in self.subscribers:
            self.subscribers[session_id].pop(websocket, None)
            if not self.subscribers[session_id]:
                del self.subscribers[session_id]
            logger.info("log_subscriber_removed", session=session_id, count=len(self.subscribers.get(session_id, {})))

    async def set_subscriber_filter(
        self,
        session_id: str,
        websocket: WebSocket,
        level: Optional[str] = None,
        tag: Optional[str] = None,
    ):
        """
        设置订阅者的日志过滤条件。

        只有满足过滤条件的日志才会推送给该订阅者。
        这减少了网络流量和客户端处理开销。

        参数：
            session_id: 调试会话 ID。
            websocket: 客户端 WebSocket 连接。
            level: 按日志级别过滤（V/D/I/W/E/F）。None = 所有级别。
            tag: 按标签子串过滤。None = 所有标签。
        """
        if session_id in self.subscribers and websocket in self.subscribers[session_id]:
            self.subscribers[session_id][websocket] = {"level": level, "tag": tag}
            logger.info(
                "subscriber_filter_set",
                session=session_id,
                level=level,
                tag=tag,
            )

    async def _notify_subscribers(self, session_id: str, entry: dict):
        """
        向所有订阅者推送新日志条目（根据各自的过滤条件）。

        参数：
            session_id: 调试会话 ID。
            entry: 日志条目字典。
        """
        if session_id not in self.subscribers:
            return

        dead_connections = set()
        for ws, filter_opts in self.subscribers[session_id].items():
            # 检查过滤条件
            filter_level = filter_opts.get("level")
            filter_tag = filter_opts.get("tag")

            if filter_level and entry.get("level") != filter_level:
                continue
            if filter_tag and filter_tag not in entry.get("tag", ""):
                continue

            try:
                await ws.send_json({"type": "log", "entry": entry})
            except Exception as e:
                logger.warning("failed_to_send_log", error=str(e))
                dead_connections.add(ws)

        # 清理断开的连接
        for ws in dead_connections:
            self.subscribers[session_id].pop(ws, None)
        if not self.subscribers[session_id]:
            del self.subscribers[session_id]

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

    async def get_or_create_shell(self, session_id: str) -> "ShellSession":
        """
        获取或创建交互式 shell 会话。

        如果 shell 已存在且存活，则返回现有实例。
        否则创建新的 shell 会话，并启动输出转发任务。

        参数：
            session_id: 活跃的调试会话。

        返回：
            ShellSession 实例。

        异常：
            ValueError: 如果找不到 session_id。
            AdbError: 如果启动失败。
        """
        if session_id in self.shell_sessions:
            shell = self.shell_sessions[session_id]
            if shell.is_alive:
                return shell
            # Shell 已死亡，移除
            del self.shell_sessions[session_id]

        session = self.sessions.get(session_id)
        if not session:
            raise ValueError(f"Session not found: {session_id}")

        # 创建新 shell
        shell = await self.adb.create_shell(session.device_id)
        self.shell_sessions[session_id] = shell
        logger.info("shell_created", session=session_id, device=session.device_id)

        # 启动输出转发（将 shell 输出通过 WebSocket 发送给客户端）
        self._start_output_forwarding(session_id, shell)

        return shell

    def _start_output_forwarding(self, session_id: str, shell: "ShellSession"):
        """
        启动 shell 输出转发任务。

        首先发送初始输出（设备 prompt），然后后台持续从 shell._output_queue
        读取输出并通过 WebSocket 发送给订阅的客户端。

        当 shell 退出时（get_output() 返回 None），任务自动结束。

        参数：
            session_id: 调试会话 ID。
            shell: InteractiveShell 实例。
        """
        if session_id in self.shell_output_forwarding_tasks:
            task = self.shell_output_forwarding_tasks[session_id]
            if not task.done():
                return  # 已经在运行
            del self.shell_output_forwarding_tasks[session_id]

        async def forward_loop():
            try:
                # 初始输出由 subscribe 处理器直接发送，此处只转发后续输出
                logger.info("shell_output_forwarding_started", session=session_id)
                while True:
                    line = await shell.get_output()
                    if line is None:
                        break  # Shell 退出
                    await self._notify_shell_output(session_id, line)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.warning("shell_output_forwarding_error",
                               session=session_id, error=str(e))

        self.shell_output_forwarding_tasks[session_id] = asyncio.create_task(forward_loop())
        logger.info("shell_output_forwarding_task_created", session=session_id)

    async def _notify_shell_output(self, session_id: str, line: str):
        """
        将 shell 输出发送给所有订阅该会话的 WebSocket 客户端。

        参数：
            session_id: 调试会话 ID。
            line: 输出行。
        """
        if session_id not in self.subscribers:
            return

        dead_connections = set()
        for ws in list(self.subscribers[session_id].keys()):
            try:
                await ws.send_json({"type": "shell_stream", "line": line})
            except Exception as e:
                logger.warning("failed_to_send_shell_output", error=str(e))
                dead_connections.add(ws)

        # 清理断开的连接
        for ws in dead_connections:
            self.subscribers[session_id].pop(ws, None)
        if not self.subscribers[session_id]:
            del self.subscribers[session_id]

    async def stop_output_forwarding(self, session_id: str):
        """
        停止 shell 输出转发任务。

        通常在 WebSocket 断开时调用。

        参数：
            session_id: 调试会话 ID。
        """
        if task := self.shell_output_forwarding_tasks.pop(session_id, None):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            logger.debug("shell_output_forwarding_stopped", session=session_id)

    async def exec_shell_stream(self, session_id: str, cmd: str):
        """
        流式执行 shell 命令（使用 InteractiveShell 的 execute）。

        执行期间，后台读取器将输出路由到 _exec_queue，
        避免与 PTY 输出队列冲突。命令完成后返回汇总结果。

        参数：
            session_id: 活跃的调试会话。
            cmd: 要执行的 shell 命令。

        返回：
            字典 {"output": str, "success": bool}

        异常：
            ValueError: 如果找不到 session_id。
        """
        session = self.sessions.get(session_id)
        if not session:
            raise ValueError(f"Session not found: {session_id}")

        collected_output = []
        success = True
        try:
            # 使用 InteractiveShell（PTY 模式）
            shell = await self.get_or_create_shell(session_id)
            async for line in shell.execute(cmd):
                collected_output.append(line)
        except Exception as e:
            collected_output.append(str(e))
            success = False
        finally:
            # 命令完成后，记录到历史
            full_output = "".join(collected_output)
            await self.repo.save_shell_history(session_id, cmd, full_output)
            session.shell_history.append(cmd)
            session.touch()

        return {"output": full_output, "success": success}

    async def _exec_shell_stream_raw(self, session_id: str, cmd: str):
        """
        流式执行 shell 命令，逐行产出输出（供 WebSocket 使用）。

        与 exec_shell_stream 类似，但以异步迭代器形式逐行产出，
        而不是收集完整输出。WebSocket 处理器使用此方法实时转发输出。

        参数：
            session_id: 活跃的调试会话。
            cmd: 要执行的 shell 命令。

        产出：
            每行输出（字符串）。

        异常：
            ValueError: 如果找不到 session_id。
            ShellExitedError: 如果 shell 退出。
        """
        session = self.sessions.get(session_id)
        if not session:
            raise ValueError(f"Session not found: {session_id}")

        shell = await self.get_or_create_shell(session_id)
        collected_output = []
        try:
            async for line in shell.execute(cmd):
                collected_output.append(line)
                yield line
        finally:
            # 命令完成后，记录到历史
            full_output = "".join(collected_output)
            await self.repo.save_shell_history(session_id, cmd, full_output)
            session.shell_history.append(cmd)
            session.touch()

    # -------------------------------------------------------------------
    # 日志清理
    # -------------------------------------------------------------------

    async def start_cleanup_task(self):
        """
        启动定期日志清理任务。

        从配置中读取清理间隔（默认 1 小时），启动后台 asyncio 任务。
        """
        from app.core.config import settings as get_settings
        settings = get_settings()
        interval_seconds = settings.debug.cleanup_interval_hours * 3600

        if self.cleanup_task and not self.cleanup_task.done():
            logger.warning("cleanup_task_already_running")
            return

        self.cleanup_task = asyncio.create_task(self._cleanup_loop(interval_seconds))
        logger.info("cleanup_task_started", interval_hours=settings.debug.cleanup_interval_hours)

    async def stop_cleanup_task(self):
        """停止定期清理任务。"""
        if self.cleanup_task and not self.cleanup_task.done():
            self.cleanup_task.cancel()
            try:
                await self.cleanup_task
            except asyncio.CancelledError:
                pass
        logger.info("cleanup_task_stopped")

    async def _cleanup_loop(self, interval_seconds: float):
        """
        后台清理循环。

        每隔 interval_seconds 执行一次清理：
          1. 删除超过保留期的日志
          2. 删除超过保留期的 shell 历史
          3. 如果数据库超过大小限制，删除最旧日志
        """
        try:
            while True:
                await asyncio.sleep(interval_seconds)
                await self.run_cleanup()
        except asyncio.CancelledError:
            logger.info("cleanup_loop_cancelled")

    async def run_cleanup(self) -> dict:
        """
        执行一次日志清理。

        清理策略：
          1. 删除超过 log_retention_days 的日志
          2. 删除超过 shell_history_days 的 shell 历史
          3. 如果数据库超过 max_db_size_mb，删除最旧日志直到低于限制

        返回：
            清理统计信息字典。
        """
        from app.core.config import settings as get_settings
        settings = get_settings()

        log_retention_seconds = settings.debug.log_retention_days * 86400
        shell_retention_seconds = settings.debug.shell_history_days * 86400
        max_db_size_bytes = settings.debug.max_db_size_mb * 1024 * 1024

        # 1. 删除超过保留期的日志
        logs_deleted = await self.repo.delete_old_logs(log_retention_seconds)

        # 2. 删除超过保留期的 shell 历史
        shell_deleted = await self.repo.delete_old_shell_history(shell_retention_seconds)

        # 3. 如果数据库超过大小限制，删除最旧日志
        size_trimmed = await self.repo.trim_logs_to_db_size(max_db_size_bytes)

        db_size = await self.repo.get_db_size_bytes()

        result = {
            "logs_deleted": logs_deleted,
            "shell_deleted": shell_deleted,
            "size_trimmed": size_trimmed,
            "db_size_bytes": db_size,
            "db_size_mb": round(db_size / (1024 * 1024), 2),
        }

        logger.info(
            "cleanup_completed",
            **result,
        )
        return result

    async def cleanup_session(self, session_id: str) -> int:
        """
        手动清理指定会话的所有日志。

        参数：
            session_id: 要清理的会话 ID。

        返回：
            删除的日志条数。
        """
        return await self.repo.delete_session_logs(session_id)

    async def get_db_stats(self) -> dict:
        """
        获取数据库统计信息。

        返回：
            包含数据库大小、日志数量等信息的字典。
        """
        db_size = await self.repo.get_db_size_bytes()
        return {
            "db_size_bytes": db_size,
            "db_size_mb": round(db_size / (1024 * 1024), 2),
            "active_sessions": len(self.sessions),
            "total_subscribers": sum(len(s) for s in self.subscribers.values()),
        }
