"""BatchLogWriter 批量写入测试（不依赖设备/服务）。"""
import asyncio
import time

import pytest
from structlog.testing import capture_logs

from app.application import batch_writer
from app.application.batch_writer import BatchLogWriter
from app.domain.ports import LogEntry, LogFilter
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


# ---------------------------------------------------------------------------
# 方案 58 B-22：flush 失败有界重试（瞬时 DB 故障不再永久丢日志）
# ---------------------------------------------------------------------------


class FlakyRepo:
    """save_logs_bulk 按脚本逐次执行：异常元素抛出，其余正常入库。"""

    def __init__(self, script: list[object]) -> None:
        self._script = list(script)
        self.saved: list[tuple] = []

    async def save_logs_bulk(self, batch: list[tuple]) -> None:
        outcome = self._script.pop(0) if self._script else None
        if isinstance(outcome, Exception):
            raise outcome
        self.saved.extend(batch)


@pytest.mark.asyncio
async def test_flush_failure_batches_are_retried():
    """失败批次进重试队列，下轮 flush 优先消化；恢复后不丢不重复。"""
    repo = FlakyRepo([RuntimeError("db locked"), None])
    w = BatchLogWriter(repo, batch_size=100, flush_interval=60)
    for i in range(2):
        w.submit("s", i, entry(i))

    await w.flush()  # 第 1 次失败 → 进重试队列
    assert w._dropped == 0
    assert repo.saved == []

    await w.flush()  # 重试成功
    assert len(repo.saved) == 2
    assert w._dropped == 0


@pytest.mark.asyncio
async def test_retry_exhausted_drops_batch_with_alert(monkeypatch):
    """重试轮次耗尽 → 整批丢弃，dropped 计数并告警带增量。"""
    monkeypatch.setattr(batch_writer, "_RETRY_MAX_ROUNDS", 2)
    repo = FlakyRepo([RuntimeError("boom")] * 10)
    w = BatchLogWriter(repo, batch_size=100, flush_interval=60)
    w.submit("s", 0, entry(0))

    with capture_logs() as logs:
        for _ in range(3):
            await w.flush()

    assert w._dropped == 1
    drops = [e for e in logs if e["event"] == "batch_log_flush_dropped"]
    assert drops and drops[-1]["count"] == 1 and drops[-1]["dropped"] == 1


@pytest.mark.asyncio
async def test_retry_queue_record_cap_drops_oldest(monkeypatch):
    """重试队列条数越限 → 丢最老批次保新，dropped 计增量。"""
    monkeypatch.setattr(batch_writer, "_RETRY_MAX_RECORDS", 3)
    repo = FlakyRepo([RuntimeError("boom")] * 10)
    w = BatchLogWriter(repo, batch_size=2, flush_interval=60)

    # 批 A（2 条）
    for i in range(2):
        w.submit("s", i, entry(i))
    await w.flush()  # A 失败 → 重试队列（rec=2）

    # 批 B（2 条）：A 重试又失败（rounds=2 仍 ≤ 2 轮上限），B 失败时 2+2>3
    for i in range(2, 4):
        w.submit("s", i, entry(i))
    await w.flush()

    assert w._dropped == 2  # 最老的 A 整批被丢
    assert w._retry_records == 2  # 队列仅存 B


@pytest.mark.asyncio
async def test_stop_drains_retry_queue():
    """stop() 收尾也消化重试队列（瞬时故障恢复窗内不丢尾批）。"""
    repo = FlakyRepo([RuntimeError("db locked"), None])
    w = BatchLogWriter(repo, batch_size=100, flush_interval=60)
    for i in range(2):
        w.submit("s", i, entry(i))

    await w.flush()  # 失败 → 进重试队列
    await w.stop()

    assert len(repo.saved) == 2
    assert w._dropped == 0
