"""
视频编码器测试
===============

测试 scrcpy-server 视频编码器的功能。

测试内容：
    - 编码器启动和停止
    - H264 帧读取
    - NALU 解析
    - 错误处理
    - 资源清理
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import asyncio

from app.scrcpy.encoder import ScrcpyEncoder
from app.scrcpy.constants import (
    EncoderOpts,
    DEFAULT_ENCODER_OPTS,
    SCRCPY_SERVER_REMOTE_PATH,
    SCRCPY_SERVER_CLASS,
    SCRCPY_SERVER_VERSION,
)


# ---------------------------------------------------------------------------
# 编码器启动测试
# ---------------------------------------------------------------------------

class TestEncoderStart:
    """编码器启动测试"""

    @pytest.mark.asyncio
    async def test_start_encoder_success(self, mock_device_id, mock_encoder_opts):
        """测试成功启动编码器"""
        encoder = ScrcpyEncoder()

        # Mock ServerManager
        with patch.object(encoder._server_manager, 'ensure_server', return_value=True):
            # Mock subprocess
            mock_proc = MagicMock()
            mock_proc.pid = 12345
            mock_proc.returncode = None
            mock_proc.stdout = AsyncMock()
            mock_proc.stderr = AsyncMock()

            # 模拟读取一帧数据后结束
            mock_proc.stdout.read = AsyncMock(side_effect=[
                b'\x00\x00\x00\x01\x67\x42\x00\x1e',  # SPS
                b'',  # EOF
            ])

            with patch('asyncio.create_subprocess_exec', return_value=mock_proc):
                # 启动编码器
                frames = []
                async for frame in encoder.start(mock_device_id, mock_encoder_opts):
                    frames.append(frame)
                    break  # 只读取一帧

                # 应该读取到至少一帧
                assert len(frames) > 0

    @pytest.mark.asyncio
    async def test_start_encoder_server_deploy_failed(self, mock_device_id, mock_encoder_opts):
        """测试 server 部署失败"""
        encoder = ScrcpyEncoder()

        # Mock ServerManager 返回 False
        with patch.object(encoder._server_manager, 'ensure_server', return_value=False):
            with pytest.raises(RuntimeError, match="Failed to deploy"):
                async for _ in encoder.start(mock_device_id, mock_encoder_opts):
                    pass

    @pytest.mark.asyncio
    async def test_start_encoder_process_failed(self, mock_device_id, mock_encoder_opts):
        """测试启动进程失败"""
        encoder = ScrcpyEncoder()

        # Mock ServerManager
        with patch.object(encoder._server_manager, 'ensure_server', return_value=True):
            # Mock subprocess（启动后立即退出）
            mock_proc = MagicMock()
            mock_proc.pid = 12345
            mock_proc.returncode = 1  # 启动失败
            mock_proc.stderr = AsyncMock()
            mock_proc.stderr.read = AsyncMock(return_value=b"error message")

            with patch('asyncio.create_subprocess_exec', return_value=mock_proc):
                with pytest.raises(RuntimeError, match="failed to start"):
                    async for _ in encoder.start(mock_device_id, mock_encoder_opts):
                        pass


# ---------------------------------------------------------------------------
# 编码器停止测试
# ---------------------------------------------------------------------------

class TestEncoderStop:
    """编码器停止测试"""

    @pytest.mark.asyncio
    async def test_stop_encoder(self, mock_device_id, mock_encoder_opts):
        """测试停止编码器"""
        encoder = ScrcpyEncoder()

        # Mock ServerManager
        with patch.object(encoder._server_manager, 'ensure_server', return_value=True):
            # Mock subprocess
            mock_proc = MagicMock()
            mock_proc.pid = 12345
            mock_proc.returncode = None
            mock_proc.stdout = AsyncMock()
            mock_proc.stdout.read = AsyncMock(return_value=b'')
            mock_proc.terminate = MagicMock()
            mock_proc.wait = AsyncMock()

            with patch('asyncio.create_subprocess_exec', return_value=mock_proc):
                # 启动并立即停止
                async for _ in encoder.start(mock_device_id, mock_encoder_opts):
                    break

                # 验证进程被终止
                mock_proc.terminate.assert_called()

    @pytest.mark.asyncio
    async def test_stop_encoder_force_kill(self, mock_device_id, mock_encoder_opts):
        """测试强制终止编码器"""
        encoder = ScrcpyEncoder()

        # Mock ServerManager
        with patch.object(encoder._server_manager, 'ensure_server', return_value=True):
            # Mock subprocess（terminate 超时）
            mock_proc = MagicMock()
            mock_proc.pid = 12345
            mock_proc.returncode = None
            mock_proc.stdout = AsyncMock()
            mock_proc.stdout.read = AsyncMock(return_value=b'')
            mock_proc.terminate = MagicMock()
            mock_proc.wait = AsyncMock(side_effect=asyncio.TimeoutError())
            mock_proc.kill = MagicMock()

            with patch('asyncio.create_subprocess_exec', return_value=mock_proc):
                async for _ in encoder.start(mock_device_id, mock_encoder_opts):
                    break

                # 验证强制终止
                mock_proc.kill.assert_called()


# ---------------------------------------------------------------------------
# H264 帧读取测试
# ---------------------------------------------------------------------------

class TestFrameReading:
    """H264 帧读取测试"""

    @pytest.mark.asyncio
    async def test_read_single_frame(self, mock_device_id, mock_encoder_opts):
        """测试读取单帧"""
        encoder = ScrcpyEncoder()

        # Mock ServerManager
        with patch.object(encoder._server_manager, 'ensure_server', return_value=True):
            # Mock subprocess
            mock_proc = MagicMock()
            mock_proc.pid = 12345
            mock_proc.returncode = None
            mock_proc.stdout = AsyncMock()
            mock_proc.stderr = AsyncMock()

            # 模拟读取一帧 SPS
            mock_proc.stdout.read = AsyncMock(side_effect=[
                b'\x00\x00\x00\x01\x67\x42\x00\x1e',  # SPS
                b'\x00\x00\x00\x01',  # 下一个起始码（触发解析）
                b'',  # EOF
            ])

            with patch('asyncio.create_subprocess_exec', return_value=mock_proc):
                frames = []
                async for frame in encoder.start(mock_device_id, mock_encoder_opts):
                    frames.append(frame)

                # 应该读取到一帧
                assert len(frames) == 1
                assert frames[0].startswith(b'\x00\x00\x00\x01\x67')

    @pytest.mark.asyncio
    async def test_read_multiple_frames(self, mock_device_id, mock_encoder_opts):
        """测试读取多帧"""
        encoder = ScrcpyEncoder()

        # Mock ServerManager
        with patch.object(encoder._server_manager, 'ensure_server', return_value=True):
            # Mock subprocess
            mock_proc = MagicMock()
            mock_proc.pid = 12345
            mock_proc.returncode = None
            mock_proc.stdout = AsyncMock()
            mock_proc.stderr = AsyncMock()

            # 模拟读取多帧
            sps = b'\x00\x00\x00\x01\x67\x42\x00\x1e'
            pps = b'\x00\x00\x00\x01\x68\xce\x38\x80'
            idr = b'\x00\x00\x00\x01\x65\x88\x84\x00'

            mock_proc.stdout.read = AsyncMock(side_effect=[
                sps + pps + idr,
                b'\x00\x00\x00\x01',  # 触发解析
                b'',  # EOF
            ])

            with patch('asyncio.create_subprocess_exec', return_value=mock_proc):
                frames = []
                async for frame in encoder.start(mock_device_id, mock_encoder_opts):
                    frames.append(frame)

                # 应该读取到三帧
                assert len(frames) == 3

    @pytest.mark.asyncio
    async def test_read_frame_split_across_chunks(self, mock_device_id, mock_encoder_opts):
        """测试帧跨越多个数据块"""
        encoder = ScrcpyEncoder()

        # Mock ServerManager
        with patch.object(encoder._server_manager, 'ensure_server', return_value=True):
            # Mock subprocess
            mock_proc = MagicMock()
            mock_proc.pid = 12345
            mock_proc.returncode = None
            mock_proc.stdout = AsyncMock()
            mock_proc.stderr = AsyncMock()

            # 一帧数据分成两个块
            frame = b'\x00\x00\x00\x01\x67\x42\x00\x1e\xab\x40\xa0'

            mock_proc.stdout.read = AsyncMock(side_effect=[
                frame[:10],  # 第一部分
                frame[10:],  # 第二部分
                b'\x00\x00\x00\x01',  # 触发解析
                b'',  # EOF
            ])

            with patch('asyncio.create_subprocess_exec', return_value=mock_proc):
                frames = []
                async for frame in encoder.start(mock_device_id, mock_encoder_opts):
                    frames.append(frame)

                # 应该正确重组并读取到一帧
                assert len(frames) == 1


# ---------------------------------------------------------------------------
# 编码器状态测试
# ---------------------------------------------------------------------------

class TestEncoderState:
    """编码器状态测试"""

    @pytest.mark.asyncio
    async def test_is_running(self, mock_device_id, mock_encoder_opts):
        """测试运行状态检查"""
        encoder = ScrcpyEncoder()

        # 初始状态
        assert not encoder.is_running()

        # Mock ServerManager
        with patch.object(encoder._server_manager, 'ensure_server', return_value=True):
            # Mock subprocess
            mock_proc = MagicMock()
            mock_proc.pid = 12345
            mock_proc.returncode = None
            mock_proc.stdout = AsyncMock()
            mock_proc.stdout.read = AsyncMock(return_value=b'')
            mock_proc.terminate = MagicMock()
            mock_proc.wait = AsyncMock()

            with patch('asyncio.create_subprocess_exec', return_value=mock_proc):
                # 启动编码器
                async for _ in encoder.start(mock_device_id, mock_encoder_opts):
                    # 在运行中
                    assert encoder.is_running()
                    break

                # 停止后
                assert not encoder.is_running()

    @pytest.mark.asyncio
    async def test_get_pid(self, mock_device_id, mock_encoder_opts):
        """测试获取进程 PID"""
        encoder = ScrcpyEncoder()

        # 未启动时
        assert encoder.get_pid() is None

        # Mock ServerManager
        with patch.object(encoder._server_manager, 'ensure_server', return_value=True):
            # Mock subprocess
            mock_proc = MagicMock()
            mock_proc.pid = 12345
            mock_proc.returncode = None
            mock_proc.stdout = AsyncMock()
            mock_proc.stdout.read = AsyncMock(return_value=b'')
            mock_proc.terminate = MagicMock()
            mock_proc.wait = AsyncMock()

            with patch('asyncio.create_subprocess_exec', return_value=mock_proc):
                async for _ in encoder.start(mock_device_id, mock_encoder_opts):
                    # 获取 PID
                    pid = await encoder.get_pid()
                    assert pid == 12345
                    break


# ---------------------------------------------------------------------------
# 编码器配置测试
# ---------------------------------------------------------------------------

class TestEncoderConfig:
    """编码器配置测试"""

    @pytest.mark.asyncio
    async def test_encoder_opts_used(self, mock_device_id):
        """测试编码器配置被正确使用"""
        encoder = ScrcpyEncoder()

        # 使用自定义配置
        custom_opts = EncoderOpts(
            max_size=720,
            bit_rate="2M",
            codec="h264",
            fps=60,
        )

        # Mock ServerManager
        with patch.object(encoder._server_manager, 'ensure_server', return_value=True):
            # Mock subprocess
            mock_proc = MagicMock()
            mock_proc.pid = 12345
            mock_proc.returncode = None
            mock_proc.stdout = AsyncMock()
            mock_proc.stdout.read = AsyncMock(return_value=b'')
            mock_proc.terminate = MagicMock()
            mock_proc.wait = AsyncMock()

            with patch('asyncio.create_subprocess_exec', return_value=mock_proc) as mock_exec:
                async for _ in encoder.start(mock_device_id, custom_opts):
                    break

                # 验证命令参数包含配置值
                call_args = mock_exec.call_args[0]
                assert any("max_size=720" in str(arg) for arg in call_args)
                assert any("bit_rate=2M" in str(arg) for arg in call_args)
                assert any("max_fps=60" in str(arg) for arg in call_args)


# ---------------------------------------------------------------------------
# 资源清理测试
# ---------------------------------------------------------------------------

class TestResourceCleanup:
    """资源清理测试"""

    @pytest.mark.asyncio
    async def test_cleanup_on_exception(self, mock_device_id, mock_encoder_opts):
        """测试异常时资源清理"""
        encoder = ScrcpyEncoder()

        # Mock ServerManager
        with patch.object(encoder._server_manager, 'ensure_server', return_value=True):
            # Mock subprocess
            mock_proc = MagicMock()
            mock_proc.pid = 12345
            mock_proc.returncode = None
            mock_proc.stdout = AsyncMock()
            mock_proc.stderr = AsyncMock()
            mock_proc.terminate = MagicMock()
            mock_proc.wait = AsyncMock()

            # 模拟读取时抛出异常
            mock_proc.stdout.read = AsyncMock(side_effect=Exception("Read error"))

            with patch('asyncio.create_subprocess_exec', return_value=mock_proc):
                with pytest.raises(Exception, match="Read error"):
                    async for _ in encoder.start(mock_device_id, mock_encoder_opts):
                        pass

                # 验证资源被清理
                mock_proc.terminate.assert_called()

    @pytest.mark.asyncio
    async def test_cleanup_on_generator_exit(self, mock_device_id, mock_encoder_opts):
        """测试生成器退出时资源清理"""
        encoder = ScrcpyEncoder()

        # Mock ServerManager
        with patch.object(encoder._server_manager, 'ensure_server', return_value=True):
            # Mock subprocess
            mock_proc = MagicMock()
            mock_proc.pid = 12345
            mock_proc.returncode = None
            mock_proc.stdout = AsyncMock()
            mock_proc.stdout.read = AsyncMock(return_value=b'')
            mock_proc.terminate = MagicMock()
            mock_proc.wait = AsyncMock()

            with patch('asyncio.create_subprocess_exec', return_value=mock_proc):
                # 创建生成器但不完全消费
                gen = encoder.start(mock_device_id, mock_encoder_opts)

                # 读取一帧
                await gen.__anext__()

                # 关闭生成器
                await gen.aclose()

                # 验证资源被清理
                mock_proc.terminate.assert_called()
