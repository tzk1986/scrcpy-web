"""
Pytest 全局测试配置
====================

提供所有测试共享的 fixture 和配置。

主要功能：
    - 设置测试环境
    - 提供共享 fixture
    - 配置测试工具

使用方式：
    这些 fixture 在所有测试文件中自动可用，无需导入。
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "backend"))


# ---------------------------------------------------------------------------
# 基础 Fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    """
    FastAPI 测试客户端。

    使用 TestClient 模拟 HTTP 请求，无需启动真实服务器。
    """
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app)


@pytest.fixture
def mock_adb():
    """
    Mock ADB 驱动。

    模拟 AdbDriver 协议，返回预设的测试数据。
    用于单元测试中避免依赖真实 ADB 连接。
    """
    from app.domain.ports import AdbDriver

    mock = MagicMock(spec=AdbDriver)
    mock.list_devices = AsyncMock(return_value=["emulator-5554"])
    mock.get_device_info = AsyncMock()
    mock.shell = AsyncMock(return_value="mock output")
    return mock


@pytest.fixture
def mock_device_id():
    """
    模拟设备 ID。

    返回：
        str: 模拟的 ADB 设备序列号。
    """
    return "emulator-5554"


@pytest.fixture
def mock_session_id():
    """
    模拟会话 ID。

    返回：
        str: 模拟的调试会话 ID。
    """
    return "session_12345"


@pytest.fixture
def mock_encoder_opts():
    """
    模拟编码器配置。

    返回：
        EncoderOpts: 测试用的编码器配置对象。
    """
    from app.scrcpy.constants import EncoderOpts
    return EncoderOpts(
        max_size=720,
        bit_rate="2M",
        codec="h264",
        fps=30,
    )


@pytest.fixture
def event_loop():
    """
    创建事件循环用于异步测试。

    返回：
        asyncio.AbstractEventLoop: 新的事件循环。
    """
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ---------------------------------------------------------------------------
# Mock 工具 Fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_subprocess():
    """
    模拟 asyncio 子进程。

    返回：
        MagicMock: 配置好的模拟子进程对象。
    """
    mock_proc = MagicMock()
    mock_proc.pid = 12345
    mock_proc.returncode = 0

    # 模拟 stdout 和 stderr 管道
    mock_stdout = AsyncMock()
    mock_stderr = AsyncMock()
    mock_proc.stdout = mock_stdout
    mock_proc.stderr = mock_stderr

    # 模拟进程方法
    mock_proc.communicate = AsyncMock(return_value=(b"success", b""))
    mock_proc.wait = AsyncMock()
    mock_proc.kill = MagicMock()
    mock_proc.terminate = MagicMock()

    return mock_proc


@pytest.fixture
def mock_adb_success():
    """
    模拟成功的 ADB 命令执行。

    返回：
        AsyncMock: 模拟的 subprocess 创建函数。
    """
    async def create_mock_proc(*args, **kwargs):
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_proc.returncode = 0

        mock_stdout = AsyncMock()
        mock_stderr = AsyncMock()
        mock_proc.stdout = mock_stdout
        mock_proc.stderr = mock_stderr

        mock_proc.communicate = AsyncMock(return_value=(b"success", b""))
        mock_proc.wait = AsyncMock()
        mock_proc.kill = MagicMock()
        mock_proc.terminate = MagicMock()

        return mock_proc

    return create_mock_proc


@pytest.fixture
def mock_adb_failure():
    """
    模拟失败的 ADB 命令执行。

    返回：
        AsyncMock: 模拟的失败 subprocess 创建函数。
    """
    async def create_mock_proc(*args, **kwargs):
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_proc.returncode = 1

        mock_stdout = AsyncMock()
        mock_stderr = AsyncMock()
        mock_proc.stdout = mock_stdout
        mock_proc.stderr = mock_stderr

        mock_proc.communicate = AsyncMock(return_value=(b"", b"error message"))
        mock_proc.wait = AsyncMock()
        mock_proc.kill = MagicMock()
        mock_proc.terminate = MagicMock()

        return mock_proc

    return create_mock_proc


# ---------------------------------------------------------------------------
# 测试数据构建器
# ---------------------------------------------------------------------------

class H264DataBuilder:
    """
    H264 测试数据构建器。

    提供流式 API 生成自定义 H264 数据。

    使用示例：
        data = H264DataBuilder() \\
            .with_sps() \\
            .with_pps() \\
            .with_idr() \\
            .build()
    """

    def __init__(self):
        """初始化构建器，创建空缓冲区。"""
        self._buffer = bytearray()

    def with_sps(self, custom_data: bytes = None):
        """
        添加 SPS NALU。

        参数：
            custom_data: 自定义 SPS 数据（可选）。

        返回：
            H264DataBuilder: 构建器自身（支持链式调用）。
        """
        if custom_data is None:
            custom_data = b'\x42\x00\x1e\xab\x40\xa0\xfd\x00\xf0'
        self._buffer.extend(b'\x00\x00\x00\x01\x67')
        self._buffer.extend(custom_data)
        return self

    def with_pps(self, custom_data: bytes = None):
        """
        添加 PPS NALU。

        参数：
            custom_data: 自定义 PPS 数据（可选）。

        返回：
            H264DataBuilder: 构建器自身（支持链式调用）。
        """
        if custom_data is None:
            custom_data = b'\xce\x38\x80'
        self._buffer.extend(b'\x00\x00\x00\x01\x68')
        self._buffer.extend(custom_data)
        return self

    def with_idr(self, custom_data: bytes = None):
        """
        添加 IDR NALU（关键帧）。

        参数：
            custom_data: 自定义 IDR 数据（可选）。

        返回：
            H264DataBuilder: 构建器自身（支持链式调用）。
        """
        if custom_data is None:
            custom_data = b'\x88\x84\x00\xa0\xbd\x3c'
        self._buffer.extend(b'\x00\x00\x00\x01\x65')
        self._buffer.extend(custom_data)
        return self

    def with_non_idr(self, custom_data: bytes = None):
        """
        添加非 IDR NALU（P/B 帧）。

        参数：
            custom_data: 自定义非 IDR 数据（可选）。

        返回：
            H264DataBuilder: 构建器自身（支持链式调用）。
        """
        if custom_data is None:
            custom_data = b'\x9a\x24\x6c\xb0'
        self._buffer.extend(b'\x00\x00\x00\x01\x41')
        self._buffer.extend(custom_data)
        return self

    def build(self) -> bytes:
        """
        构建并返回 H264 数据。

        返回：
            bytes: 拼接好的 H264 数据流。
        """
        return bytes(self._buffer)


@pytest.fixture
def h264_builder():
    """
    H264 数据构建器 fixture。

    返回：
        H264DataBuilder: 数据构建器实例。
    """
    return H264DataBuilder()


# ---------------------------------------------------------------------------
# H264 数据 Fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_h264_sps():
    """
    示例 H264 SPS（序列参数集）。

    返回：
        bytes: 包含起始码的 SPS NALU。
    """
    return b'\x00\x00\x00\x01\x67\x42\x00\x1e\xab\x40\xa0\xfd\x00\xf0'


@pytest.fixture
def sample_h264_pps():
    """
    示例 H264 PPS（图像参数集）。

    返回：
        bytes: 包含起始码的 PPS NALU。
    """
    return b'\x00\x00\x00\x01\x68\xce\x38\x80'


@pytest.fixture
def sample_h264_idr():
    """
    示例 H264 IDR（关键帧）。

    返回：
        bytes: 包含起始码的 IDR NALU。
    """
    return b'\x00\x00\x00\x01\x65\x88\x84\x00\xa0\xbd\x3c'


@pytest.fixture
def sample_h264_stream(sample_h264_sps, sample_h264_pps, sample_h264_idr):
    """
    示例 H264 流（多个 NALU 拼接）。

    返回：
        bytes: 包含 SPS、PPS、IDR 的连续 H264 数据流。
    """
    return sample_h264_sps + sample_h264_pps + sample_h264_idr
