"""
SQLite 持久化实现
==================

层：基础设施 → 持久化。

使用 aiosqlite 实现异步 SQLite 访问，
实现 DebugRepository 和 DeviceRepository（domain/ports.py）。

数据库模式：
    debug_sessions: 会话元数据（id, device_id, user_id, 时间戳, JSON metadata）
    debug_logs:     日志条目（session_id, ts, level, pid, tid, tag, message, raw）
    shell_history:  命令历史（session_id, ts, command, output）
    devices:        设备信息（id, model, os_version, resolution, battery, status, ip, port）

关键设计决策：
    - 每次操作一个连接（通过 _get_conn()）。这很简单，
      避免了连接池的复杂性。SQLite 的写锁对于单进程部署是可以接受的。
    - 表在启动时通过 init_db() 创建（从 lifespan.py 调用）。
    - DB 文件路径从配置读取（settings().database.path）。
    - 元数据存储为 JSON 文本（sqlite 没有原生的 JSON 类型）。
    - 日志在 (session_id, ts) 上建立索引以提高范围查询效率。

线程安全：
    aiosqlite 在后台线程中运行 SQLite，所以 async 调用对于
    FastAPI 的 async 事件循环是安全的。但是，来自多个协程的并发写入
    可能导致 SQLite 锁争用——对于预期的写入量来说是可以接受的。
"""

import json
import sqlite3
import time
from pathlib import Path

import aiosqlite

from app.core.config import settings
from app.core.logging import get_logger
from app.domain.device import DeviceInfo
from app.domain.ports import DebugRepository, DeviceRepository, LogEntry, LogFilter
from app.domain.session import DebugSession

logger = get_logger(__name__)


class SqliteDebugRepository(DebugRepository):
    """
    DebugRepository 的 SQLite 实现。

    存储调试会话、日志条目和 shell 命令历史。
    """

    def __init__(self, db_path: str | None = None):
        """
        参数：
            db_path: SQLite 文件路径。默认使用配置值。
        """
        self.db_path = db_path or settings().database.path

    async def _get_conn(self) -> aiosqlite.Connection:
        """
        打开新的 aiosqlite 连接。

        如果父目录不存在则创建。

        返回：
            打开的 aiosqlite 连接（调用方负责关闭）。
        """
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        return await aiosqlite.connect(self.db_path)

    async def init_db(self):
        """
        创建所有数据库表（如果不存在）。

        在应用启动时调用一次（lifespan.py → init_db()）。
        使用 CREATE TABLE IF NOT EXISTS 所以是幂等的。
        """
        async with await self._get_conn() as conn:
            await conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS debug_sessions (
                    id TEXT PRIMARY KEY,
                    device_id TEXT,
                    user_id TEXT,
                    created_at REAL,
                    last_active REAL,
                    metadata TEXT
                );
                CREATE TABLE IF NOT EXISTS debug_logs (
                    session_id TEXT,
                    ts REAL,
                    level TEXT,
                    pid INTEGER,
                    tid INTEGER,
                    tag TEXT,
                    message TEXT,
                    raw TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_logs_session_ts ON debug_logs(session_id, ts);
                CREATE TABLE IF NOT EXISTS shell_history (
                    session_id TEXT,
                    ts REAL,
                    command TEXT,
                    output TEXT
                );
            """
            )
            await conn.commit()

    async def save_session(self, session: DebugSession):
        """
        插入或替换调试会话。

        使用 INSERT OR REPLACE（upsert 语义）。元数据字典
        被序列化为 JSON 文本。

        参数：
            session: 要持久化的 DebugSession。
        """
        async with await self._get_conn() as conn:
            await conn.execute(
                "INSERT OR REPLACE INTO debug_sessions VALUES (?,?,?,?,?,?)",
                (
                    session.id,
                    session.device_id,
                    session.user_id,
                    session.created_at,
                    session.last_active,
                    json.dumps(session.metadata),
                ),
            )
            await conn.commit()

    async def get_session(self, session_id: str) -> DebugSession | None:
        """
        按 ID 检索调试会话。

        参数：
            session_id: 唯一的会话标识符。

        返回：
            找到则返回 DebugSession，否则返回 None。
        """
        async with await self._get_conn() as conn:
            cursor = await conn.execute(
                "SELECT * FROM debug_sessions WHERE id=?", (session_id,)
            )
            row = await cursor.fetchone()
            if not row:
                return None
            return DebugSession(
                id=row[0],
                device_id=row[1],
                user_id=row[2],
                created_at=row[3],
                last_active=row[4],
                metadata=json.loads(row[5]) if row[5] else {},
            )

    async def save_log(self, session_id: str, entry: LogEntry):
        """
        持久化单条日志条目。

        参数：
            session_id: 此日志所属的会话。
            entry: 要持久化的解析后日志条目。
        """
        async with await self._get_conn() as conn:
            await conn.execute(
                "INSERT INTO debug_logs VALUES (?,?,?,?,?,?,?,?)",
                (
                    session_id,
                    entry.ts,
                    entry.level,
                    entry.pid,
                    entry.tid,
                    entry.tag,
                    entry.message,
                    entry.raw,
                ),
            )
            await conn.commit()

    async def query_logs(self, session_id: str, filter: LogFilter) -> list[LogEntry]:
        """
        带动态过滤的日志条目查询。

        根据设置了哪些过滤字段动态构建 SQL WHERE 子句。
        结果按时间戳 DESC 排序（最新在前）并限制数量，
        然后反转以返回最旧在前的顺序。

        参数：
            session_id: 要查询的会话。
            filter: 过滤条件（level、tag、pid、search、limit）。

        返回：
            匹配过滤条件的 LogEntry 对象列表，最旧在前。
        """
        sql = "SELECT * FROM debug_logs WHERE session_id=?"
        params: list = [session_id]

        if filter.level:
            sql += " AND level=?"
            params.append(filter.level)
        if filter.tag:
            sql += " AND tag LIKE ?"
            params.append(f"%{filter.tag}%")
        if filter.pid:
            sql += " AND pid=?"
            params.append(filter.pid)

        sql += " ORDER BY ts DESC LIMIT ?"
        params.append(filter.limit)

        async with await self._get_conn() as conn:
            cursor = await conn.execute(sql, params)
            rows = await cursor.fetchall()

        # 反转以返回最旧在前（查询获取最新在前以用于 LIMIT）
        return [
            LogEntry(
                ts=row[1],
                level=row[2],
                pid=row[3],
                tid=row[4],
                tag=row[5],
                message=row[6],
                raw=row[7],
            )
            for row in reversed(rows)
        ]

    async def save_shell_history(self, session_id: str, command: str, output: str):
        """
        记录 shell 命令及其输出。

        参数：
            session_id: 执行命令的会话。
            command: shell 命令字符串。
            output: 命令的 stdout/stderr 输出。
        """
        async with await self._get_conn() as conn:
            await conn.execute(
                "INSERT INTO shell_history VALUES (?,?,?,?)",
                (session_id, time.time(), command, output),
            )
            await conn.commit()

    async def get_shell_history(
        self, session_id: str, limit: int = 100
    ) -> list[tuple[str, str]]:
        """
        检索 shell 命令历史（最新在前，然后反转）。

        参数：
            session_id: 要查询的会话。
            limit: 最大返回条目数。

        返回：
            (命令, 输出) 元组列表，最旧在前。
        """
        async with await self._get_conn() as conn:
            cursor = await conn.execute(
                "SELECT command, output FROM shell_history WHERE session_id=? ORDER BY ts DESC LIMIT ?",
                (session_id, limit),
            )
            rows = await cursor.fetchall()
        return [(row[0], row[1]) for row in reversed(rows)]


class SqliteDeviceRepository(DeviceRepository):
    """
    DeviceRepository 的 SQLite 实现。

    存储设备信息（型号、OS 版本、分辨率等）。
    """

    def __init__(self, db_path: str | None = None):
        """
        参数：
            db_path: SQLite 文件路径。默认使用配置值。
        """
        self.db_path = db_path or settings().database.path

    async def _get_conn(self) -> aiosqlite.Connection:
        """打开新的 aiosqlite 连接（创建父目录）。"""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        return await aiosqlite.connect(self.db_path)

    async def init_db(self):
        """创建 devices 表（如果不存在）。"""
        async with await self._get_conn() as conn:
            await conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS devices (
                    id TEXT PRIMARY KEY,
                    model TEXT,
                    os_version TEXT,
                    resolution_w INTEGER,
                    resolution_h INTEGER,
                    battery INTEGER,
                    status TEXT,
                    ip TEXT,
                    port INTEGER
                );
            """
            )
            await conn.commit()

    async def save(self, device: DeviceInfo):
        """
        插入或替换设备信息（upsert）。

        分辨率元组存储为两个独立的 INTEGER 列。

        参数：
            device: 要持久化的 DeviceInfo。
        """
        async with await self._get_conn() as conn:
            await conn.execute(
                "INSERT OR REPLACE INTO devices VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    device.id,
                    device.model,
                    device.os_version,
                    device.resolution[0],
                    device.resolution[1],
                    device.battery,
                    device.status,
                    device.ip,
                    device.port,
                ),
            )
            await conn.commit()

    async def get(self, device_id: str) -> DeviceInfo | None:
        """
        按 ADB 序列号检索设备。

        参数：
            device_id: ADB 序列号。

        返回：
            找到则返回 DeviceInfo，否则返回 None。
        """
        async with await self._get_conn() as conn:
            cursor = await conn.execute("SELECT * FROM devices WHERE id=?", (device_id,))
            row = await cursor.fetchone()
            if not row:
                return None
            return DeviceInfo(
                id=row[0],
                model=row[1],
                os_version=row[2],
                resolution=(row[3], row[4]),
                battery=row[5],
                status=row[6],
                ip=row[7],
                port=row[8],
            )

    async def list_all(self) -> list[DeviceInfo]:
        """
        列出所有已知设备。

        返回：
            数据库中所有 DeviceInfo 对象的列表。
        """
        async with await self._get_conn() as conn:
            cursor = await conn.execute("SELECT * FROM devices")
            rows = await cursor.fetchall()
        return [
            DeviceInfo(
                id=row[0],
                model=row[1],
                os_version=row[2],
                resolution=(row[3], row[4]),
                battery=row[5],
                status=row[6],
                ip=row[7],
                port=row[8],
            )
            for row in rows
        ]

    async def delete(self, device_id: str):
        """
        从数据库中删除设备。

        参数：
            device_id: 要删除的设备的 ADB 序列号。
        """
        async with await self._get_conn() as conn:
            await conn.execute("DELETE FROM devices WHERE id=?", (device_id,))
            await conn.commit()


async def init_db():
    """
    初始化所有数据库表。

    在应用启动时从 lifespan.py 调用一次。
    为调试数据和设备数据创建表。
    """
    debug_repo = SqliteDebugRepository()
    await debug_repo.init_db()
    device_repo = SqliteDeviceRepository()
    await device_repo.init_db()
    logger.info("database_initialized", path=settings().database.path)
