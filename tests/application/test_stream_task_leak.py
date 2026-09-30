"""
StreamService 帧循环 Task 泄漏回归测试（P0-1）
================================================

性能对标清单 §4.1 spike 确认（1.00 任务/帧）：帧分支胜出后
loser（restart_event.wait）从不取消，pending Task 随帧数线性累积，
会话期间永不回收。

修复：帧分支内取消 event_task，wait 的 future 被取消后即从
Event._waiters 摘除；本测试锁定该回归。
"""

import asyncio

import pytest

from app.application.stream_service import StreamService


def _count_event_wait() -> int:
    n = 0
    for t in asyncio.all_tasks():
        name = getattr(t.get_coro(), "__qualname__", "") or ""
        if "Event.wait" in name:
            n += 1
    return n


class FiniteEncoder:
    """立即产 5 帧后结束——每轮取帧分支都先于 restart_event 胜出。"""

    def __init__(self):
        self.stop_called = False

    async def start(self, device_id, opts):
        for _ in range(5):
            yield 0, b"f"

    async def stop(self):
        self.stop_called = True


async def _wait_for(cond, timeout=1.0):
    loop = asyncio.get_event_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        if cond():
            return True
        await asyncio.sleep(0.01)
    return False


@pytest.mark.asyncio
async def test_no_event_wait_task_leak_after_stream_consumed():
    """完整消费 N 帧后不得残留任何 Event.wait pending 任务。

    修复前：每帧泄漏 1 个（spike 实测 30/s），本断言失败；
    修复后：每轮 loser 被取消，残留归零。
    """
    baseline = _count_event_wait()
    svc = StreamService(encoder_factory=FiniteEncoder)

    frames = []
    async for f in svc.start_stream("dev1"):
        frames.append(f)
    assert len(frames) == 5

    ok = await _wait_for(lambda: _count_event_wait() == baseline)
    assert ok, f"残留 {_count_event_wait() - baseline} 个 Event.wait 任务未回收"