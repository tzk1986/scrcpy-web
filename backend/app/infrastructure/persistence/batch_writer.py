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
from typing import Any

from app.core.logging import get_logger
from app.domain.ports import DebugRepository, LogEntry

logger = get_logger(__name__)


class BatchLogWriter:
    def __init__(self, repo: DebugRepository, batch_size: int = 100, flush_interval: float = 0.1):
        self._repo = repo
        self._batch_size = max(1, batch_size)
        self._interval = flush_interval
        self._buffer: list[tuple[Any, ...]] = []
        self._task: asyncio.Task[None] | None = None
        self._dropped = 0

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._interval)
                await self.flush()
        except asyncio.CancelledError:
            pass

    def submit(self, session_id: str, seq: int, entry: LogEntry) -> None:
        self._buffer.append((
            session_id, entry.ts, entry.level, entry.pid, entry.tid,
            entry.tag, entry.message, entry.raw, seq,
        ))
        if len(self._buffer) >= self._batch_size:
            # 事件循环内无法 await，交给后台 _loop 即时冲刷
            asyncio.create_task(self.flush())

    async def flush(self) -> None:
        if not self._buffer:
            return
        batch, self._buffer = self._buffer[: self._batch_size * 10], self._buffer[self._batch_size * 10 :]
        try:
            await self._repo.save_logs_bulk(batch)
        except Exception as e:
            self._dropped += len(batch)
            logger.error("batch_log_flush_failed", count=len(batch), error=str(e))

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        while self._buffer:
            await self.flush()
