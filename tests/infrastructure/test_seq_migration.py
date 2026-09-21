"""debug_logs.seq 列迁移与读写测试。"""
import time

import aiosqlite
import pytest

from app.domain.ports import LogEntry, LogFilter
from app.infrastructure.persistence.sqlite import SqliteDebugRepository, _POOLS


@pytest.fixture
async def db_path(tmp_path):
    p = str(tmp_path / "seq_test.sqlite")
    yield p
    # aiosqlite 连接线程非守护，不关闭会阻塞解释器退出（pytest 挂起）
    pool = _POOLS.pop(p, None)
    if pool is not None:
        await pool.close()


OLD_SCHEMA_ROWS = [
    ("s1", 100.0, "I", 1, 1, "Tag", "old message", "raw"),
    ("s1", 101.0, "W", 2, 2, "Tag", "second", "raw2"),
]


async def test_migration_adds_seq_preserves_rows(db_path):
    # 先手工建旧 schema（无 seq 列）+ 插数据
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("""CREATE TABLE debug_logs (
            session_id TEXT, ts REAL, level TEXT, pid INTEGER,
            tid INTEGER, tag TEXT, message TEXT, raw TEXT)""")
        await conn.executemany(
            "INSERT INTO debug_logs VALUES (?,?,?,?,?,?,?,?)", OLD_SCHEMA_ROWS)
        await conn.commit()
    repo = SqliteDebugRepository(db_path)
    await repo.init_db()  # 应 ALTER 加列
    async with aiosqlite.connect(db_path) as conn:
        cursor = await conn.execute("PRAGMA table_info(debug_logs)")
        cols = [r[1] for r in await cursor.fetchall()]
    assert "seq" in cols
    entries = await repo.query_logs("s1", LogFilter())
    assert len(entries) == 2  # 旧数据没丢


async def test_save_log_with_seq_and_next_seq(db_path):
    repo = SqliteDebugRepository(db_path)
    await repo.init_db()
    e = LogEntry(ts=time.time(), level="I", pid=1, tid=1, tag="T", message="m", raw="r")
    await repo.save_log("s2", e, seq=41)
    await repo.save_log("s2", e, seq=42)
    assert await repo.next_seq("s2") == 43   # 下一条从 max+1 续接
    assert await repo.next_seq("nope") == 0  # 无记录从 0 起
