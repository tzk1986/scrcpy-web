"""
批量日志写入器
================

层：应用层（方案 52 B6 R3 自 infrastructure/persistence/batch_writer.py
搬正——经注入 DebugRepository 端口落库，本体零 infra 技术）。

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

# 方案 58 B-22：flush 失败有界重试——瞬时 DB 故障不得让日志历史永久空洞。
# 失败批次进重试队列（FIFO），每轮 flush 先试队列头一批（每 interval 得一
# 次重试机会）；重试轮次耗尽或队列条数越限则整批丢弃并告警（dropped 增量）。
_RETRY_MAX_ROUNDS = 20
_RETRY_MAX_RECORDS = 5000


class BatchLogWriter:
    def __init__(self, repo: DebugRepository, batch_size: int = 100, flush_interval: float = 0.1):
        self._repo = repo
        self._batch_size = max(1, batch_size)
        self._interval = flush_interval
        self._buffer: list[tuple[Any, ...]] = []
        self._task: asyncio.Task[None] | None = None
        self._dropped = 0
        # 方案 51 D12：即时冲刷任务持引用（done 回调 discard），防裸 create_task 被 GC
        self._flush_tasks: set[asyncio.Task[None]] = set()
        # 方案 58 B-22：重试队列 [(已试轮次, 批次)] 与其在队条数
        self._retry: list[tuple[int, list[tuple[Any, ...]]]] = []
        self._retry_records = 0

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
            # 事件循环内无法 await，交给后台任务即时冲刷（D12 持引用）
            task = asyncio.create_task(self.flush())
            self._flush_tasks.add(task)
            task.add_done_callback(self._flush_tasks.discard)

    async def flush(self) -> None:
        # 方案 58 B-22：先消化重试队列头一批（保序：旧数据先落），再冲新缓冲
        if self._retry:
            rounds, batch = self._retry.pop(0)
            self._retry_records -= len(batch)
            await self._save(batch, rounds + 1)
        if not self._buffer:
            return
        batch, self._buffer = self._buffer[: self._batch_size * 10], self._buffer[self._batch_size * 10 :]
        await self._save(batch, 1)

    async def _save(self, batch: list[tuple[Any, ...]], rounds: int) -> None:
        """落库一批；失败转有界重试或丢弃告警（rounds=本批已试轮次）。"""
        try:
            await self._repo.save_logs_bulk(batch)
        except Exception as e:
            self._retry_or_drop(batch, rounds, e)

    def _retry_or_drop(self, batch: list[tuple[Any, ...]], rounds: int, exc: Exception) -> None:
        error = str(exc)
        if rounds > _RETRY_MAX_ROUNDS:
            self._drop(batch, "retry exhausted", rounds, error)
            return
        if len(batch) > _RETRY_MAX_RECORDS:
            self._drop(batch, "batch exceeds retry cap", rounds, error)
            return
        while self._retry and self._retry_records + len(batch) > _RETRY_MAX_RECORDS:
            _, oldest = self._retry.pop(0)
            self._retry_records -= len(oldest)
            self._drop(oldest, "retry queue overflow", rounds, error)
        self._retry.append((rounds, batch))
        self._retry_records += len(batch)
        logger.warning("batch_log_flush_retrying", count=len(batch),
                       rounds=rounds, error=error)

    def _drop(self, batch: list[tuple[Any, ...]], reason: str, rounds: int, error: str) -> None:
        self._dropped += len(batch)
        logger.error("batch_log_flush_dropped", count=len(batch),
                     dropped=self._dropped, rounds=rounds, reason=reason, error=error)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        # 方案 58 B-22：收尾同样消化重试队列（瞬时故障恢复窗内不丢尾批）
        while self._buffer or self._retry:
            await self.flush()
