"""方案 58 B-03：raw_stream_fallback 三处读取口径统一为设备视图。

覆盖：
    - use_packet_protocol 消费 device_stream_settings(device_id) 视图（不再直读全局）
    - start_stream 守卫冲突抛领域异常 StreamConfigConflictError
      （message 用户可读，经 video.py OpenScrcpyException 分支直达客户端）
"""

from types import SimpleNamespace

import pytest

from app.application.stream_service import StreamService
from app.core.config import settings
from app.core.exceptions import StreamConfigConflictError


class NoopEncoder:
    """最小假编码器：一帧即停。"""

    async def start(self, device_id, opts):
        yield 0, b"f"

    async def stop(self):
        pass


def test_use_packet_protocol_consumes_device_view(monkeypatch):
    seen: list[str] = []

    def fake_view(device_id: str):
        seen.append(device_id)
        return SimpleNamespace(raw_stream_fallback=device_id == "dev-raw")

    monkeypatch.setattr(
        "app.application.stream_service.device_stream_settings", fake_view
    )
    svc = StreamService(encoder_factory=NoopEncoder)
    assert svc.use_packet_protocol("dev-raw") is False
    assert svc.use_packet_protocol("dev-normal") is True
    assert seen == ["dev-raw", "dev-normal"]


@pytest.mark.asyncio
async def test_start_stream_guard_raises_domain_exception(monkeypatch):
    s = settings()
    monkeypatch.setattr(s.stream, "raw_stream_fallback", True)
    monkeypatch.setattr(s.stream, "codec", "h265")
    svc = StreamService(encoder_factory=NoopEncoder)
    token = await svc.acquire_stream("dev1")
    with pytest.raises(StreamConfigConflictError) as ei:
        async for _ in svc.start_stream("dev1", token):
            pass
    assert ei.value.code == "STREAM_CONFIG_CONFLICT"
    assert "h265" in ei.value.message
