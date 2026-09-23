"""
StreamService.request_keyframe 关键帧请求测试（方案 19 实施项 3 + 终审修复）
===========================================================================

用假编码器验证：客户端 resume 回切时请求关键帧，
经 1s 防抖窗口合并重复请求；无活跃编码器时返回 False；
编码器侧无控制通道时 no-op 不抛异常。

终审修复（Important #2）：控制 socket 异常断开时发送会抛异常——
异常必须被吞掉返回 False（不得向上传播杀死 input 处理任务），
且失败不得占用 1s 防抖窗口（时间戳只在发送成功后记录）。
"""

from app.application.stream_service import StreamService
from app.infrastructure.stream.scrcpy import ScrcpyEncoder


class KFEncoder:
    """记录 request_keyframe 调用次数的假编码器。"""

    def __init__(self):
        self.calls = 0

    async def request_keyframe(self):
        self.calls += 1

    async def stop(self):
        pass


class FailingKFEncoder:
    """每次发送都抛异常（模拟控制 socket 已断开）。"""

    def __init__(self):
        self.calls = 0

    async def request_keyframe(self):
        self.calls += 1
        raise RuntimeError("control socket gone")


class FlakyKFEncoder:
    """首次发送抛异常、其后成功。"""

    def __init__(self):
        self.calls = 0

    async def request_keyframe(self):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("first send fails")


async def test_request_keyframe_debounced_1s():
    """1s 防抖：窗口内重复请求被合并，窗口外重新发送；
    未知设备返回 False。

    时序：now=100.0 发送（True）→ 100.5 窗口内拒绝（False）→
    101.1 窗口外再次发送（True）→ 共 2 次编码器调用；
    missing 设备无编码器 → False。
    """
    svc = StreamService()
    enc = KFEncoder()
    svc.encoders["dev"] = enc
    assert await svc.request_keyframe("dev", now=100.0) is True
    assert await svc.request_keyframe("dev", now=100.5) is False   # 防抖窗口内
    assert await svc.request_keyframe("dev", now=101.1) is True
    assert enc.calls == 2
    assert await svc.request_keyframe("missing", now=200.0) is False


async def test_encoder_request_keyframe_noop_without_control():
    """ScrcpyEncoder 无控制通道（_control_sender 为 None）时 no-op 不抛。"""
    enc = ScrcpyEncoder()
    await enc.request_keyframe()  # _control_sender 为 None → no-op 不抛


async def test_request_keyframe_send_failure_returns_false_and_retries():
    """发送异常被吞掉返回 False（终审修复：不向上传播杀死 input 任务）；
    失败不占防抖窗口——1s 窗口内重试仍会再次发送。"""
    svc = StreamService()
    enc = FailingKFEncoder()
    svc.encoders["dev"] = enc
    assert await svc.request_keyframe("dev", now=100.0) is False
    # 100.5 在「100.0 发送成功后」本应处于防抖窗口内：
    # 失败未记录时间戳（改为发送成功后记录）→ 仍重试发送
    assert await svc.request_keyframe("dev", now=100.5) is False
    assert enc.calls == 2


async def test_request_keyframe_failure_then_success_occupies_debounce():
    """失败不记录防抖时间戳；下一次成功才记录，此后 1s 窗口内请求被合并。"""
    svc = StreamService()
    enc = FlakyKFEncoder()
    svc.encoders["dev"] = enc
    assert await svc.request_keyframe("dev", now=100.0) is False  # 发送失败
    assert await svc.request_keyframe("dev", now=100.5) is True   # 失败未占窗口 → 成功
    assert await svc.request_keyframe("dev", now=100.9) is False  # 成功时间戳 100.5 → 防抖生效
    assert enc.calls == 2