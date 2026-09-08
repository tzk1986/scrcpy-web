"""
视频流服务集成测试
==================

测试视频流服务的架构和接口。

注意：由于 scrcpy-server 集成仍在调试中，
这些测试主要验证架构正确性而非完整功能。
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.application.stream_service import StreamService
from app.domain.ports import EncoderOpts


class TestStreamService:
    """测试 StreamService"""

    @pytest.mark.asyncio
    async def test_service_initialization(self):
        """测试服务初始化"""
        service = StreamService()
        assert len(service.get_active_streams()) == 0
        assert len(service.encoders) == 0

    @pytest.mark.asyncio
    async def test_start_stream_creates_encoder(self):
        """测试启动流时创建编码器"""
        service = StreamService()

        # Mock ScrcpyEncoder
        with patch('app.application.stream_service.ScrcpyEncoder') as MockEncoder:
            mock_encoder = MagicMock()
            # start 返回异步生成器
            mock_encoder.start = MagicMock(return_value=self._mock_frame_generator())
            mock_encoder.stop = AsyncMock()
            MockEncoder.return_value = mock_encoder

            # 启动流（会立即返回，因为生成器为空）
            async for frame in service.start_stream("test-device"):
                pass

            # 验证编码器被创建
            MockEncoder.assert_called_once()
            # 验证编码器被启动
            mock_encoder.start.assert_called_once()
            # 验证编码器被停止
            mock_encoder.stop.assert_called_once()

    def _mock_frame_generator(self, count: int = 3):
        """生成模拟帧的异步生成器"""
        async def generator():
            for i in range(count):
                yield b"\x00\x00\x00\x01" + bytes([i] * 100)
                await asyncio.sleep(0.01)
        return generator()


class TestStreamServiceIntegration:
    """StreamService 集成测试（需要真实设备）"""

    @pytest.mark.asyncio
    async def test_stream_service_with_real_encoder(self):
        """测试使用真实编码器的流服务"""
        # 这个测试需要 scrcpy-server 正常工作
        # 目前跳过，等待 scrcpy-server 集成完成
        pytest.skip("scrcpy-server integration not ready")

    @pytest.mark.asyncio
    async def test_multi_device_real(self):
        """测试真实多设备流"""
        # 这个测试需要多个真实设备
        # 目前跳过
        pytest.skip("Requires multiple real devices")
