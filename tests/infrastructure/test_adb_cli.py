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

import asyncio

import pytest
from ipaddress import ip_network
from unittest.mock import AsyncMock, MagicMock, patch

from app.infrastructure.adb.cli import AdbCliDriver
from app.core.exceptions import (
    AdbError,
    DeviceNotReadyError,
    DeviceUnreachableError,
    PairError,
)


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

    @pytest.mark.asyncio
    async def test_get_device_info_commands_use_short_timeout(self, mock_device_id):
        """4 条信息命令均以 5s 短超时透传（诊断类命令的 30s 不受影响）。"""
        calls: list = []

        async def fake_run_serial(device_id, *args, **kwargs):
            calls.append(kwargs)
            if "ro.product.model" in args:
                return "Pixel 6"
            if "ro.build.version.release" in args:
                return "13"
            if "size" in args:
                return "Physical size: 1080x2400"
            return "level: 85"

        driver = AdbCliDriver()
        with patch.object(driver, "_run_serial", new=fake_run_serial):
            info = await driver.get_device_info(mock_device_id)

        assert info.model == "Pixel 6"
        assert len(calls) == 4
        assert all(kwargs.get("timeout") == 5 for kwargs in calls)

    @pytest.mark.asyncio
    async def test_get_device_info_runs_commands_concurrently(self, mock_device_id):
        """4 条命令并发执行——串行实现 in-flight 峰值恒为 1（方案 35 D2）。"""
        import asyncio

        in_flight = 0
        peak = 0

        async def fake_run_serial(device_id, *args, **kwargs):
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.01)
            in_flight -= 1
            if "ro.product.model" in args:
                return "Pixel 6"
            if "ro.build.version.release" in args:
                return "13"
            if "size" in args:
                return "Physical size: 1080x2400"
            return "level: 85"

        driver = AdbCliDriver()
        with patch.object(driver, "_run_serial", new=fake_run_serial):
            info = await driver.get_device_info(mock_device_id)

        assert info.battery == 85
        assert peak >= 2


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

    @pytest.mark.asyncio
    async def test_connect_tcp_passes_timeout_to_run(self):
        """扫描编排路径：connect_tcp 的 timeout 透传给 _run（方案 36 D4）。"""
        driver = AdbCliDriver()
        run_mock = AsyncMock(return_value="connected to 192.168.1.5:5555")
        with (
            patch('app.infrastructure.adb.cli.probe_tcp', new=AsyncMock(return_value=None)),
            patch.object(driver, '_run', new=run_mock),
        ):
            device_id = await driver.connect_tcp("192.168.1.5", 5555, timeout=5)

        assert device_id == "192.168.1.5:5555"
        run_mock.assert_awaited_once_with("connect", "192.168.1.5:5555", timeout=5)

    @pytest.mark.asyncio
    async def test_connect_tcp_default_timeout_stays_none(self):
        """缺省不传 timeout → _run 收到 None（回落配置 30s，既有语义不变）。

        already connected 分支带状态复核（路线图 15），故二次 _run 为 devices。
        """
        driver = AdbCliDriver()
        run_mock = AsyncMock(side_effect=[
            "already connected to 192.168.1.5:5555",
            "List of devices attached\n192.168.1.5:5555\tdevice\n",
        ])
        with (
            patch('app.infrastructure.adb.cli.probe_tcp', new=AsyncMock(return_value=None)),
            patch.object(driver, '_run', new=run_mock),
        ):
            device_id = await driver.connect_tcp("192.168.1.5", 5555)

        assert device_id == "192.168.1.5:5555"
        assert run_mock.await_args_list[0].args == ("connect", "192.168.1.5:5555")
        assert run_mock.await_args_list[0].kwargs == {"timeout": None}

    @pytest.mark.asyncio
    async def test_connect_tcp_fresh_connected_skips_state_recheck(self):
        """非 already connected 的新连接成功不做 devices 复核（scan 路径零额外开销）。"""
        driver = AdbCliDriver()
        run_mock = AsyncMock(return_value="connected to 192.168.1.5:5555")
        with (
            patch('app.infrastructure.adb.cli.probe_tcp', new=AsyncMock(return_value=None)),
            patch.object(driver, '_run', new=run_mock),
        ):
            device_id = await driver.connect_tcp("192.168.1.5", 5555)
        assert device_id == "192.168.1.5:5555"
        run_mock.assert_awaited_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("devices_state", "hint"),
        [
            pytest.param("offline", "offline", id="offline"),
            pytest.param("unauthorized", "授权", id="unauthorized"),
            pytest.param(None, "断开", id="absent"),
        ],
    )
    async def test_connect_tcp_already_connected_bad_state_fails(self, devices_state, hint):
        """already connected + 设备表状态非 device → 假成功守卫报 DeviceNotReadyError（路线图 15）。"""
        driver = AdbCliDriver()
        table = (
            "List of devices attached\n"
            + (f"192.168.1.5:5555\t{devices_state}\n" if devices_state else "")
        )
        run_mock = AsyncMock(side_effect=[
            "already connected to 192.168.1.5:5555",
            table,
        ])
        with (
            patch('app.infrastructure.adb.cli.probe_tcp', new=AsyncMock(return_value=None)),
            patch.object(driver, '_run', new=run_mock),
        ):
            with pytest.raises(DeviceNotReadyError) as exc:
                await driver.connect_tcp("192.168.1.5", 5555)
        assert hint in exc.value.message
        assert exc.value.code == "DEVICE_NOT_READY"


class TestPairTcp:
    """测试无线调试配对（adb pair，方案 47 T4）"""

    @pytest.mark.asyncio
    async def test_pair_tcp_success(self):
        driver = AdbCliDriver()
        run_mock = AsyncMock(return_value="Successfully paired to 192.168.1.5:37123 [guid=abc]")
        probe = AsyncMock(return_value=None)
        with (
            patch('app.infrastructure.adb.cli.probe_tcp', new=probe),
            patch.object(driver, '_run', new=run_mock),
        ):
            await driver.pair_tcp("192.168.1.5", 37123, "123456")
        probe.assert_awaited_once()
        run_mock.assert_awaited_once_with("pair", "192.168.1.5:37123", "123456")

    @pytest.mark.asyncio
    async def test_pair_tcp_unreachable_skips_adb(self):
        """预检不可达快速失败，不进入 adb 子进程（与 connect 同语义）"""
        driver = AdbCliDriver()
        run_mock = AsyncMock()
        with (
            patch('app.infrastructure.adb.cli.probe_tcp',
                  new=AsyncMock(side_effect=DeviceUnreachableError("192.168.1.5", 37123, "连接被拒绝"))),
            patch.object(driver, '_run', new=run_mock),
        ):
            with pytest.raises(DeviceUnreachableError):
                await driver.pair_tcp("192.168.1.5", 37123, "123456")
        run_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_pair_tcp_old_adb_maps_platform_tools_hint(self):
        """adb 过旧（unknown command pair）→ 文案含 platform-tools 升级指引"""
        driver = AdbCliDriver()
        with (
            patch('app.infrastructure.adb.cli.probe_tcp', new=AsyncMock(return_value=None)),
            patch.object(driver, '_run',
                         new=AsyncMock(side_effect=AdbError("ADB command failed: adb.exe: unknown command pair"))),
        ):
            with pytest.raises(PairError) as exc:
                await driver.pair_tcp("192.168.1.5", 37123, "123456")
        assert "platform-tools" in exc.value.message

    @pytest.mark.asyncio
    async def test_pair_tcp_wrong_code_maps_code_hint_both_forms(self):
        """配对码错误两类形态（stderr 异常/stdout 文本）都归入"配对码"文案"""
        driver = AdbCliDriver()
        stderr_form = AdbError(
            "ADB command failed: Pairing failed. Please make sure you have entered a valid pairing code")
        for is_exception, detail in ((True, stderr_form), (False, "Pairing failed: invalid pairing code")):
            run_mock = (AsyncMock(side_effect=detail) if is_exception
                        else AsyncMock(return_value=detail))
            with (
                patch('app.infrastructure.adb.cli.probe_tcp', new=AsyncMock(return_value=None)),
                patch.object(driver, '_run', new=run_mock),
            ):
                with pytest.raises(PairError) as exc:
                    await driver.pair_tcp("192.168.1.5", 37123, "000000")
            assert "配对码" in exc.value.message

    @pytest.mark.asyncio
    async def test_pair_tcp_generic_fallback(self):
        """其他失败（拒绝连接等）→ 弹窗/端口最新性兜底文案，code=PAIR_FAILED"""
        driver = AdbCliDriver()
        with (
            patch('app.infrastructure.adb.cli.probe_tcp', new=AsyncMock(return_value=None)),
            patch.object(driver, '_run',
                         new=AsyncMock(side_effect=AdbError("ADB command failed: connection refused"))),
        ):
            with pytest.raises(PairError) as exc:
                await driver.pair_tcp("192.168.1.5", 37123, "123456")
        assert exc.value.code == "PAIR_FAILED"
        assert "弹窗" in exc.value.message


class TestScanHosts:
    """测试网段扫描薄包装（方案 36 D1）"""

    @pytest.mark.asyncio
    async def test_scan_hosts_delegates_to_reachability(self):
        """scan_hosts 委托 reachability.scan_open_hosts，参数原样透传。"""
        driver = AdbCliDriver()
        hosts = [str(ip) for ip in ip_network("192.168.8.0/24").hosts()]
        scan_mock = AsyncMock(return_value=["192.168.8.18", "192.168.8.25"])
        with patch('app.infrastructure.adb.cli.scan_open_hosts', new=scan_mock):
            result = await driver.scan_hosts(hosts, port=5556, timeout=0.8, concurrency=32)

        assert result == ["192.168.8.18", "192.168.8.25"]
        scan_mock.assert_awaited_once_with(hosts, port=5556, timeout=0.8, concurrency=32)


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
# 截图 raw+gzip 链路（方案 29：adb exec-out sh -c "screencap | gzip -1"）
# ---------------------------------------------------------------------------

class TestScreenshotRawGzip:
    """测试 screenshot_raw_gzip 方法"""

    def _proc(self, captured: list, rc: int = 0, stdout: bytes = b"\x1f\x8bGZIP", stderr: bytes = b""):
        async def create_mock_proc(*args, **kwargs):
            captured.append(args)
            proc = MagicMock()
            proc.returncode = rc
            proc.communicate = AsyncMock(return_value=(stdout, stderr))
            return proc
        return create_mock_proc

    @pytest.mark.asyncio
    async def test_screenshot_raw_gzip_argv_and_stdout(self):
        """命令形态正确（exec-out sh -c "screencap 2>/dev/null | gzip -1"，方案 38 A1）且原样返回 gzip 字节。"""
        captured: list = []
        with patch('asyncio.create_subprocess_exec',
                   side_effect=self._proc(captured, stdout=b"GZIP_BYTES")):
            driver = AdbCliDriver()
            result = await driver.screenshot_raw_gzip("dev1")
        assert result == b"GZIP_BYTES"
        assert captured[0][1:4] == ("-s", "dev1", "exec-out")
        assert captured[0][4:] == ("sh", "-c", "screencap 2>/dev/null | gzip -1")

    @pytest.mark.asyncio
    async def test_screenshot_raw_gzip_strips_lead_junk(self):
        """多屏警告前缀剥离（方案 38 A2）：347B 文本 + gzip 流 → 从魔数处切片。"""
        warning = b"Multiple displays were found - screenshot captured on the default display\n"
        stdout = warning + b"\x1f\x8bGZIP_PAYLOAD"
        captured: list = []
        with patch('asyncio.create_subprocess_exec', side_effect=self._proc(captured, stdout=stdout)):
            driver = AdbCliDriver()
            result = await driver.screenshot_raw_gzip("dev1")
        assert result == b"\x1f\x8bGZIP_PAYLOAD"

    @pytest.mark.asyncio
    async def test_screenshot_raw_gzip_magic_at_zero_untouched(self):
        """魔数位于 0 位 → 原样透传（防御不增不减）。"""
        stdout = b"\x1f\x8bGZIP_PAYLOAD"
        captured: list = []
        with patch('asyncio.create_subprocess_exec', side_effect=self._proc(captured, stdout=stdout)):
            driver = AdbCliDriver()
            result = await driver.screenshot_raw_gzip("dev1")
        assert result == stdout

    @pytest.mark.asyncio
    async def test_screenshot_raw_gzip_failure(self):
        """设备端链失败（rc≠0）抛 AdbError 且携带 stderr。"""
        captured: list = []
        with patch('asyncio.create_subprocess_exec',
                   side_effect=self._proc(captured, rc=127, stderr=b"sh: gzip: not found")):
            driver = AdbCliDriver()
            with pytest.raises(AdbError, match="gzip: not found"):
                await driver.screenshot_raw_gzip("dev1")

    @pytest.mark.asyncio
    async def test_screenshot_raw_gzip_empty(self):
        """空输出抛 AdbError（对齐既有 screenshot 语义）。"""
        captured: list = []
        with patch('asyncio.create_subprocess_exec',
                   side_effect=self._proc(captured, stdout=b"")):
            driver = AdbCliDriver()
            with pytest.raises(AdbError, match="empty"):
                await driver.screenshot_raw_gzip("dev1")

    @pytest.mark.asyncio
    async def test_screenshot_raw_gzip_timeout_kills_and_raises(self):
        """方案 51 A01：communicate 悬挂 → 按 adb.timeout 截断、kill 子进程、抛 AdbError（不无限挂起）。"""
        procs: list = []

        async def create_mock_proc(*args, **kwargs):
            proc = MagicMock()
            proc.returncode = None
            proc.kill = MagicMock()
            proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
            procs.append(proc)
            return proc

        with patch('asyncio.create_subprocess_exec', side_effect=create_mock_proc):
            driver = AdbCliDriver()
            with pytest.raises(AdbError, match="[Tt]imed out"):
                await driver.screenshot_raw_gzip("dev1")
        assert procs and procs[0].kill.called


# ---------------------------------------------------------------------------
# 截图 PNG 链路（方案 38：sh -c 硬化 + 魔数剥前缀）
# ---------------------------------------------------------------------------

class TestScreenshotPng:
    """测试 screenshot（PNG）方法"""

    def _proc(self, captured: list, rc: int = 0, stdout: bytes = b"\x89PNG\r\n\x1a\n", stderr: bytes = b""):
        async def create_mock_proc(*args, **kwargs):
            captured.append(args)
            proc = MagicMock()
            proc.returncode = rc
            proc.communicate = AsyncMock(return_value=(stdout, stderr))
            return proc
        return create_mock_proc

    @pytest.mark.asyncio
    async def test_screenshot_timeout_kills_and_raises(self):
        """方案 51 A01：communicate 悬挂 → 按 adb.timeout 截断、kill 子进程、抛 AdbError（不无限挂起）。"""
        procs: list = []

        async def create_mock_proc(*args, **kwargs):
            proc = MagicMock()
            proc.returncode = None
            proc.kill = MagicMock()
            proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
            procs.append(proc)
            return proc

        with patch('asyncio.create_subprocess_exec', side_effect=create_mock_proc):
            driver = AdbCliDriver()
            with pytest.raises(AdbError, match="[Tt]imed out"):
                await driver.screenshot("dev1")
        assert procs and procs[0].kill.called

    @pytest.mark.asyncio
    async def test_screenshot_argv_and_stdout(self):
        """命令形态正确（exec-out sh -c "screencap -p 2>/dev/null"，方案 38 A1）且原样返回 PNG 字节。"""
        captured: list = []
        with patch('asyncio.create_subprocess_exec',
                   side_effect=self._proc(captured, stdout=b"PNG_BYTES")):
            driver = AdbCliDriver()
            result = await driver.screenshot("dev1")
        assert result == b"PNG_BYTES"
        assert captured[0][1:4] == ("-s", "dev1", "exec-out")
        assert captured[0][4:] == ("sh", "-c", "screencap -p 2>/dev/null")

    @pytest.mark.asyncio
    async def test_screenshot_strips_lead_junk(self):
        """多屏警告前缀剥离（方案 38 A2）：文本 + PNG → 从魔数处切片。"""
        warning = b"Multiple displays were found 347B junk\n"
        stdout = warning + b"\x89PNG\r\n\x1a\nPAYLOAD"
        captured: list = []
        with patch('asyncio.create_subprocess_exec', side_effect=self._proc(captured, stdout=stdout)):
            driver = AdbCliDriver()
            result = await driver.screenshot("dev1")
        assert result == b"\x89PNG\r\n\x1a\nPAYLOAD"

    @pytest.mark.asyncio
    async def test_screenshot_magic_at_zero_untouched(self):
        """魔数位于 0 位 → 原样透传。"""
        stdout = b"\x89PNG\r\n\x1a\nPAYLOAD"
        captured: list = []
        with patch('asyncio.create_subprocess_exec', side_effect=self._proc(captured, stdout=stdout)):
            driver = AdbCliDriver()
            result = await driver.screenshot("dev1")
        assert result == stdout

    @pytest.mark.asyncio
    async def test_screenshot_failure_and_empty(self):
        """链路失败（rc≠0）抛 AdbError；空输出抛 AdbError。"""
        captured: list = []
        with patch('asyncio.create_subprocess_exec',
                   side_effect=self._proc(captured, rc=1, stderr=b"boom")):
            driver = AdbCliDriver()
            with pytest.raises(AdbError, match="boom"):
                await driver.screenshot("dev1")
        with patch('asyncio.create_subprocess_exec', side_effect=self._proc(captured, stdout=b"")):
            driver = AdbCliDriver()
            with pytest.raises(AdbError, match="empty"):
                await driver.screenshot("dev1")


# ---------------------------------------------------------------------------
# 设备 rotation 查询（方案 40 B1：dumpsys display mCurrentOrientation——
# 方案 38 的 dumpsys input SurfaceOrientation 是 InputFlinger 坐标系语义，
# .19/.13 实测证伪（.13 且双行噪声）；真信号 = 默认显示器当前 orientation）
# ---------------------------------------------------------------------------

class TestRotation:
    """测试 get_rotation 方法"""

    # 真机样张结构（2026-10-10 六台取证：DisplayDeviceInfo 段 + 紧跟的
    # mCurrentOrientation 行；.24 型单屏 2、.13 型内置 0 + 外接 HDMI 2）
    _DUMP_SINGLE_DISPLAY = (
        '  DisplayDeviceInfo{"内置屏幕": uniqueId="local:0", 1280 x 800, '
        "rotation 0, type BUILT_IN, state ON}\n"
        "    mCurrentOrientation=2\n"
        "    mBaseDisplayInfo=DisplayInfo{app 1920 x 1080, rotation 0}\n"
    )

    _DUMP_DUAL_DISPLAY = (
        '  DisplayDeviceInfo{"内置屏幕": uniqueId="local:0", 1280 x 720, '
        "rotation 0, type INTERNAL, state ON}\n"
        "    mCurrentOrientation=0\n"
        '  DisplayDeviceInfo{"HDMI 屏幕": uniqueId="local:1", 1024 x 600, '
        "rotation 2, type EXTERNAL, state ON}\n"
        "    mCurrentOrientation=2\n"
    )

    @pytest.mark.asyncio
    async def test_get_rotation_parses_mcurrentorientation(self):
        """解析默认显示器（首个）mCurrentOrientation 数值，命令为 dumpsys display。"""
        mock_run = AsyncMock(return_value=self._DUMP_SINGLE_DISPLAY)
        with patch.object(AdbCliDriver, "_run_serial", new=mock_run):
            driver = AdbCliDriver()
            result = await driver.get_rotation("dev1")
        assert result == 2
        assert mock_run.await_args.args[1:] == ("shell", "dumpsys", "display")

    @pytest.mark.asyncio
    async def test_get_rotation_dual_display_takes_first(self):
        """双显示器（.13 型：内置 0 + 外接 HDMI 2）→ 取首个（默认屏）= 0。"""
        with patch.object(
            AdbCliDriver, "_run_serial",
            new=AsyncMock(return_value=self._DUMP_DUAL_DISPLAY),
        ):
            driver = AdbCliDriver()
            result = await driver.get_rotation("dev1")
        assert result == 0

    @pytest.mark.asyncio
    async def test_get_rotation_missing_line_defaults_zero(self):
        """输出无 mCurrentOrientation 行 → 0（不旋转）。"""
        with patch.object(
            AdbCliDriver, "_run_serial", new=AsyncMock(return_value="no such line\n")
        ):
            driver = AdbCliDriver()
            result = await driver.get_rotation("dev1")
        assert result == 0

    @pytest.mark.asyncio
    async def test_get_rotation_command_failure_propagates(self):
        """命令失败抛 AdbError（由应用层收敛为 0 并缓存）。"""
        with patch.object(
            AdbCliDriver, "_run_serial", side_effect=AdbError("boom")
        ):
            driver = AdbCliDriver()
            with pytest.raises(AdbError):
                await driver.get_rotation("dev1")


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
