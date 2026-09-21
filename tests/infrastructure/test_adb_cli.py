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
from app.core.exceptions import AdbError, DeviceUnreachableError
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

        with (
            patch('asyncio.create_subprocess_exec', side_effect=mock_proc),
            patch('app.infrastructure.adb.cli.probe_tcp', new=AsyncMock(return_value=None)),
        ):
            driver = AdbCliDriver()
            device_id = await driver.connect_tcp("192.168.1.5", 5555)
            assert device_id == "192.168.1.5:5555"

    @pytest.mark.asyncio
    async def test_connect_tcp_failure(self, mock_adb_failure):
        """测试 TCP/IP 连接失败"""
        with (
            patch('asyncio.create_subprocess_exec', side_effect=mock_adb_failure),
            patch('app.infrastructure.adb.cli.probe_tcp', new=AsyncMock(return_value=None)),
        ):
            driver = AdbCliDriver()
            with pytest.raises(AdbError):
                await driver.connect_tcp("192.168.1.5", 5555)

    @pytest.mark.asyncio
    async def test_disconnect_tcp(self, mock_adb_success):
        """测试断开 TCP/IP 连接"""
        with (
            patch('asyncio.create_subprocess_exec', side_effect=mock_adb_success),
            patch('app.infrastructure.adb.cli.probe_tcp', new=AsyncMock(return_value=None)),
        ):
            driver = AdbCliDriver()
            # 不应该抛出异常
            await driver.disconnect_tcp("192.168.1.5", 5555)

    @pytest.mark.asyncio
    async def test_connect_tcp_unreachable_fast_fail(self):
        """预检不可达 → 直接抛 DeviceUnreachableError，不创建 adb 子进程"""
        with (
            patch('app.infrastructure.adb.cli.probe_tcp',
                  new=AsyncMock(side_effect=DeviceUnreachableError(
                      "192.168.1.5", 5555, "1.5 秒内无响应（连接超时）"))),
            patch('asyncio.create_subprocess_exec') as exec_mock,
        ):
            driver = AdbCliDriver()
            with pytest.raises(DeviceUnreachableError):
                await driver.connect_tcp("192.168.1.5", 5555)
            exec_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_disconnect_tcp_unreachable_skips_adb(self):
        """预检不可达 → 跳过 adb disconnect 正常返回，不创建 adb 子进程"""
        with (
            patch('app.infrastructure.adb.cli.probe_tcp',
                  new=AsyncMock(side_effect=DeviceUnreachableError(
                      "192.168.1.5", 5555, "连接被拒绝"))),
            patch('asyncio.create_subprocess_exec') as exec_mock,
        ):
            driver = AdbCliDriver()
            await driver.disconnect_tcp("192.168.1.5", 5555)
            exec_mock.assert_not_called()


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
# logcat 流式采集测试（-T 时间下界绑定）
# ---------------------------------------------------------------------------

class _FakeReader:
    """最小 StreamReader 替身：按行返回，用尽后 EOF。"""

    def __init__(self, lines: list[bytes]):
        self._lines = list(lines)

    async def readline(self) -> bytes:
        return self._lines.pop(0) if self._lines else b""

    async def read(self) -> bytes:
        return b""


def _make_logcat_proc(stdout_lines: list[bytes]) -> MagicMock:
    proc = MagicMock()
    proc.returncode = None
    proc.stdout = _FakeReader(stdout_lines)
    proc.stderr = _FakeReader([])
    proc.kill = MagicMock()
    return proc


class TestStreamLogcat:
    """测试 stream_logcat：重启采集以设备端当前时刻为下界，不回放缓冲。"""

    @pytest.mark.asyncio
    async def test_stream_logcat_binds_device_time_lower_bound(self, mock_device_id):
        """命令经 shell 以单参数传入，含 -T 且时刻由设备端 date 生成（防时钟差）。"""
        captured = {}
        proc = _make_logcat_proc([])

        async def mock_exec(*args, **kwargs):
            captured["argv"] = args
            return proc

        with patch('asyncio.create_subprocess_exec', side_effect=mock_exec):
            driver = AdbCliDriver()
            lines = [line async for line in driver.stream_logcat(mock_device_id)]

        assert lines == []
        argv = captured["argv"]
        assert argv[0] == driver.adb_path
        assert argv[1:4] == ("-s", mock_device_id, "shell")
        # shell 后必须只有一个参数（整条命令），否则远端 shell 会把含空格的时刻拆开
        assert len(argv) == 5
        cmd = argv[4]
        assert cmd == AdbCliDriver._LOGCAT_SINCE_NOW_CMD
        assert cmd.startswith("logcat -v threadtime -T ")
        assert '$(date "+%m-%d %H:%M:%S.%N")' in cmd
        # 时间形式必须带引号（纯数字参数会被 logcat 按「计数」解析而回放行）
        assert '-T "' in cmd

    @pytest.mark.asyncio
    async def test_stream_logcat_yields_lines(self, mock_device_id):
        """逐行产出并去除行尾，流结束后 kill 子进程。"""
        raw = [
            b"09-21 14:58:50.634 24011 24011 I TZKMARK : A\r\n",
            b"09-21 14:58:51.000 24012 24012 I TZKMARK : B",
        ]
        proc = _make_logcat_proc(raw)

        async def mock_exec(*args, **kwargs):
            return proc

        with patch('asyncio.create_subprocess_exec', side_effect=mock_exec):
            driver = AdbCliDriver()
            seen = [line async for line in driver.stream_logcat(mock_device_id)]

        assert seen == [
            "09-21 14:58:50.634 24011 24011 I TZKMARK : A",
            "09-21 14:58:51.000 24012 24012 I TZKMARK : B",
        ]
        proc.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_stream_logcat_kills_proc_on_early_cancel(self, mock_device_id):
        """消费方提前关闭生成器（采集任务被取消）时 kill 子进程。"""
        proc = _make_logcat_proc([b"line1\n", b"line2\n"])

        async def mock_exec(*args, **kwargs):
            return proc

        with patch('asyncio.create_subprocess_exec', side_effect=mock_exec):
            driver = AdbCliDriver()
            gen = driver.stream_logcat(mock_device_id)
            first = await gen.__anext__()
            await gen.aclose()

        assert first == "line1"
        proc.kill.assert_called_once()


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
