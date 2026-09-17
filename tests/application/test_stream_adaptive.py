"""
StreamService 自适应码率重启测试
==================================

用假编码器验证：客户端低 fps 上报 → 决策器降档 → 流不中断地
以新码率重启编码器（scrcpy 协议无运行中改码率，只能重启）。

覆盖：
    - 低 fps 触发一次重启，新编码器使用更低档码率，旧编码器被 stop
    - adaptive_bitrate=False 时 report 无效、不重启
    - 无活跃流时 report 不抛异常
"""

import asyncio

import pytest

from app.application.bitrate_advisor import parse_bit_rate
from app.application.stream_service import StreamService
from app.core.config import settings


class FakeEncoder:
    """按 1ms 节奏无限产帧的假编码器，记录实例供断言。"""

    created: list["FakeEncoder"] = []

    def __init__(self):
        self.opts = None
        self.stop_called = False
        FakeEncoder.created.append(self)

    async def start(self, device_id, opts):
        self.opts = opts
        while True:
            await asyncio.sleep(0.001)
            yield b"f"

    async def stop(self):
        self.stop_called = True


@pytest.fixture
def stream_settings(monkeypatch):
    s = settings()
    monkeypatch.setattr(s.stream, "adaptive_bitrate", True)
    monkeypatch.setattr(s.stream, "bit_rate", "4M")
    monkeypatch.setattr(s.stream, "bitrate_tiers", "8M,4M,2M,1M")
    monkeypatch.setattr(s.stream, "fps", 30)
    return s


async def _wait_for(cond, timeout=2.0):
    end = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < end:
        if cond():
            return True
        await asyncio.sleep(0.01)
    return False


@pytest.mark.asyncio
async def test_low_fps_restarts_encoder_at_lower_tier(stream_settings):
    FakeEncoder.created.clear()
    svc = StreamService(encoder_factory=FakeEncoder)

    async def consume():
        async for _ in svc.start_stream("dev1"):
            pass

    task = asyncio.create_task(consume())
    assert await _wait_for(lambda: len(FakeEncoder.created) == 1)

    # 8 个坏样本（fps=10 < 0.7*30）→ 决策降档 4M→2M
    for i in range(8):
        svc.report_client_fps("dev1", 10, now=float(i))

    assert await _wait_for(
        lambda: len(FakeEncoder.created) == 2 and FakeEncoder.created[1].opts is not None
    )
    assert parse_bit_rate(FakeEncoder.created[0].opts.bit_rate) == 4_000_000
    assert parse_bit_rate(FakeEncoder.created[1].opts.bit_rate) == 2_000_000
    assert FakeEncoder.created[0].stop_called is True

    await svc.stop_stream("dev1")
    await _wait_for(lambda: FakeEncoder.created[1].stop_called)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_disabled_adaptive_ignores_stats(stream_settings, monkeypatch):
    monkeypatch.setattr(stream_settings.stream, "adaptive_bitrate", False)
    FakeEncoder.created.clear()
    svc = StreamService(encoder_factory=FakeEncoder)

    async def consume():
        async for _ in svc.start_stream("dev1"):
            pass

    task = asyncio.create_task(consume())
    assert await _wait_for(lambda: len(FakeEncoder.created) == 1)

    for i in range(20):
        svc.report_client_fps("dev1", 5, now=float(i))
    await asyncio.sleep(0.1)
    assert len(FakeEncoder.created) == 1

    await svc.stop_stream("dev1")
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


def test_report_without_stream_is_noop(stream_settings):
    svc = StreamService(encoder_factory=FakeEncoder)
    svc.report_client_fps("ghost", 10, now=0.0)
    assert svc._advisors == {}


class FakeIdleEncoder(FakeEncoder):
    """只产 1 帧后挂起——模拟静止画面下 scrcpy 不再出帧。"""

    created: list["FakeIdleEncoder"] = []

    def __init__(self):
        self.opts = None
        self.stop_called = False
        self._never = asyncio.Event()
        FakeIdleEncoder.created.append(self)

    async def start(self, device_id, opts):
        self.opts = opts
        yield b"f0"
        await self._never.wait()


@pytest.mark.asyncio
async def test_restart_applies_even_when_stream_idle(stream_settings):
    """静止画面（无新帧）时也须立即应用码率切换，不能等下一帧。"""
    FakeIdleEncoder.created.clear()
    svc = StreamService(encoder_factory=FakeIdleEncoder)

    async def consume():
        async for _ in svc.start_stream("dev1"):
            pass

    task = asyncio.create_task(consume())
    assert await _wait_for(lambda: len(FakeIdleEncoder.created) == 1)

    for i in range(8):
        svc.report_client_fps("dev1", 10, now=float(i))

    # 帧循环此时阻塞在 __anext__，无新帧；事件驱动应仍完成重启
    assert await _wait_for(
        lambda: len(FakeIdleEncoder.created) == 2 and FakeIdleEncoder.created[1].opts is not None,
        timeout=2.0,
    )
    assert parse_bit_rate(FakeIdleEncoder.created[1].opts.bit_rate) == 2_000_000
    assert FakeIdleEncoder.created[0].stop_called is True

    await svc.stop_stream("dev1")
    await _wait_for(lambda: FakeIdleEncoder.created[1].stop_called)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
