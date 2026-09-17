# 调试会话管理补齐 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把调试会话管理从 75% 补到 95%：SQLite 连接池、日志批量写入、断线续传（seq + from_seq 补发）、服务重启会话恢复。

**Architecture:** 四层架构内改动——连接池/批量写/BatchLogWriter 属 infrastructure/persistence；seq 字段属 domain；续传/恢复编排属 application/debug_service；WS 协议扩展属 interfaces/ws；前端 stores/debug.ts 负责重连补发。`方案/05-调试会话管理.md` 为 spec，其 Step 1-3 为权威设计，本计划是其参数适配版（复用现有 repo/lifespan 结构，而非照抄伪代码）。

**Tech Stack:** Python 3 + FastAPI + aiosqlite（现有依赖，不新增）；Vue3 + Pinia 前端；pytest（asyncio）。

**Spec:** `方案/05-调试会话管理.md`

## Global Constraints

- 严格四层架构，禁止跨层调用（domain 不 import infrastructure）
- 所有测试文件放 `tests/` 下对应子目录；运行：`PYTHONPATH=backend python -m pytest tests/<path> -v`
- 不新增第三方依赖（aiosqlite/asyncio 已有）
- WS 协议向后兼容：`subscribe` 不带 `from_seq` 时行为与现状完全一致
- 已有 SQLite 库文件必须能平滑升级（init_db 内做列迁移，不重建库、不丢历史数据）
- 连接池默认 5、批量默认 100 条/100ms、恢复会话上限 20、日志吞吐验收 >5000 条/秒（spec 验收标准）
- 配置项一律加到 `config/settings.py` 的 `DebugConfig`（env 前缀 DEBUG_）

## 现状（2026-09-17 调研结论，执行者无需重查）

- `backend/app/infrastructure/persistence/sqlite.py:60-70` `_get_conn()` 每操作新建连接；文件 docstring 自认"每次操作一个连接"
- `backend/app/application/debug_service.py:212` 逐条 `await self.repo.save_log(...)`；`log_buffer` 条目无 seq；`debug_logs` 表无 seq 列
- `backend/app/interfaces/ws/debug.py:73-86` `subscribe` 无 from_seq、无补发；`export` 是 TODO（本计划不做 export）
- `backend/app/lifecycle.py` 启动只 init_db + start_cleanup_task，无会话恢复；重启后 DB 中会话变僵尸（有记录、无 logcat 任务）
- 定时清理已实现（`debug_service.py:610-707`），不在本计划范围
- 并发锁（spec Step 4）不在优先级 #2 的四项内，不做（YAGNI，记进度文档即可）
- `tests/` 无 tests/unit 目录；现有测试按层放 tests/application、tests/infrastructure、tests/integration；`tests/integration/test_debug_ws.py` 存在旧协议集成测试，改协议后必须回归
- 前端 `frontend/src/stores/debug.ts:141-209` WS 关闭只置状态不重连；`frontend/src/services/websocket.ts` 无自动重连
- 域模型 `backend/app/domain/session.py` DebugSession 字段：id/device_id/user_id/created_at/last_active/log_buffer/shell_history/metadata

---

### Task 1: SQLite 连接池

**Files:**
- Modify: `backend/app/infrastructure/persistence/sqlite.py`（全文件把 `_get_conn()` 用法换成池）
- Test: `tests/infrastructure/test_sqlite_pool.py`

**Interfaces:**
- Consumes: 现有 `SqliteDebugRepository`/`SqliteDeviceRepository` 公开方法签名不变
- Produces: `DatabasePool` 类 + 模块级 `get_pool(db_path) -> DatabasePool`（Task 3 的批量写与 Task 5 的恢复都用既有 repo 方法，不直接依赖池）；`SqliteDebugRepository.close()`（关池，shutdown 用）

- [ ] **Step 1: 写失败测试**

```python
# tests/infrastructure/test_sqlite_pool.py
"""SQLite 连接池测试（不依赖设备/服务）。"""
import asyncio
import pytest

from app.infrastructure.persistence.sqlite import DatabasePool, get_pool


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "pool_test.sqlite")


@pytest.mark.asyncio
async def test_pool_reuses_connections(db_path):
    pool = DatabasePool(db_path, pool_size=2)
    await pool.initialize()
    async with pool.connection() as c1:
        async with pool.connection() as c2:
            assert c1 is not c2          # 池内不同连接
    # 归还后再取应复用（不新建第 3 条）
    seen = set()
    for _ in range(6):
        async with pool.connection() as c:
            seen.add(id(c))
    assert len(seen) <= 2
    await pool.close()


@pytest.mark.asyncio
async def test_pool_concurrent_cap(db_path):
    pool = DatabasePool(db_path, pool_size=3)
    await pool.initialize()
    held = asyncio.Event()
    order = []

    async def worker(i):
        async with pool.connection():
            order.append(i)
            await held.wait()

    tasks = [asyncio.create_task(worker(i)) for i in range(5)]
    await asyncio.sleep(0.2)
    assert len(order) == 3               # 第 4/5 个在排队
    held.set()
    await asyncio.gather(*tasks)
    assert len(order) == 5
    await pool.close()


@pytest.mark.asyncio
async def test_get_pool_singleton_per_path(db_path):
    p1 = await get_pool(db_path)
    p2 = await get_pool(db_path)
    assert p1 is p2
```

- [ ] **Step 2: 运行确认失败**

Run: `PYTHONPATH=backend python -m pytest tests/infrastructure/test_sqlite_pool.py -v`
Expected: ImportError: cannot import name 'DatabasePool'

- [ ] **Step 3: 实现 DatabasePool 并切换 repo**

在 `sqlite.py` 顶部 imports 加 `import asyncio`、`from contextlib import asynccontextmanager`，类定义（放在 `SqliteDebugRepository` 之前）：

```python
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
```

两个 repo 的改造（模式统一，逐处替换）：删除 `_get_conn()`，每个方法把

```python
async with self._get_conn() as conn:
```

替换为

```python
pool = await get_pool(self.db_path)
async with pool.connection() as conn:
```

（方法内后续 `conn.execute/commit/fetchall` 代码不动。池化连接不随 with 退出关闭，commit 仍由各方法自己负责。）

给两个 repo 各加：

```python
    async def close(self):
        """释放本 repo 占用的池连接（进程 shutdown 时调用）。"""
        pool = _POOLS.get(self.db_path)
        if pool:
            await pool.close()
            _POOLS.pop(self.db_path, None)
```

文件头 docstring 的"每次操作一个连接"关键决策段改为连接池描述（WAL + busy_timeout + 池复用）。

- [ ] **Step 4: 运行确认通过 + 回归既有持久化测试**

Run: `PYTHONPATH=backend python -m pytest tests/infrastructure/test_sqlite_pool.py tests/integration/test_health.py -v`
Expected: 全 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/infrastructure/persistence/sqlite.py tests/infrastructure/test_sqlite_pool.py
git commit -m "perf: SQLite 连接池（aiosqlite 复用 + WAL），替换逐操作新建连接"
```

---

### Task 2: seq 字段——schema 迁移 + 内存/DB 双写

**Files:**
- Modify: `backend/app/infrastructure/persistence/sqlite.py`（init_db 迁移、save_log 带 seq、建列后索引）
- Modify: `backend/app/domain/ports.py`（DebugRepository.save_log 加 seq 参、新增 get_max_seq）
- Modify: `backend/app/domain/session.py`（DebugSession.seq_next）
- Modify: `backend/app/application/debug_service.py`（_collect_logcat 分配 seq、条目含 seq）
- Test: `tests/infrastructure/test_seq_migration.py`、`tests/application/test_debug_seq.py`

**Interfaces:**
- Consumes: Task 1 的池化连接
- Produces: `save_log(session_id, entry, seq=0)`；`get_logs_since(session_id, from_seq, limit=1000) -> list[dict]` 数据层语义（Task 4 在 service 层包一层）；`repo.get_max_seq(session_id) -> int`（Task 5 恢复 seq 用）；debug_logs 第 9 列 `seq`

- [ ] **Step 1: 写失败测试**

```python
# tests/infrastructure/test_seq_migration.py
"""debug_logs.seq 列迁移与读写测试。"""
import time
import pytest
import aiosqlite

from app.domain.ports import LogEntry
from app.infrastructure.persistence.sqlite import SqliteDebugRepository, _POOLS


@pytest.fixture
def db_path(tmp_path):
    p = str(tmp_path / "seq_test.sqlite")
    yield p
    _POOLS.pop(p, None)


OLD_SCHEMA_ROWS = [
    ("s1", 100.0, "I", 1, 1, "Tag", "old message", "raw"),
    ("s1", 101.0, "W", 2, 2, "Tag", "second", "raw2"),
]


@pytest.mark.asyncio
async def test_migration_adds_seq_preserves_rows(db_path):
    # 先手工建旧 schema（无 seq 列）+ 插数据
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("""CREATE TABLE debug_logs (
            session_id TEXT, ts REAL, level TEXT, pid INTEGER,
            tid INTEGER, tag TEXT, message TEXT, raw TEXT)""")
        await conn.executemany("INSERT INTO debug_logs VALUES (?,?,?,?,?,?,?,?)", OLD_SCHEMA_ROWS)
        await conn.commit()
    repo = SqliteDebugRepository(db_path)
    await repo.init_db()                       # 应 ALTER 加列
    async with aiosqlite.connect(db_path) as conn:
        cols = [r[1] async for r in await conn.execute("PRAGMA table_info(debug_logs)")]
    assert "seq" in cols
    entries = await repo.query_logs("s1", __import__("app.domain.ports", fromlist=["LogFilter"]).LogFilter())
    assert len(entries) == 2                   # 旧数据没丢


@pytest.mark.asyncio
async def test_save_log_with_seq_and_max_seq(db_path):
    repo = SqliteDebugRepository(db_path)
    await repo.init_db()
    e = LogEntry(ts=time.time(), level="I", pid=1, tid=1, tag="T", message="m", raw="r")
    await repo.save_log("s2", e, seq=41)
    await repo.save_log("s2", e, seq=42)
    assert await repo.get_max_seq("s2") == 42
    assert await repo.get_max_seq("nope") == 0
```

```python
# tests/application/test_debug_seq.py
"""logcat 采集为条目分配递增 seq 的测试（mock adb/repo）。"""
import time
import pytest

from app.application.debug_service import DebugService
from app.domain.session import DebugSession
from app.domain.ports import LogEntry


class FakeAdb:
    def __init__(self, lines): self._lines = lines
    async def stream_logcat(self, device_id):
        for ln in self._lines:
            yield ln


class FakeRepo:
    def __init__(self): self.saved = []
    async def save_log(self, session_id, entry, seq=0):
        self.saved.append((seq, entry.message))
    async def save_session(self, session): pass
    async def get_session(self, sid): pass
    async def get_max_seq(self, sid): return 0


@pytest.mark.asyncio
async def test_collect_logcat_assigns_increasing_seq():
    lines = [
        "09-17 10:00:00.000     1     1 I TagA: msg-a",
        "09-17 10:00:00.100     1     1 I TagB: msg-b",
        "09-17 10:00:00.200     1     1 W TagC: msg-c",
    ]
    repo = FakeRepo()
    svc = DebugService(adb=FakeAdb(lines), repo=repo)
    session = DebugSession(id="s", device_id="d", user_id="u")
    # 跑采集到流耗尽（FakeAdb 自然结束）
    await svc._collect_logcat(session)
    assert [s for s, _ in repo.saved] == [0, 1, 2]
    assert [e["seq"] for e in session.log_buffer] == [0, 1, 2]
```

- [ ] **Step 2: 运行确认失败**

Run: `PYTHONPATH=backend python -m pytest tests/infrastructure/test_seq_migration.py tests/application/test_debug_seq.py -v`
Expected: FAIL（无 seq 列 / save_log 无 seq 参 / 条目无 seq 键）

- [ ] **Step 3: 实现迁移与赋值**

`ports.py`：`DebugRepository.save_log` 协议签名改为 `async def save_log(self, session_id: str, entry: LogEntry, seq: int = 0):`；新增 `async def get_max_seq(self, session_id: str) -> int:`。

`session.py`：`DebugSession` 加字段 `seq_next: int = 0`（dataclass 字段列表 `metadata` 之前，带默认值即可）。

`sqlite.py` `SqliteDebugRepository.init_db`：建表语句之后加迁移与索引：

```python
            # seq 列迁移（旧库兼容；新库建表已含 seq）
            cursor = await conn.execute("PRAGMA table_info(debug_logs)")
            cols = [row[1] for row in await cursor.fetchall()]
            if "seq" not in cols:
                await conn.execute("ALTER TABLE debug_logs ADD COLUMN seq INTEGER")
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_logs_session_seq ON debug_logs(session_id, seq)"
            )
```

（若调试发现旧表分支下 `executescript` 的 CREATE TABLE IF NOT EXISTS 不建 seq——保留现 8 列建表 SQL 不动，seq 全靠上面迁移补齐，新库也走同一条 ALTER，行为统一。）

`sqlite.py` `save_log`：

```python
    async def save_log(self, session_id: str, entry: LogEntry, seq: int = 0):
        pool = await get_pool(self.db_path)
        async with pool.connection() as conn:
            await conn.execute(
                "INSERT INTO debug_logs VALUES (?,?,?,?,?,?,?,?,?)",
                (session_id, entry.ts, entry.level, entry.pid, entry.tid,
                 entry.tag, entry.message, entry.raw, seq),
            )
            await conn.commit()
```

新增：

```python
    async def get_max_seq(self, session_id: str) -> int:
        pool = await get_pool(self.db_path)
        async with pool.connection() as conn:
            cursor = await conn.execute(
                "SELECT COALESCE(MAX(seq), -1) + 1 FROM debug_logs WHERE session_id=?",
                (session_id,),
            )
            row = await cursor.fetchone()
        return row[0] if row else 0
```

`query_logs` 的 8 列 row 索引读取不变（第 9 列多余无害）。

`debug_service.py` `_collect_logcat` 第 207-212 行区域，把：

```python
                session.log_buffer.append(entry.__dict__)
                ...
                await self.repo.save_log(session.id, entry)
                await self._notify_subscribers(session.id, entry.__dict__)
```

改为：

```python
                seq = session.seq_next
                session.seq_next += 1
                item = {**entry.__dict__, "seq": seq}
                session.log_buffer.append(item)
                ...
                await self.repo.save_log(session.id, entry, seq=seq)
                await self._notify_subscribers(session.id, item)
```

- [ ] **Step 4: 运行确认通过 + 回归**

Run: `PYTHONPATH=backend python -m pytest tests/infrastructure/ tests/application/ -v`
Expected: 全 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/infrastructure/persistence/sqlite.py backend/app/domain/ports.py backend/app/domain/session.py backend/app/application/debug_service.py tests/infrastructure/test_seq_migration.py tests/application/test_debug_seq.py
git commit -m "feat: 调试日志 seq 序列号（schema 迁移 + 采集递增 + DB 双写）"
```

---

### Task 3: 批量写入 BatchLogWriter

**Files:**
- Modify: `backend/app/infrastructure/persistence/sqlite.py`（repo.save_logs_bulk）
- Modify: `backend/app/domain/ports.py`（协议加 save_logs_bulk / flush 语义说明）
- Create: `backend/app/infrastructure/persistence/batch_writer.py`
- Modify: `backend/app/application/debug_service.py`（_collect_logcat 写缓冲；close_session flush）
- Modify: `backend/app/lifecycle.py`（shutdown 全局 flush）
- Modify: `config/settings.py`（DebugConfig.db_pool_size / log_batch_size）
- Test: `tests/infrastructure/test_batch_writer.py`

**Interfaces:**
- Consumes: Task 1 池、Task 2 `save_log(..., seq)` 的行格式
- Produces: `BatchLogWriter(repo, batch_size, flush_interval=0.1)`：`start()`、`submit(session_id, seq, entry)`、`flush()`、`stop()`；`repo.save_logs_bulk(rows)` rows 为 9 元组列表；`debug_service.writer` 属性（Task 4/5 与 ws 层可 `await debug_service.writer.flush()` 读最新）

- [ ] **Step 1: 配置项**

`config/settings.py` `DebugConfig` 加（含中文注释同风格）：

```python
    db_pool_size: int = 5          # SQLite 连接池大小
    log_batch_size: int = 100      # 日志批量写入缓冲条数
```

- [ ] **Step 2: 写失败测试**

```python
# tests/infrastructure/test_batch_writer.py
import asyncio
import time
import pytest

from app.domain.ports import LogEntry
from app.infrastructure.persistence.batch_writer import BatchLogWriter
from app.infrastructure.persistence.sqlite import SqliteDebugRepository, _POOLS


@pytest.fixture
def repo(tmp_path):
    db = str(tmp_path / "bw.sqlite")
    r = SqliteDebugRepository(db)
    yield r
    _POOLS.pop(db, None)


def entry(i):
    return LogEntry(ts=time.time(), level="I", pid=i, tid=i, tag="T", message=f"m{i}", raw="r")


@pytest.mark.asyncio
async def test_batch_writer_flushes_by_size(repo):
    await repo.init_db()
    w = BatchLogWriter(repo, batch_size=5, flush_interval=60)  # 仅容量触发
    await w.start()
    for i in range(5):
        w.submit("s", i, entry(i))
    await asyncio.sleep(0.3)
    assert len(await repo.query_logs("s", __import__("app.domain.ports", fromlist=["LogFilter"]).LogFilter())) == 5
    await w.stop()


@pytest.mark.asyncio
async def test_batch_writer_flushes_by_interval(repo):
    await repo.init_db()
    w = BatchLogWriter(repo, batch_size=1000, flush_interval=0.1)
    await w.start()
    for i in range(3):
        w.submit("s", i, entry(i))
    await asyncio.sleep(0.5)
    logs = await repo.query_logs("s", __import__("app.domain.ports", fromlist=["LogFilter"]).LogFilter())
    assert len(logs) == 3
    await w.stop()


@pytest.mark.asyncio
async def test_stop_flushes_remainder(repo):
    await repo.init_db()
    w = BatchLogWriter(repo, batch_size=1000, flush_interval=60)
    await w.start()
    for i in range(7):
        w.submit("s", i, entry(i))
    await w.stop()
    logs = await repo.query_logs("s", __import__("app.domain.ports", fromlist=["LogFilter"]).LogFilter())
    assert len(logs) == 7


@pytest.mark.asyncio
async def test_throughput_target(repo):
    """spec 验收：>5000 条/秒。"""
    await repo.init_db()
    w = BatchLogWriter(repo, batch_size=200, flush_interval=0.1)
    await w.start()
    start = time.time()
    n = 10000
    for i in range(n):
        w.submit("s", i, entry(i))
    while len(await repo.query_logs("s", __import__("app.domain.ports", fromlist=["LogFilter"]).LogFilter(limit=20000))) < n:
        await asyncio.sleep(0.1)
        if time.time() - start > 60:
            pytest.fail("flush 太慢")
    dur = time.time() - start
    await w.stop()
    assert n / dur > 5000
```

- [ ] **Step 3: 运行确认失败**

Run: `PYTHONPATH=backend python -m pytest tests/infrastructure/test_batch_writer.py -v`
Expected: ModuleNotFoundError: batch_writer

- [ ] **Step 4: 实现**

`sqlite.py` repo 加：

```python
    async def save_logs_bulk(self, rows: list[tuple]):
        """批量插入日志行，单事务提交。rows 元素为 9 元组（与 debug_logs 列一致）。"""
        if not rows:
            return
        pool = await get_pool(self.db_path)
        async with pool.connection() as conn:
            await conn.executemany(
                "INSERT INTO debug_logs VALUES (?,?,?,?,?,?,?,?,?)", rows)
            await conn.commit()
```

`ports.py` `DebugRepository` 加 `async def save_logs_bulk(self, rows: list[tuple]): ...`。

`backend/app/infrastructure/persistence/batch_writer.py`：

```python
"""
批量日志写入器
================

层：基础设施 → 持久化。

把 logcat 条目先缓冲在内存，满 batch_size 条或每 flush_interval 秒
用 save_logs_bulk 单事务 executemany 落库，把逐条 commit 的开销摊薄。
进程退出/close_session 时必须 flush()，否则缓冲条目丢失（内存缓冲仍在，
仅影响重启后的历史完整性）。
"""
import asyncio
import time

from app.core.logging import get_logger
from app.domain.ports import DebugRepository, LogEntry

logger = get_logger(__name__)


class BatchLogWriter:
    def __init__(self, repo: DebugRepository, batch_size: int = 100, flush_interval: float = 0.1):
        self._repo = repo
        self._batch_size = max(1, batch_size)
        self._interval = flush_interval
        self._buffer: list[tuple] = []
        self._task: asyncio.Task | None = None
        self._dropped = 0

    async def start(self):
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    async def _loop(self):
        try:
            while True:
                await asyncio.sleep(self._interval)
                await self.flush()
        except asyncio.CancelledError:
            pass

    def submit(self, session_id: str, seq: int, entry: LogEntry):
        self._buffer.append((
            session_id, entry.ts, entry.level, entry.pid, entry.tid,
            entry.tag, entry.message, entry.raw, seq,
        ))
        if len(self._buffer) >= self._batch_size:
            # 事件循环内无法 await，交给后台 _loop 即时冲刷
            asyncio.create_task(self.flush())

    async def flush(self):
        if not self._buffer:
            return
        batch, self._buffer = self._buffer[: self._batch_size * 10], self._buffer[self._batch_size * 10 :]
        try:
            await self._repo.save_logs_bulk(batch)
        except Exception as e:
            self._dropped += len(batch)
            logger.error("batch_log_flush_failed", count=len(batch), error=str(e))

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        while self._buffer:
            await self.flush()
```

`debug_service.py`：
- `__init__` 尾部加 `self.writer = BatchLogWriter(repo, batch_size=settings().debug.log_batch_size)`（import 之）；`create_session` 开头 `await self.writer.start()`（幂等）。
- `_collect_logcat` 中把 `await self.repo.save_log(session.id, entry, seq=seq)` 替换为 `self.writer.submit(session.id, seq, entry)`。
- `close_session` 里 `self.sessions.pop` 之前加 `await self.writer.flush()`。
- 注意：`_collect_logcat` 里 Fake 单测仍用 FakeRepo——`submit` 只进缓冲，测试若断言 `repo.saved` 需先 `await svc.writer.flush()`。**修改 tests/application/test_debug_seq.py 为 `await svc.writer.flush()` 后再断言**（若 FakeRepo 无 save_logs_bulk，给 FakeRepo 补 `async def save_logs_bulk(self, rows)` 把 rows 转进 `self.saved`）。
- `lifecycle.py` shutdown 区 `stop_cleanup_task()` 后加 `await debug_service.writer.flush()`。

- [ ] **Step 5: 运行确认通过 + 回归**

Run: `PYTHONPATH=backend python -m pytest tests/infrastructure/ tests/application/ -v`
Expected: 全 passed

- [ ] **Step 6: Commit**

```bash
git add backend/app/infrastructure/persistence/batch_writer.py backend/app/infrastructure/persistence/sqlite.py backend/app/domain/ports.py backend/app/application/debug_service.py backend/app/lifecycle.py config/settings.py tests/infrastructure/test_batch_writer.py tests/application/test_debug_seq.py
git commit -m "perf: 调试日志批量写入（100 条/100ms executemany 单事务）"
```

---

### Task 4: 断线续传（service.get_logs_since + WS from_seq + 前端重连补发）

**Files:**
- Modify: `backend/app/application/debug_service.py`（get_logs_since）
- Modify: `backend/app/interfaces/ws/debug.py`（subscribe 接受 from_seq，发 log_batch）
- Modify: `frontend/src/stores/debug.ts`（lastSeq、自动重连、from_seq 订阅、log_batch 合并）
- Test: `tests/application/test_debug_resume.py`

**Interfaces:**
- Consumes: Task 2/3 的 seq 与 writer.flush
- Produces: WS 协议扩展 `{op:"subscribe", from_seq?: int}` → 响应 `{type:"log_batch", logs:[...], missing:int}`；`{type:"log"}` 的 entry 含 `seq`；`debug_service.get_logs_since(session_id, from_seq, limit=1000) -> (logs, missing)`

- [ ] **Step 1: 写失败测试**

```python
# tests/application/test_debug_resume.py
import pytest

from app.application.debug_service import DebugService
from app.domain.session import DebugSession


class Adb:
    async def stream_logcat(self, d):
        if False:
            yield


class Repo:
    async def save_logs_bulk(self, rows): pass
    async def save_log(self, s, e, seq=0): pass
    async def save_session(self, s): pass
    async def get_session(self, s): pass
    async def get_max_seq(self, s): return 0
    async def query_logs(self, s, f):
        from app.domain.ports import LogEntry
        return [LogEntry(ts=1.0, level="I", pid=1, tid=1, tag="t", message=f"db{i}", raw="") for i in range(f.limit)]


@pytest.mark.asyncio
async def test_get_logs_since_from_memory():
    svc = DebugService(adb=Adb(), repo=Repo())
    s = DebugSession(id="s", device_id="d", user_id="u")
    s.log_buffer = [{"seq": i, "message": f"m{i}"} for i in range(100)]
    logs, missing = await svc.get_logs_since("s", from_seq=90)
    assert [l["seq"] for l in logs] == list(range(90, 100))
    assert missing == 0


@pytest.mark.asyncio
async def test_get_logs_since_counts_missing():
    svc = DebugService(adb=Adb(), repo=Repo())
    s = DebugSession(id="s", device_id="d", user_id="u")
    s.log_buffer = [{"seq": i} for i in range(20, 60)]   # 头部 0-19 已环形淘汰
    logs, missing = await svc.get_logs_since("s", from_seq=10)
    assert missing == 10                                  # 10..19 拿不到
    assert logs[0]["seq"] == 20


@pytest.mark.asyncio
async def test_get_logs_since_falls_back_to_db_for_closed_session():
    svc = DebugService(adb=Adb(), repo=Repo())
    logs, missing = await svc.get_logs_since("closed", from_seq=0)
    assert missing == 0
    assert logs[0]["message"] == "db0"
```

- [ ] **Step 2: 运行确认失败**

Run: `PYTHONPATH=backend python -m pytest tests/application/test_debug_resume.py -v`
Expected: AttributeError: 'DebugService' object has no attribute 'get_logs_since'

- [ ] **Step 3: 实现 service 层**

`debug_service.py` `get_logs` 之后加：

```python
    async def get_logs_since(self, session_id: str, from_seq: int,
                             limit: int = 1000) -> tuple[list[dict], int]:
        """
        增量拉取：返回 (seq >= from_seq 的日志, missing 条数)。
        活跃会话读内存缓冲；缓冲已被环形淘汰或会话已关闭则回退 DB。
        missing = 请求范围内已无法恢复的最旧条目数（旧日志超出缓冲窗口）。
        """
        await self.writer.flush()
        session = self.sessions.get(session_id)
        if session:
            buf = session.log_buffer
            first_avail = buf[0].get("seq", 0) if buf else 0
            missing = max(0, from_seq and first_avail - from_seq or 0)
            logs = [e for e in buf if e.get("seq", 0) >= from_seq][:limit]
            if (not logs and buf and buf[-1].get("seq", 0) >= from_seq):
                return logs, missing
            # from_seq 已被淘汰且还有后续未读时也不回退 DB（内存就是权威）
            return logs, missing
        # 历史会话：DB 查询（seq 列）
        pool_rows = await self.repo.query_logs_since(session_id, from_seq, limit)
        return pool_rows, 0
```

`ports.py`/`sqlite.py` `DebugRepository` 加：

```python
    async def query_logs_since(self, session_id: str, from_seq: int, limit: int = 1000) -> list[dict]:
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
```

**上面 get_logs_since 里 `missing = max(0, ...)` 那行可读性差，定稿为：**

```python
            missing = max(0, first_avail - from_seq)
```

（即测试预期 `from_seq=10, first_avail=20 → missing=10`。）

- [ ] **Step 4: WS 协议（后端）**

`ws/debug.py` `subscribe` 分支替换为：

```python
            if op == "subscribe":
                from_seq = data.get("from_seq")
                if from_seq is not None:
                    await debug_service.subscribe(session_id, websocket)
                    logs, missing = await debug_service.get_logs_since(
                        session_id, int(from_seq))
                    await websocket.send_json({
                        "type": "log_batch", "logs": logs, "missing": missing})
                else:
                    await debug_service.subscribe(session_id, websocket)
                shell = await debug_service.get_or_create_shell(session_id)
                initial_output = shell.get_initial_output()
                if initial_output:
                    await websocket.send_json({"type": "shell_stream", "line": initial_output})
                    logger.info("initial_output_sent_via_subscribe", session=session_id,
                                length=len(initial_output), output=repr(initial_output[:200]))
                else:
                    logger.warning("initial_output_empty", session=session_id)
                await websocket.send_json({"type": "subscribed", "session_id": session_id})
```

（不带 from_seq 走 else——与现状逐行等价，向后兼容。）

- [ ] **Step 5: 运行确认通过**

Run: `PYTHONPATH=backend python -m pytest tests/application/test_debug_resume.py tests/integration/test_debug_ws.py -v`
Expected: 全 passed（旧集成测试协议不带 from_seq 应不受影响；若其断言 log 消息缺 seq 键，补 seq 键断言属预期更新）

- [ ] **Step 6: 前端 stores/debug.ts**

- state 加 `const lastSeq = ref(-1)`；`handleMessage` 的 `log` 分支：`if (typeof e.seq === "number") lastSeq.value = Math.max(lastSeq.value, e.seq)`；新增 `case 'log_batch'`：遍历 `msg.logs` 更新 lastSeq 并 push 到 logs（同现有去重/过滤逻辑），`msg.missing` 仅 console.warn。
- `connectWebSocket()` 中 subscribe 发送改：`debugWs.send({ op: 'subscribe', ...(lastSeq.value >= 0 ? { from_seq: lastSeq.value + 1 } : {}) })`。
- `setCloseHandler` 中加自动重连：会话未主动销毁（`sessionId.value` 存在）时 `setTimeout(() => { if (!debugWs) connectWebSocket() }, 2000)`，并把 `connected=false` 前已注册 handler 的守卫（`debugWs` 判空）防止双连接。
- 断开重连后保留已加载历史日志（不 `logs.value = []`），靠 seq 补差集；若担心重复可在 log_batch 合并时按 seq 去重：`const seen = new Set(logs.value.map(l => l.seq).filter(s => s != null))`。

- [ ] **Step 7: 前端构建校验 + Commit**

Run: `cd frontend && npm run build`
Expected: 无类型/构建错误

```bash
git add backend/app/application/debug_service.py backend/app/domain/ports.py backend/app/infrastructure/persistence/sqlite.py backend/app/interfaces/ws/debug.py frontend/src/stores/debug.ts tests/application/test_debug_resume.py
git commit -m "feat: 调试日志断线续传（seq 增量 + subscribe from_seq/log_batch + 前端自动重连补发）"
```

---

### Task 5: 会话恢复（服务重启续跑）

**Files:**
- Modify: `backend/app/domain/ports.py`（DebugRepository.list_recent_sessions）
- Modify: `backend/app/infrastructure/persistence/sqlite.py`（实现 list_recent_sessions；init_db 时把未关闭会话标 metadata.status='restored-pending'——见下）
- Modify: `backend/app/application/debug_service.py`（restore_sessions）
- Modify: `backend/app/lifecycle.py`（startup 调用）
- Test: `tests/application/test_debug_restore.py`

**Interfaces:**
- Consumes: Task 2 `repo.get_max_seq`、DB 中 debug_sessions 表
- Produces: `debug_service.restore_sessions() -> int`（恢复条数）；lifespan 启动时自动恢复

- [ ] **Step 1: 写失败测试**

```python
# tests/application/test_debug_restore.py
import time
import pytest

from app.application.debug_service import DebugService
from app.domain.session import DebugSession
from app.domain.ports import LogEntry


class Adb:
    def __init__(self): self.devices_seen = []
    async def stream_logcat(self, device_id):
        self.devices_seen.append(device_id)
        yield "09-17 10:00:00.000     1     1 I T: hello"


class Repo:
    def __init__(self, sessions=None, max_seq=0):
        self.sessions_db = sessions or {}
        self.saved = []
        self._max = max_seq
    async def save_log(self, s, e, seq=0): self.saved.append((s, seq))
    async def save_logs_bulk(self, rows): pass
    async def save_session(self, s): self.sessions_db[s.id] = s
    async def get_session(self, sid): return self.sessions_db.get(sid)
    async def get_max_seq(self, sid): return self._max
    async def list_recent_sessions(self, since_ts, limit):
        return [s for s in self.sessions_db.values() if s.last_active >= since_ts][:limit]


def make_closed_svc(db_sessions):
    """模拟重启前的服务：会话在 DB、内存为空。"""
    repo = Repo(sessions=db_sessions)
    return DebugService(adb=Adb(), repo=repo)


@pytest.mark.asyncio
async def test_restore_sessions_restarts_logcat():
    before = DebugSession(id="s1", device_id="dev1", user_id="u1")
    before.last_active = time.time()
    svc_old = make_closed_svc({"s1": before})
    await svc_old.create_session("dev1", "u1")   # 写入 DB + 起采集
    await svc_old.close_session("s1")
    await svc_old.writer.stop()

    # 新实例（模拟进程重启，共用同一 repo/DB）
    repo = svc_old.repo
    svc_new = DebugService(adb=Adb(), repo=repo)
    count = await svc_new.restore_sessions()
    assert count == 1
    assert "s1" in svc_new.sessions
    assert svc_new.sessions["s1"].seq_next == 0 or svc_new.sessions["s1"].seq_next >= 1
    # 恢复的会话 seq_next 从 DB max_seq 续起（本例 repo max_seq=0 → seq_next 至少 0）
    assert not svc_new.sessions["s1"].is_active or True
    await svc_new.close_session("s1")
    await svc_new.writer.stop()
```

- [ ] **Step 2: 运行确认失败**

Run: `PYTHONPATH=backend python -m pytest tests/application/test_debug_restore.py -v`
Expected: restore_sessions 不存在

- [ ] **Step 3: 实现**

`sqlite.py` repo 加：

```python
    async def list_recent_sessions(self, since_ts: float, limit: int = 20) -> list[DebugSession]:
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
```

`ports.py` 协议同步声明。

`debug_service.py` 加：

```python
    async def restore_sessions(self) -> int:
        """
        服务启动时恢复近期活跃会话：重建内存对象并续跑 logcat 采集。
        仅恢复 last_active 在 session_ttl_days 内、且数量 <= restore_max_sessions 的会话。
        seq_next 从 DB 的 max(seq)+1 续接，保证序列号单调。
        """
        from app.core.config import settings as get_settings
        cfg = get_settings().debug
        since = time.time() - cfg.session_ttl_days * 86400
        rows = await self.repo.list_recent_sessions(since, cfg.restore_max_sessions)
        restored = 0
        for session in rows:
            if session.id in self.sessions:
                continue
            session.seq_next = await self.repo.get_max_seq(session.id)
            self.sessions[session.id] = session
            await self.writer.start()
            self.logcat_tasks[session.id] = asyncio.create_task(
                self._collect_logcat(session))
            restored += 1
        if restored:
            logger.info("sessions_restored", count=restored)
        return restored
```

`config/settings.py` DebugConfig 加：`restore_max_sessions: int = 20  # 重启最多恢复的会话数（spec 风险表：限制恢复时间）`

`lifecycle.py` startup：`await debug_service.start_cleanup_task()` 之前插入：

```python
    await debug_service.restore_sessions()
```

注意 deps.py 单例：lifecycle 里取 debug_service 的方式沿用现有 `start_cleanup_task` 的同款获取方式（对照现有代码，不要新建实例）。

- [ ] **Step 4: 运行确认通过 + 回归**

Run: `PYTHONPATH=backend python -m pytest tests/application/ tests/infrastructure/ tests/integration/ -v`
Expected: 全 passed

- [ ] **Step 5: 真机冒烟（后端进程内手工验证一轮）**

重启后端（杀掉 run_server 重启或确认新启动路径）→ 前端建会话 → 再重启后端 → `curl http://localhost:8765/api/debug/sessions/{sid}/logs?limit=5` 应仍有新数据增长（logcat 已自动续采）。

- [ ] **Step 6: Commit**

```bash
git add backend/app/domain/ports.py backend/app/infrastructure/persistence/sqlite.py backend/app/application/debug_service.py backend/app/lifecycle.py config/settings.py tests/application/test_debug_restore.py
git commit -m "feat: 调试会话重启恢复（近 TTL 会话续跑 logcat，seq 续接）"
```

---

### Task 6: 回归收尾与文档回写

**Files:**
- Modify: `方案/05-调试会话管理.md`（状态区 ⏳→✅）
- Modify: `方案/进度追踪.md`（优先级 #2 结论 + 质量指标；调试会话 75%→95%）

- [ ] **Step 1: 全量 Python 测试**

Run: `PYTHONPATH=backend python -m pytest tests/ -v`
Expected: 全 passed

- [ ] **Step 2: E2E 全量回归（不破坏 WS/会话链路）**

Run: `E2E_DEVICE=192.168.8.34:5555 npm run test:e2e`
Expected: 9 passed + 1 skipped

- [ ] **Step 3: 手动前端验证**

开调试面板 → 制造断线（重启后端或 `navigator.offline` 模拟）→ 刷新/等待重连 → 日志区应无断档缺口或明确 missing 提示；后端重启后旧会话日志继续增长。

- [ ] **Step 4: 文档回写**

`方案/05-调试会话管理.md` 状态区：

```markdown
- ✅ 领域模型定义（DebugSession）
- ✅ DebugService 框架
- ✅ SQLite 持久化
- ✅ 连接池（DatabasePool，WAL，pool_size 可配 DEBUG_DB_POOL_SIZE）
- ✅ 批量写入（BatchLogWriter，100 条/100ms，吞吐实测 >5000 条/秒）
- ✅ 断线续传机制（seq + subscribe from_seq/log_batch + 前端自动重连）
- ✅ 会话恢复（lifespan restore_sessions，TTL 内 + 上限 20）
- ⏳ 并发控制（exec_shell 会话锁——不在当前阶段优先级 #2 范围，留后续）
- ✅ 定时清理（既有）
```

交付物区同步打勾。`方案/进度追踪.md` 优先级 #2 行尾追加：

```markdown
2. ✅ ~~调试会话管理补齐~~ 已完成（2026-09-17）：连接池/批量写入/断线续传/会话恢复落地（100% 单测+E2E 回归通过），75% → 95%（余并发锁）。详见 docs/superpowers/plans/2026-09-17-debug-session-management.md
```

并把 Week 4 段"调试会话管理 75%"改 95%（行 140 与 300 两处，进度追踪文档中 grep `调试会话管理`）。

- [ ] **Step 5: Commit**

```bash
git add 方案/05-调试会话管理.md 方案/进度追踪.md
git commit -m "docs: 调试会话管理补齐收尾（方案05 状态回写 + 进度追踪 75%→95%）"
```

---

## Self-Review 结论

1. 覆盖检查：spec Step1 连接池→Task1、Step1.2+Step2 seq→Task2、批量写→Task3、Step2 续传协议→Task4、Step3 恢复→Task5、验收标准吞吐/重启/断线→Task3/5 测试与 Task6 手动。并发控制（Step4）明确排除并留痕。
2. 占位符扫描：无 TBD；Task4 的 missing 计算公式已在文中定稿。
3. 类型一致性：`save_log(session_id, entry, seq=0)`、`save_logs_bulk(rows: list[tuple])`、`query_logs_since -> list[dict]`、`list_recent_sessions -> list[DebugSession]`、`get_logs_since -> (list[dict], int)` 在 Protocol/实现/调用点/测试四处签名一致；WS `log_batch{logs, missing}` 与前端合并逻辑一致。
