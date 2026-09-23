"""
StreamService.request_keyframe 关键帧请求测试（方案 19 实施项 3）
================================================================

用假编码器验证：客户端 resume 回切时请求关键帧，
经 1s 防抖窗口合并重复请求；无活跃编码器时返回 False；
编码器侧无控制通道时 no-op 不抛异常。
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