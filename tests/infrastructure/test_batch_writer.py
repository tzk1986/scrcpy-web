"""BatchLogWriter 批量写入测试（不依赖设备/服务）。"""
import asyncio
import time

import pytest

from app.domain.ports import LogEntry, LogFilter
from app.infrastructure.persistence.batch_writer import BatchLogWriter
from app.infrastructure.persistence.sqlite import SqliteDebugRepository


@pytest.fixture
async def repo(tmp_path):
    db = str(tmp_path / "bw.sqlite")
    r = SqliteDebugRepository(db)
    yield r
    # aiosqlite 连接线程非守护，不关闭会阻塞解释器退出（pytest 挂起）
    await r.close()


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
    assert len(await repo.query_logs("s", LogFilter())) == 5
    await w.stop()


@pytest.mark.asyncio
async def test_batch_writer_flushes_by_interval(repo):
    await repo.init_db()
    w = BatchLogWriter(repo, batch_size=1000, flush_interval=0.1)
    await w.start()
    for i in range(3):
        w.submit("s", i, entry(i))
    await asyncio.sleep(0.5)
    logs = await repo.query_logs("s", LogFilter())
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
    logs = await repo.query_logs("s", LogFilter())
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
    while len(await repo.query_logs("s", LogFilter(limit=20000))) < n:
        await asyncio.sleep(0.1)
        if time.time() - start > 60:
            pytest.fail("flush 太慢")
    dur = time.time() - start
    await w.stop()
    assert n / dur > 5000
