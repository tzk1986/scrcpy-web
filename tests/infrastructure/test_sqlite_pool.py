"""SQLite 连接池测试（不依赖设备/服务）。"""
import asyncio

import pytest

from app.infrastructure.persistence.sqlite import (
    DatabasePool,
    SqliteDebugRepository,
    _POOLS,
    get_pool,
)


@pytest.fixture
def db_path(tmp_path):
    p = str(tmp_path / "pool_test.sqlite")
    yield p
    _POOLS.pop(p, None)


async def test_pool_reuses_connections(db_path):
    pool = DatabasePool(db_path, pool_size=2)
    await pool.initialize()
    async with pool.connection() as c1:
        async with pool.connection() as c2:
            assert c1 is not c2  # 池内不同连接
    # 归还后再取应复用（不新建第 3 条）
    seen = set()
    for _ in range(6):
        async with pool.connection() as c:
            seen.add(id(c))
    assert len(seen) <= 2
    await pool.close()


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
    assert len(order) == 3  # 第 4/5 个在排队
    held.set()
    await asyncio.gather(*tasks)
    assert len(order) == 5
    await pool.close()


async def test_get_pool_singleton_per_path(db_path):
    p1 = await get_pool(db_path)
    p2 = await get_pool(db_path)
    assert p1 is p2


async def test_repo_works_through_pool(db_path):
    repo = SqliteDebugRepository(db_path)
    await repo.init_db()
    from app.domain.session import DebugSession

    s = DebugSession(id="s1", device_id="d1", user_id="u1")
    await repo.save_session(s)
    got = await repo.get_session("s1")
    assert got is not None and got.device_id == "d1"
