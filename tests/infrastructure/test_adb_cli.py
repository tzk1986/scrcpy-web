"""
ADB CLI 驱动测试
==================

测试 AdbCliDriver 的所有功能。

测试内容：
    - 基础命令执行（_run）
    - 设备列表查询
    - 设备信息获取
    - Shell 命令执行
    - 连接稳定性检测
    - TCP/IP 连接管理
    - 错误处理
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.infrastructure.adb.cli import AdbCliDriver
from app.core.exceptions import AdbError
from app.domain.device import DeviceInfo


# ---------------------------------------------------------------------------
# 基础命令测试
# ---------------------------------------------------------------------------

class TestAdbRun:
    """测试 _run 方法"""

    @pytest.mark.asyncio
    async def test_run_success(self, mock_adb_success):
        """测试成功执行 ADB 命令"""
        with patch('asyncio.create_subprocess_exec', side_effect=mock_adb_success):
            driver = AdbCliDriver()
            result = await driver._run("devices")
            assert result == "success"

    @pytest.mark.asyncio
    async def test_run_failure(self, mock_adb_failure):
        """测试 ADB 命令失败"""
        with patch('asyncio.create_subprocess_exec', side_effect=mock_adb_failure):
            driver = AdbCliDriver()
            with pytest.raises(AdbError, match="ADB command failed"):
                await driver._run("invalid_command")

    @pytest.mark.asyncio
    async def test_run_adb_not_found(self):
        """测试 ADB 未安装"""
        async def not_found_mock(*args, **kwargs):
            raise FileNotFoundError("adb not found")

        with patch('asyncio.create_subprocess_exec', side_effect=not_found_mock):
            driver = AdbCliDriver()
            with pytest.raises(AdbError, match="ADB not found"):
                await driver._run("devices")

    @pytest.mark.asyncio
    async def test_run_timeout(self):
        """测试 ADB 命令超时"""
        import asyncio

        async def timeout_mock(*args, **kwargs):
            proc = MagicMock()
            proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
            proc.kill = MagicMock()
            return proc

        with patch('asyncio.create_subprocess_exec', side_effect=timeout_mock):
            driver = AdbCliDriver()
            with pytest.raises(AdbError, match="timed out"):
                await driver._run("devices")


# ---------------------------------------------------------------------------
# 设备列表测试
# ---------------------------------------------------------------------------

class TestListDevices:
    """测试 list_devices 方法"""

    @pytest.mark.asyncio
    async def test_list_devices_success(self, mock_device_id, mock_adb_success):
        """测试成功列出设备"""
        async def mock_proc(*args, **kwargs):
            proc = MagicMock()
            proc.returncode = 0
            proc.communicate = AsyncMock(return_value=(
                b"List of devices attached\nemulator-5554    device\n",
                b""
            ))
            return proc

        with patch('asyncio.create_subprocess_exec', side_effect=mock_proc):
            driver = AdbCliDriver()
            devices = await driver.list_devices()
            assert len(devices) == 1
            assert devices[0] == "emulator-5554"

    @pytest.mark.asyncio
    async def test_list_devices_empty(self, mock_adb_success):
        """测试没有连接设备"""
        async def mock_proc(*args, **kwargs):
            proc = MagicMock()
            proc.returncode = 0
            proc.communicate = AsyncMock(return_value=(
                b"List of devices attached\n",
                b""
            ))
            return proc

        with patch('asyncio.create_subprocess_exec', side_effect=mock_proc):
            driver = AdbCliDriver()
            devices = await driver.list_devices()
            assert len(devices) == 0


# ---------------------------------------------------------------------------
# 设备信息测试
# ---------------------------------------------------------------------------

class TestGetDeviceInfo:
    """测试 get_device_info 方法"""

    @pytest.mark.asyncio
    async def test_get_device_info_success(self, mock_device_id):
        """测试成功获取设备信息"""
        call_count = 0

        async def mock_proc(*args, **kwargs):
            nonlocal call_count
            proc = MagicMock()
            proc.returncode = 0

            # 模拟不同的命令返回不同的数据
            # 注意：args 是元组，如 ('adb', '-s', 'id', 'shell', 'wm', 'size')
            # 需要检查单个元素而非组合字符串
            if "ro.product.model" in args:
                stdout = b"Pixel 6"
            elif "ro.build.version.release" in args:
                stdout = b"13"
            elif "wm" in args and "size" in args:
                stdout = b"Physical size: 1080x2400"
            elif "dumpsys" in args and "battery" in args:
                stdout = b"level: 85"
            else:
                stdout = b""

            call_count += 1
            proc.communicate = AsyncMock(return_value=(stdout, b""))
            return proc

        with patch('asyncio.create_subprocess_exec', side_effect=mock_proc):
            driver = AdbCliDriver()
            info = await driver.get_device_info(mock_device_id)

            assert info.id == mock_device_id
            assert info.model == "Pixel 6"
            assert info.os_version == "13"
            assert info.resolution == (1080, 2400)
            assert info.battery == 85
            assert info.status == "online"


# ---------------------------------------------------------------------------
# 连接稳定性测试
# ---------------------------------------------------------------------------

class TestCheckConnection:
    """测试 check_connection 方法"""

    @pytest.mark.asyncio
    async def test_check_connection_online(self, mock_device_id, mock_adb_success):
        """测试设备在线"""
        with patch('asyncio.create_subprocess_exec', side_effect=mock_adb_success):
            driver = AdbCliDriver()
            result = await driver.check_connection(mock_device_id)
            assert result is True

    @pytest.mark.asyncio
    async def test_check_connection_offline(self, mock_device_id, mock_adb_failure):
        """测试设备离线"""
        with patch('asyncio.create_subprocess_exec', side_effect=mock_adb_failure):
            driver = AdbCliDriver()
            result = await driver.check_connection(mock_device_id)
            assert result is False


# ---------------------------------------------------------------------------
# TCP/IP 连接测试
# ---------------------------------------------------------------------------

class TestTcpConnection:
    """测试 TCP/IP 连接方法"""

    @pytest.mark.asyncio
    async def test_connect_tcp_success(self, mock_adb_success):
        """测试成功通过 TCP/IP 连接"""
        async def mock_proc(*args, **kwargs):
            proc = MagicMock()
            proc.returncode = 0
            proc.communicate = AsyncMock(return_value=(
                b"connected to 192.168.1.5:5555",
                b""
            ))
            return proc

        with patch('asyncio.create_subprocess_exec', side_effect=mock_proc):
            driver = AdbCliDriver()
            device_id = await driver.connect_tcp("192.168.1.5", 5555)
            assert device_id == "192.168.1.5:5555"

    @pytest.mark.asyncio
    async def test_connect_tcp_failure(self, mock_adb_failure):
        """测试 TCP/IP 连接失败"""
        with patch('asyncio.create_subprocess_exec', side_effect=mock_adb_failure):
            driver = AdbCliDriver()
            with pytest.raises(AdbError):
                await driver.connect_tcp("192.168.1.5", 5555)

    @pytest.mark.asyncio
    async def test_disconnect_tcp(self, mock_adb_success):
        """测试断开 TCP/IP 连接"""
        with patch('asyncio.create_subprocess_exec', side_effect=mock_adb_success):
            driver = AdbCliDriver()
            # 不应该抛出异常
            await driver.disconnect_tcp("192.168.1.5", 5555)


# ---------------------------------------------------------------------------
# Shell 命令测试
# ---------------------------------------------------------------------------

class TestShell:
    """测试 shell 方法"""

    @pytest.mark.asyncio
    async def test_shell_success(self, mock_device_id, mock_adb_success):
        """测试成功执行 shell 命令"""
        with patch('asyncio.create_subprocess_exec', side_effect=mock_adb_success):
            driver = AdbCliDriver()
            result = await driver.shell(mock_device_id, "ls")
            assert result == "success"


# ---------------------------------------------------------------------------
# 解析方法测试
# ---------------------------------------------------------------------------

class TestParsing:
    """测试解析方法"""

    def test_parse_resolution_normal(self):
        """测试正常分辨率解析"""
        driver = AdbCliDriver()
        result = driver._parse_resolution("Physical size: 1080x2400")
        assert result == (1080, 2400)

    def test_parse_resolution_with_override(self):
        """测试带覆盖的分辨率解析"""
        driver = AdbCliDriver()
        result = driver._parse_resolution(
            "Physical size: 1080x2400\nOverride size: 720x1600"
        )
        assert result == (1080, 2400)

    def test_parse_resolution_invalid(self):
        """测试无效分辨率解析"""
        driver = AdbCliDriver()
        result = driver._parse_resolution("invalid output")
        assert result == (0, 0)
