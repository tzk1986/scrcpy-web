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
    - 按 db_path 共享的 aiosqlite 连接池（DatabasePool/get_pool），
      连接循环复用；PRAGMA WAL + busy_timeout 提升并发读写。
    - 表在启动时通过 init_db() 创建（从 lifespan.py 调用）。
    - DB 文件路径从配置读取（settings().database.path）。
    - 元数据存储为 JSON 文本（sqlite 没有原生的 JSON 类型）。
    - 日志在 (session_id, ts) 上建立索引以提高范围查询效率。

线程安全：
    aiosqlite 在后台线程中运行 SQLite，所以 async 调用对于
    FastAPI 的 async 事件循环是安全的。但是，来自多个协程的并发写入
    可能导致 SQLite 锁争用——对于预期的写入量来说是可以接受的。
"""

import asyncio
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite

from app.core.config import settings
from app.core.logging import get_logger
from app.domain.device import DeviceInfo
from app.domain.ports import DebugRepository, DeviceRepository, LogEntry, LogFilter
from app.domain.session import DebugSession

logger = get_logger(__name__)


class DatabasePool:
    """aiosqlite 连接池：initialize 后池内恒有 pool_size 条连接循环复用。"""

    def __init__(self, db_path: str, pool_size: int = 5):
        self.db_path = db_path
        self.pool_size = max(1, pool_size)
        self._pool: asyncio.Queue[aiosqlite.Connection] = asyncio.Queue(maxsize=self.pool_size)
        self._initialized = False

    async def initialize(self):
        if self._initialized:
            return
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        for _ in range(self.pool_size):
            conn = await aiosqlite.connect(self.db_path)
            # WAL 提升读写并发；busy_timeout 防瞬时锁冲突报错
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.execute("PRAGMA busy_timeout=5000")
            await self._pool.put(conn)
        self._initialized = True

    @asynccontextmanager
    async def connection(self):
        if not self._initialized:
            await self.initialize()
        conn = await self._pool.get()
        try:
            yield conn
        finally:
            await self._pool.put(conn)

    async def close(self):
        while not self._pool.empty():
            conn = await self._pool.get()
            await conn.close()
        self._initialized = False


_POOLS: dict[str, DatabasePool] = {}


async def get_pool(db_path: str) -> DatabasePool:
    """按 db_path 返回共享连接池（首次调用创建并初始化）。"""
    pool = _POOLS.get(db_path)
    if pool is None:
        pool = DatabasePool(db_path, settings().debug.db_pool_size)
        await pool.initialize()
        _POOLS[db_path] = pool
    return pool


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

    async def close(self):
        """释放本库文件共享的连接池（进程 shutdown 时调用）。"""
        pool = _POOLS.get(self.db_path)
        if pool:
            await pool.close()
            _POOLS.pop(self.db_path, None)

    async def init_db(self):
        """
        创建所有数据库表（如果不存在）。

        在应用启动时调用一次（lifespan.py → init_db()）。
        使用 CREATE TABLE IF NOT EXISTS 所以是幂等的。
        """
        pool = await get_pool(self.db_path)
        async with pool.connection() as conn:
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
            # seq 列迁移（旧库兼容；无此列则 ALTER 补齐，历史数据不丢）
            cursor = await conn.execute("PRAGMA table_info(debug_logs)")
            cols = [row[1] for row in await cursor.fetchall()]
            if "seq" not in cols:
                await conn.execute("ALTER TABLE debug_logs ADD COLUMN seq INTEGER")
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_logs_session_seq "
                "ON debug_logs(session_id, seq)"
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
        pool = await get_pool(self.db_path)
        async with pool.connection() as conn:
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
        pool = await get_pool(self.db_path)
        async with pool.connection() as conn:
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

    async def save_log(self, session_id: str, entry: LogEntry, seq: int = 0):
        """
        持久化单条日志条目。

        参数：
            session_id: 此日志所属的会话。
            entry: 要持久化的解析后日志条目。
            seq: 会话内单调递增序列号（断线续传游标）。
        """
        pool = await get_pool(self.db_path)
        async with pool.connection() as conn:
            await conn.execute(
                "INSERT INTO debug_logs VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    session_id,
                    entry.ts,
                    entry.level,
                    entry.pid,
                    entry.tid,
                    entry.tag,
                    entry.message,
                    entry.raw,
                    seq,
                ),
            )
            await conn.commit()

    async def list_recent_sessions(self, since_ts: float, limit: int = 20) -> list[DebugSession]:
        """列出 last_active >= since_ts 的会话（重启恢复用），按最近活跃优先。"""
        pool = await get_pool(self.db_path)
        async with pool.connection() as conn:
            cursor = await conn.execute(
                "SELECT id, device_id, user_id, created_at, last_active, metadata "
                "FROM debug_sessions WHERE last_active >= ? "
                "ORDER BY last_active DESC LIMIT ?",
                (since_ts, limit))
            rows = await cursor.fetchall()
        out = []
        for r in rows:
            meta = json.loads(r[5]) if r[5] else {}
            out.append(DebugSession(id=r[0], device_id=r[1], user_id=r[2],
                                    created_at=r[3], last_active=r[4], metadata=meta))
        return out

    async def save_logs_bulk(self, rows: list[tuple]):
        """批量插入日志行，单事务提交。rows 元素为 9 元组（与 debug_logs 列一致）。"""
        if not rows:
            return
        pool = await get_pool(self.db_path)
        async with pool.connection() as conn:
            await conn.executemany(
                "INSERT INTO debug_logs VALUES (?,?,?,?,?,?,?,?,?)", rows)
            await conn.commit()

    async def next_seq(self, session_id: str) -> int:
        """该会话下一条日志应使用的 seq（max(seq)+1，无记录为 0）。"""
        pool = await get_pool(self.db_path)
        async with pool.connection() as conn:
            cursor = await conn.execute(
                "SELECT COALESCE(MAX(seq), -1) + 1 FROM debug_logs WHERE session_id=?",
                (session_id,),
            )
            row = await cursor.fetchone()
        return row[0] if row else 0

    async def query_logs_since(self, session_id: str, from_seq: int, limit: int = 1000) -> list[dict]:
        """按 seq 增量查询（断线续传补发用），返回 dict 列表（含 seq），seq 升序。"""
        pool = await get_pool(self.db_path)
        async with pool.connection() as conn:
            cursor = await conn.execute(
                "SELECT ts, level, pid, tid, tag, message, raw, seq FROM debug_logs "
                "WHERE session_id=? AND seq>=? ORDER BY seq ASC LIMIT ?",
                (session_id, from_seq, limit))
            rows = await cursor.fetchall()
        return [
            {"ts": r[0], "level": r[1], "pid": r[2], "tid": r[3],
             "tag": r[4], "message": r[5], "raw": r[6], "seq": r[7]}
            for r in rows
        ]

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

        pool = await get_pool(self.db_path)
        async with pool.connection() as conn:
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
        pool = await get_pool(self.db_path)
        async with pool.connection() as conn:
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
        pool = await get_pool(self.db_path)
        async with pool.connection() as conn:
            cursor = await conn.execute(
                "SELECT command, output FROM shell_history WHERE session_id=? ORDER BY ts DESC LIMIT ?",
                (session_id, limit),
            )
            rows = await cursor.fetchall()
        return [(row[0], row[1]) for row in reversed(rows)]

    async def delete_old_logs(self, retention_seconds: float) -> int:
        """
        删除超过保留期的日志。

        参数：
            retention_seconds: 保留时间（秒）。早于此时间的日志将被删除。

        返回：
            删除的日志条数。
        """
        cutoff_time = time.time() - retention_seconds
        pool = await get_pool(self.db_path)
        async with pool.connection() as conn:
            cursor = await conn.execute(
                "DELETE FROM debug_logs WHERE ts < ?", (cutoff_time,)
            )
            await conn.commit()
            deleted = cursor.rowcount
        if deleted > 0:
            logger.info("old_logs_deleted", count=deleted, cutoff_time=cutoff_time)
        return deleted

    async def delete_old_shell_history(self, retention_seconds: float) -> int:
        """
        删除超过保留期的 shell 历史。

        参数：
            retention_seconds: 保留时间（秒）。

        返回：
            删除的记录数。
        """
        cutoff_time = time.time() - retention_seconds
        pool = await get_pool(self.db_path)
        async with pool.connection() as conn:
            cursor = await conn.execute(
                "DELETE FROM shell_history WHERE ts < ?", (cutoff_time,)
            )
            await conn.commit()
            deleted = cursor.rowcount
        if deleted > 0:
            logger.info("old_shell_history_deleted", count=deleted)
        return deleted

    async def delete_session_logs(self, session_id: str) -> int:
        """
        删除指定会话的所有日志。

        参数：
            session_id: 要清理的会话 ID。

        返回：
            删除的日志条数。
        """
        pool = await get_pool(self.db_path)
        async with pool.connection() as conn:
            cursor = await conn.execute(
                "DELETE FROM debug_logs WHERE session_id=?", (session_id,)
            )
            await conn.execute(
                "DELETE FROM shell_history WHERE session_id=?", (session_id,)
            )
            await conn.commit()
            deleted = cursor.rowcount
        if deleted > 0:
            logger.info("session_logs_deleted", session=session_id, count=deleted)
        return deleted

    async def get_db_size_bytes(self) -> int:
        """
        获取数据库文件大小（字节）。

        返回：
            文件大小（字节）。
        """
        try:
            return Path(self.db_path).stat().st_size
        except FileNotFoundError:
            return 0

    async def trim_logs_to_db_size(self, max_size_bytes: int) -> int:
        """
        当数据库超过指定大小时，删除最旧的日志直到低于限制。

        参数：
            max_size_bytes: 最大数据库大小（字节）。

        返回：
            删除的日志条数。
        """
        current_size = await self.get_db_size_bytes()
        if current_size <= max_size_bytes:
            return 0

        total_deleted = 0
        # 批量删除，每次删除 1000 条
        batch_size = 1000
        pool = await get_pool(self.db_path)
        async with pool.connection() as conn:
            while True:
                current_size = await self.get_db_size_bytes()
                if current_size <= max_size_bytes:
                    break

                # 获取最旧的 1000 条日志的时间戳
                cursor = await conn.execute(
                    "SELECT ts FROM debug_logs ORDER BY ts ASC LIMIT ?", (batch_size,)
                )
                rows = await cursor.fetchall()
                if not rows:
                    break

                # 删除这些日志
                max_ts = rows[-1][0]
                cursor = await conn.execute(
                    "DELETE FROM debug_logs WHERE ts <= ?", (max_ts,)
                )
                await conn.commit()
                total_deleted += cursor.rowcount

        if total_deleted > 0:
            logger.info(
                "logs_trimmed_to_size",
                deleted=total_deleted,
                max_size_mb=max_size_bytes / (1024 * 1024),
            )
        return total_deleted

    async def vacuum(self):
        """
        执行 SQLite VACUUM 命令回收未使用的空间。

        注意：这会重写整个数据库文件，可能需要较长时间。
        """
        pool = await get_pool(self.db_path)
        async with pool.connection() as conn:
            await conn.execute("VACUUM")
            await conn.commit()
        logger.info("database_vacuumed")


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

    async def close(self):
        """释放本库文件共享的连接池（进程 shutdown 时调用）。"""
        pool = _POOLS.get(self.db_path)
        if pool:
            await pool.close()
            _POOLS.pop(self.db_path, None)

    async def init_db(self):
        """创建 devices 表（如果不存在）。"""
        pool = await get_pool(self.db_path)
        async with pool.connection() as conn:
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
        pool = await get_pool(self.db_path)
        async with pool.connection() as conn:
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
        pool = await get_pool(self.db_path)
        async with pool.connection() as conn:
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
        pool = await get_pool(self.db_path)
        async with pool.connection() as conn:
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
        pool = await get_pool(self.db_path)
        async with pool.connection() as conn:
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
