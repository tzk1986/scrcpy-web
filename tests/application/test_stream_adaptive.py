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


@pytest.mark.asyncio
async def test_stall_recovery_and_storm_guard(stream_settings, monkeypatch):
    """卡死自愈：EncoderStalledError → 重建编码器续流；60s 窗口内最多 3 次，超限上抛。"""
    from app.infrastructure.stream.scrcpy import EncoderStalledError

    async def fake_sleep(t):
        pass

    monkeypatch.setattr("app.application.stream_service.asyncio.sleep", fake_sleep)

    class StallOnly:
        created = 0

        def __init__(self):
            StallOnly.created += 1
            self.n = StallOnly.created

        async def start(self, device_id, opts):
            yield b"f"
            raise EncoderStalledError("stalled")

        async def stop(self):
            pass

    StallOnly.created = 0
    svc = StreamService(encoder_factory=StallOnly)
    gen = svc.start_stream("dev")
    frames = []
    with pytest.raises(EncoderStalledError):
        async for f in gen:
            frames.append(f)
    # 首次 + 3 次防风暴窗口内重启 = 4 台编码器、4 个首帧，随后异常传播
    assert frames == [b"f"] * 4
    assert StallOnly.created == 4


class _GuardSleepProbe:
    """拦截 guard 的 1s 等待、放行其余短 sleep（monkeypatch 会覆盖全局
    asyncio.sleep，测试助手 _wait_for 与 FakeEncoder 的短 sleep 须转发真实现）。"""

    def __init__(self):
        self.real_sleep = asyncio.sleep
        self.guard_waits: list[float] = []
        self.on_first_guard_wait = None

    async def __call__(self, t):
        if t >= 1.0:
            self.guard_waits.append(t)
            if self.on_first_guard_wait is not None:
                self.on_first_guard_wait()
        else:
            await self.real_sleep(t)


@pytest.mark.asyncio
async def test_start_stream_waits_for_active_stream_cleanup(stream_settings, monkeypatch):
    """已活跃 guard（方案 19 终审修复）：旧流收尾期间等待而非立即拒绝；
    收尾完成即放行新流。guard 的 1s 等待被 probe 拦截以加速验证。"""
    FakeEncoder.created.clear()
    svc = StreamService(encoder_factory=FakeEncoder)
    svc.active_streams["dev1"] = True  # 模拟旧流尚未收尾

    probe = _GuardSleepProbe()
    probe.on_first_guard_wait = lambda: svc.active_streams.__setitem__(
        "dev1", False)  # 第 1 次等待后旧流收尾完成（其 finally 清理 active_streams）
    monkeypatch.setattr("app.application.stream_service.asyncio.sleep", probe)

    async def consume():
        async for _ in svc.start_stream("dev1"):
            pass

    task = asyncio.create_task(consume())
    assert await _wait_for(lambda: len(FakeEncoder.created) == 1)
    assert probe.guard_waits == [1.0]  # 恰好等待一轮 1s 后放行

    await svc.stop_stream("dev1")
    await _wait_for(lambda: FakeEncoder.created[0].stop_called)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_start_stream_rejects_after_three_waits(stream_settings, monkeypatch):
    """已活跃 guard：3×1s 后仍活跃 → 维持原有拒绝行为
    （生成器不产帧，不创建编码器）。"""
    FakeEncoder.created.clear()
    svc = StreamService(encoder_factory=FakeEncoder)
    svc.active_streams["dev1"] = True  # 旧流在 3s 窗口内未收尾

    probe = _GuardSleepProbe()
    monkeypatch.setattr("app.application.stream_service.asyncio.sleep", probe)

    frames = [f async for f in svc.start_stream("dev1")]
    assert frames == []
    assert probe.guard_waits == [1.0, 1.0, 1.0]
    assert FakeEncoder.created == []


@pytest.mark.asyncio
async def test_start_stream_no_wait_when_previous_stream_stopping(stream_settings, monkeypatch):
    """旧流已置 False（stop_stream 已调用、finally 未跑完）→ 不等待直接放行。"""
    FakeEncoder.created.clear()
    svc = StreamService(encoder_factory=FakeEncoder)
    svc.active_streams["dev1"] = False  # 旧流收尾中（stop_stream 已置位）

    probe = _GuardSleepProbe()

    async def no_guard_wait(t):
        if t >= 1.0:
            raise AssertionError("stop 中不应等待")
        await probe.real_sleep(t)

    monkeypatch.setattr("app.application.stream_service.asyncio.sleep", no_guard_wait)

    async def consume():
        async for _ in svc.start_stream("dev1"):
            pass

    task = asyncio.create_task(consume())
    assert await _wait_for(lambda: len(FakeEncoder.created) == 1)

    await svc.stop_stream("dev1")
    await _wait_for(lambda: FakeEncoder.created[0].stop_called)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


class BlockingStopEncoder:
    """产 1 帧后挂起（模拟静止设备无保活帧可出）；stop() 挂起直至显式放行——
    模拟旧流 teardown 挂在编码器收尾窗口（R2 竞态编排用）。"""

    created: list["BlockingStopEncoder"] = []

    def __init__(self):
        self.opts = None
        self.stop_called = False
        self.stop_release = asyncio.Event()
        self.keyframe_requests = 0
        self._never = asyncio.Event()
        BlockingStopEncoder.created.append(self)

    async def start(self, device_id, opts):
        self.opts = opts
        yield b"f0"
        await self._never.wait()

    async def stop(self):
        self.stop_called = True
        await self.stop_release.wait()

    async def request_keyframe(self):
        self.keyframe_requests += 1


@pytest.mark.asyncio
async def test_old_stream_teardown_does_not_evict_new_stream(stream_settings):
    """
    重连竞态（R2）：旧流 teardown 挂起期间新流条目写入注册表，
    旧流 teardown 继续执行后不得误弹新流的 encoder / active 条目。

    编排（无真实长 sleep）：旧流产 1 帧 → aclose 触发 finally（stop 挂起）
    → 模拟重连新流在收尾窗口内经 guard 放行写入自己的条目 → 放行旧
    teardown → 断言新条目仍在，input 路由与 request_keyframe 可用。
    """
    BlockingStopEncoder.created.clear()
    svc = StreamService(encoder_factory=BlockingStopEncoder)

    old_gen = svc.start_stream("dev1")
    assert await old_gen.__anext__() == b"f0"
    old_enc = svc.get_encoder("dev1")
    assert old_enc is not None and isinstance(old_enc, BlockingStopEncoder)

    # 旧流下线：teardown 进入 finally 并挂在 encoder.stop()（等保活帧收尾）
    close_task = asyncio.create_task(old_gen.aclose())
    assert await _wait_for(lambda: old_enc.stop_called)

    # 重连的新流在收尾窗口内经 guard 放行，写入自己的注册条目
    new_enc = BlockingStopEncoder()
    new_token = object()
    svc.encoders["dev1"] = new_enc
    svc.active_streams["dev1"] = new_token

    old_enc.stop_release.set()  # 放行旧 teardown 继续执行（identity 守卫生效点）
    await close_task

    # 新流条目未被误弹：input 路由与 request_keyframe 仍可用
    assert svc.encoders.get("dev1") is new_enc
    assert svc.active_streams.get("dev1") is new_token
    assert svc.get_active_streams() == ["dev1"]
    assert svc.get_encoder("dev1") is new_enc
    assert await svc.request_keyframe("dev1") is True
    assert new_enc.keyframe_requests == 1
    assert old_enc.stop_called is True


@pytest.mark.asyncio
async def test_teardown_cleans_own_registry_entries(stream_settings):
    """无交叠时 identity 守卫不破坏既有清理语义：teardown 后注册表清空。"""
    BlockingStopEncoder.created.clear()
    svc = StreamService(encoder_factory=BlockingStopEncoder)

    gen = svc.start_stream("dev1")
    assert await gen.__anext__() == b"f0"
    enc = svc.get_encoder("dev1")
    assert enc is not None

    enc.stop_release.set()  # 不挂起：走常规 stop 收尾
    await gen.aclose()

    assert svc.encoders.get("dev1") is None
    assert svc.active_streams.get("dev1") is None
    assert svc.get_active_streams() == []
    assert svc.get_encoder("dev1") is None
