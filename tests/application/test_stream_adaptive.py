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
from app.infrastructure.stream.scrcpy import EncoderStalledError


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
            yield 0, b"f"

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

    token = await svc.acquire_stream("dev1")
    assert token is not None

    async def consume():
        async for _ in svc.start_stream("dev1", token):
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

    token = await svc.acquire_stream("dev1")
    assert token is not None

    async def consume():
        async for _ in svc.start_stream("dev1", token):
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


def test_reports_dropped_while_bitrate_switch_pending(stream_settings):
    """切换 pending 未生效期间丢弃上报，复核窗不被重启过渡期样本污染（方案 21）。"""
    from app.application.bitrate_advisor import AdvisorConfig, BitrateAdvisor

    svc = StreamService(encoder_factory=FakeEncoder)
    svc._advisors["dev1"] = BitrateAdvisor(
        AdvisorConfig(
            tiers_bps=(8_000_000, 4_000_000, 2_000_000, 1_000_000),
            target_fps=30,
            start_bps=4_000_000,
        )
    )
    svc._pending_bitrate["dev1"] = 2_000_000
    for i in range(20):
        svc.report_client_fps("dev1", 10, now=float(i))
    # 样本全部被剔除：决策器窗口为空，冷却期后仍不产生任何切换
    # （对比：不加剔除时第 8 个坏样本触发降档、后续样本重新填满窗口，
    # 冷却期后会再降一档 2M→1M）
    assert svc._advisors["dev1"].decide(60.0) is None


@pytest.mark.asyncio
async def test_claim_clears_stale_pending_bitrate(stream_settings):
    """方案 34 D3c：claim 时清除残留 pending——旧流死亡前未消费的码率
    切换不得被新流首循环消费（05:40:59 实证：新流启动 1 秒内被强制重启）。"""
    FakeEncoder.created.clear()
    svc = StreamService(encoder_factory=FakeEncoder)
    svc._pending_bitrate["dev1"] = 1_000_000  # 旧会话残留的未消费切换

    token = await svc.acquire_stream("dev1")
    assert token is not None

    async def consume():
        async for _ in svc.start_stream("dev1", token):
            pass

    task = asyncio.create_task(consume())

    # 新流照常以起始档启动：残留 pending 被 claim 清除，不触发重启分支
    assert await _wait_for(lambda: len(FakeEncoder.created) == 1)
    await asyncio.sleep(0.2)  # 观察窗口：若误消费 pending 会立刻重启出第 2 台
    assert len(FakeEncoder.created) == 1
    assert FakeEncoder.created[0].opts.bit_rate == "4000000"
    assert svc._pending_bitrate.get("dev1") is None

    await svc.stop_stream("dev1")
    await _wait_for(lambda: FakeEncoder.created[0].stop_called)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


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
        yield 0, b"f0"
        await self._never.wait()


@pytest.mark.asyncio
async def test_restart_applies_even_when_stream_idle(stream_settings):
    """静止画面（无新帧）时也须立即应用码率切换，不能等下一帧。"""
    FakeIdleEncoder.created.clear()
    svc = StreamService(encoder_factory=FakeIdleEncoder)

    token = await svc.acquire_stream("dev1")
    assert token is not None

    async def consume():
        async for _ in svc.start_stream("dev1", token):
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
            yield 0, b"f"
            raise EncoderStalledError("stalled")

        async def stop(self):
            pass

    StallOnly.created = 0
    svc = StreamService(encoder_factory=StallOnly)
    token = await svc.acquire_stream("dev")
    assert token is not None
    gen = svc.start_stream("dev", token)
    frames = []
    with pytest.raises(EncoderStalledError):
        async for f in gen:
            frames.append(f)
    # 首次 + 3 次防风暴窗口内重启 = 4 台编码器、4 个首帧，随后异常传播
    assert frames == [(0, b"f")] * 4
    assert StallOnly.created == 4


class StallOnceThenIdleEncoder:
    """第 1 台实例产 1 帧后 stall；后续实例产 1 帧后挂起（模拟恢复后静止）。"""

    created: list["StallOnceThenIdleEncoder"] = []

    def __init__(self):
        self.opts = None
        self.stop_called = False
        self._never = asyncio.Event()
        StallOnceThenIdleEncoder.created.append(self)
        self.n = len(StallOnceThenIdleEncoder.created)

    async def start(self, device_id, opts):
        self.opts = opts
        yield 0, b"f"
        if self.n == 1:
            raise EncoderStalledError("stalled")
        await self._never.wait()

    async def stop(self):
        self.stop_called = True


@pytest.mark.asyncio
async def test_stall_restart_resets_advisor_and_sets_report_grace(stream_settings):
    """方案 34 D3b：stall 重启清空决策器样本 + 设置上报宽限期——
    黑屏期垃圾样本不再触发降档（放大级联根治）。"""
    import time

    StallOnceThenIdleEncoder.created.clear()
    svc = StreamService(encoder_factory=StallOnceThenIdleEncoder)

    token = await svc.acquire_stream("dev1")
    assert token is not None

    async def consume():
        async for _ in svc.start_stream("dev1", token):
            pass

    task = asyncio.create_task(consume())
    assert await _wait_for(lambda: len(StallOnceThenIdleEncoder.created) == 1)

    # stall 前注入 3 个坏样本（未满 8 窗，不触发降档；被 reset 清除也无妨）
    for i in range(3):
        svc.report_client_fps("dev1", 10, now=float(i))

    # 首实例 stall → 自愈重启第 2 台 + 写入宽限期
    assert await _wait_for(lambda: len(StallOnceThenIdleEncoder.created) == 2)
    grace_until = svc._report_grace_until.get("dev1")
    assert grace_until is not None and grace_until > time.monotonic()

    # 宽限期内上报被丢弃：样本窗空、无任何决策
    advisor = svc._advisors["dev1"]
    for i in range(8):
        svc.report_client_fps("dev1", 10, now=grace_until - 5.0 + i)
    assert advisor.decide(grace_until + 9.0) is None

    # 宽限期后上报恢复：8 个坏样本触发降档（第 3 台编码器，2M）
    t0 = grace_until + 1.0
    for i in range(8):
        svc.report_client_fps("dev1", 10, now=t0 + i)
    assert await _wait_for(lambda: len(StallOnceThenIdleEncoder.created) == 3)
    assert parse_bit_rate(StallOnceThenIdleEncoder.created[2].opts.bit_rate) == 2_000_000

    await svc.stop_stream("dev1")
    await _wait_for(lambda: StallOnceThenIdleEncoder.created[2].stop_called)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


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
async def test_acquire_waits_for_active_stream_cleanup(stream_settings, monkeypatch):
    """已活跃 guard（方案 19 终审修复，方案 34 D4 迁至 acquire）：
    旧流收尾期间等待而非立即拒绝；收尾完成即放行新流。guard 的 1s
    等待被 probe 拦截以加速验证。"""
    FakeEncoder.created.clear()
    svc = StreamService(encoder_factory=FakeEncoder)
    svc.active_streams["dev1"] = True  # 模拟旧流尚未收尾

    probe = _GuardSleepProbe()
    probe.on_first_guard_wait = lambda: svc.active_streams.__setitem__(
        "dev1", False)  # 第 1 次等待后旧流收尾完成（其 finally 清理 active_streams）
    monkeypatch.setattr("app.application.stream_service.asyncio.sleep", probe)

    token = await svc.acquire_stream("dev1")
    assert token is not None
    assert probe.guard_waits == [1.0]  # 恰好等待一轮 1s 后放行

    async def consume():
        async for _ in svc.start_stream("dev1", token):
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


@pytest.mark.asyncio
async def test_acquire_rejects_after_three_waits(stream_settings, monkeypatch):
    """已活跃 guard（方案 34 D4）：3×1s 后仍活跃 → acquire 返回 None，
    拒绝且不 claim、不创建编码器、不改变既有活跃标记。"""
    FakeEncoder.created.clear()
    svc = StreamService(encoder_factory=FakeEncoder)
    svc.active_streams["dev1"] = True  # 旧流在 3s 窗口内未收尾

    probe = _GuardSleepProbe()
    monkeypatch.setattr("app.application.stream_service.asyncio.sleep", probe)

    token = await svc.acquire_stream("dev1")
    assert token is None
    assert probe.guard_waits == [1.0, 1.0, 1.0]
    assert FakeEncoder.created == []
    assert svc.active_streams.get("dev1") is True  # 拒绝不得动已有标记


@pytest.mark.asyncio
async def test_acquire_no_wait_when_previous_stream_stopping(stream_settings, monkeypatch):
    """旧流已置 False（收尾中、finally 未跑完）→ 不等待直接放行。"""
    FakeEncoder.created.clear()
    svc = StreamService(encoder_factory=FakeEncoder)
    svc.active_streams["dev1"] = False  # 旧流收尾中（stop_stream 已置位）

    probe = _GuardSleepProbe()

    async def no_guard_wait(t):
        if t >= 1.0:
            raise AssertionError("stop 中不应等待")
        await probe.real_sleep(t)

    monkeypatch.setattr("app.application.stream_service.asyncio.sleep", no_guard_wait)

    token = await svc.acquire_stream("dev1")
    assert token is not None

    async def consume():
        async for _ in svc.start_stream("dev1", token):
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
        yield 0, b"f0"
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

    old_token = await svc.acquire_stream("dev1")
    assert old_token is not None
    old_gen = svc.start_stream("dev1", old_token)
    assert await old_gen.__anext__() == (0, b"f0")
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

    token = await svc.acquire_stream("dev1")
    assert token is not None
    gen = svc.start_stream("dev1", token)
    assert await gen.__anext__() == (0, b"f0")
    enc = svc.get_encoder("dev1")
    assert enc is not None

    enc.stop_release.set()  # 不挂起：走常规 stop 收尾
    await gen.aclose()

    assert svc.encoders.get("dev1") is None
    assert svc.active_streams.get("dev1") is None
    assert svc.get_active_streams() == []
    assert svc.get_encoder("dev1") is None


# ---------------------------------------------------------------------------
# 方案 34 D4：acquire/release token 持有制（防并发踩停）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_second_client_acquire_rejected_without_stomping_first(stream_settings, monkeypatch):
    """并发踩停回归锚点：第二 client acquire 得 None（被拒），其退出
    （无 token 可 release）不得影响第一流——活跃标记仍为第一 token、
    帧循环持续产帧。旧行为：WS finally 无条件 stop_stream 写 False
    踩停第一流，双端互踩成 ping-pong 重启循环（05:41-05:43 实证）。"""
    FakeEncoder.created.clear()
    svc = StreamService(encoder_factory=FakeEncoder)

    probe = _GuardSleepProbe()
    monkeypatch.setattr("app.application.stream_service.asyncio.sleep", probe)

    token1 = await svc.acquire_stream("dev1")
    assert token1 is not None
    assert svc.active_streams.get("dev1") is token1

    frames: list[tuple[int, bytes]] = []

    async def consume():
        async for f in svc.start_stream("dev1", token1):
            frames.append(f)

    task = asyncio.create_task(consume())
    assert await _wait_for(lambda: len(FakeEncoder.created) == 1)

    # 第二 client acquire：3×1s 守卫后仍活跃 → 拒绝
    token2 = await svc.acquire_stream("dev1")
    assert token2 is None
    assert probe.guard_waits == [1.0, 1.0, 1.0]

    # 被拒者不触达任何停流操作：第一流保持活跃、无重启、持续产帧
    n_frames = len(frames)
    await asyncio.sleep(0.05)
    assert svc.active_streams.get("dev1") is token1
    assert svc.get_active_streams() == ["dev1"]
    assert len(FakeEncoder.created) == 1  # 无第二次编码器（无重启风暴）
    assert len(frames) > n_frames          # 帧序列不断流

    await svc.release_stream("dev1", token1)
    assert svc.active_streams.get("dev1") is False
    await _wait_for(lambda: FakeEncoder.created[0].stop_called)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_release_with_stale_token_is_noop(stream_settings):
    """release 按 token 身份守卫：旧会话 token 在新会话已 claim 后释放为
    no-op——不置停、不踩停新流；持有者释放则正常置停。
    （不变量：只有持有 token 者能停流，方案 34 D4。）"""
    svc = StreamService(encoder_factory=FakeEncoder)
    token1 = await svc.acquire_stream("dev1")
    assert token1 is not None

    new_token = object()
    svc.active_streams["dev1"] = new_token  # 新会话已 claim（重连竞态）
    await svc.release_stream("dev1", token1)  # 旧 token → no-op
    assert svc.active_streams.get("dev1") is new_token
    assert svc.get_active_streams() == ["dev1"]

    await svc.release_stream("dev1", new_token)  # 持有者释放 → 置停
    assert svc.active_streams.get("dev1") is False
