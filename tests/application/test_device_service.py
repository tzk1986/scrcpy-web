"""
设备管理服务测试
==================

测试 DeviceService 的所有用例。

测试内容：
    - 基础设备查询（list_devices, get_device）
    - APK 安装（install_apk, batch_install）
    - 截图（screenshot）
    - TCP/IP 连接管理（connect_tcp, disconnect_tcp）
    - 后台设备刷新（start, stop, _refresh_loop）
    - 事件回调机制（on_device_connected, on_device_disconnected）
"""

import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call

from app.application.device_service import DeviceService
from app.core.exceptions import AdbError
from app.domain.device import DeviceInfo


# ---------------------------------------------------------------------------
# 测试 Fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_adb():
    """
    模拟 AdbDriver 协议。

    返回：
        MagicMock: 所有方法都是 AsyncMock 的 ADB 驱动模拟。
    """
    adb = MagicMock()
    adb.list_devices = AsyncMock(return_value=[])
    adb.get_device_info = AsyncMock()
    adb.shell = AsyncMock(return_value="")
    adb.install = AsyncMock()
    adb.screenshot = AsyncMock(return_value=b"\x89PNG\r\n\x1a\n")
    adb.connect_tcp = AsyncMock(return_value="192.168.1.5:5555")
    adb.disconnect_tcp = AsyncMock()
    return adb


@pytest.fixture
def mock_repo():
    """
    模拟 DeviceRepository 协议。

    返回：
        MagicMock: 所有方法都是 AsyncMock 的设备仓库模拟。
    """
    repo = MagicMock()
    repo.save = AsyncMock()
    repo.get = AsyncMock(return_value=None)
    repo.list_all = AsyncMock(return_value=[])
    repo.delete = AsyncMock()
    return repo


@pytest.fixture
def service(mock_adb, mock_repo):
    """
    构建测试用 DeviceService。

    返回：
        DeviceService: 注入模拟依赖的服务实例。
    """
    return DeviceService(adb=mock_adb, repo=mock_repo)


def _make_device(device_id: str = "emulator-5554", model: str = "Pixel 6") -> DeviceInfo:
    """
    创建测试用 DeviceInfo。

    参数：
        device_id: 设备 ID。
        model: 设备型号。

    返回：
        DeviceInfo: 填充了测试数据的设备信息。
    """
    return DeviceInfo(
        id=device_id,
        model=model,
        os_version="13",
        resolution=(1080, 2400),
        battery=85,
        status="online",
    )


# ---------------------------------------------------------------------------
# 基础设备查询测试
# ---------------------------------------------------------------------------

class TestListDevices:
    """测试 list_devices 方法"""

    @pytest.mark.asyncio
    async def test_list_devices_returns_all(self, service, mock_adb, mock_repo):
        """测试列出所有连接的设备"""
        device1 = _make_device("device-1")
        device2 = _make_device("device-2")

        mock_adb.list_devices.return_value = ["device-1", "device-2"]
        mock_adb.get_device_info.side_effect = [device1, device2]

        result = await service.list_devices()

        assert len(result) == 2
        assert result[0].id == "device-1"
        assert result[1].id == "device-2"

    @pytest.mark.asyncio
    async def test_list_devices_persists_each(self, service, mock_adb, mock_repo):
        """测试每个设备都被持久化到仓库"""
        device = _make_device("device-1")
        mock_adb.list_devices.return_value = ["device-1"]
        mock_adb.get_device_info.return_value = device

        await service.list_devices()

        mock_repo.save.assert_called_once_with(device)

    @pytest.mark.asyncio
    async def test_list_devices_empty(self, service, mock_adb):
        """测试没有连接设备时返回空列表"""
        mock_adb.list_devices.return_value = []

        result = await service.list_devices()

        assert result == []


class TestGetDevice:
    """测试 get_device 方法"""

    @pytest.mark.asyncio
    async def test_get_device_found(self, service, mock_repo):
        """测试找到设备时返回 DeviceInfo"""
        device = _make_device()
        mock_repo.get.return_value = device

        result = await service.get_device("emulator-5554")

        assert result == device
        mock_repo.get.assert_called_once_with("emulator-5554")

    @pytest.mark.asyncio
    async def test_get_device_not_found(self, service, mock_repo):
        """测试设备不存在时返回 None"""
        mock_repo.get.return_value = None

        result = await service.get_device("nonexistent")

        assert result is None


# ---------------------------------------------------------------------------
# APK 安装测试
# ---------------------------------------------------------------------------

class TestInstallApk:
    """测试 install_apk 方法"""

    @pytest.mark.asyncio
    async def test_install_success(self, service, mock_adb):
        """测试成功安装 APK"""
        result = await service.install_apk("emulator-5554", "/path/to/app.apk")

        mock_adb.install.assert_called_once_with("emulator-5554", "/path/to/app.apk")
        assert "emulator-5554" in result
        assert "installed" in result.lower()

    @pytest.mark.asyncio
    async def test_install_failure_propagates(self, service, mock_adb):
        """测试安装失败时异常被传播"""
        mock_adb.install.side_effect = AdbError("INSTALL_FAILED")

        with pytest.raises(AdbError, match="INSTALL_FAILED"):
            await service.install_apk("emulator-5554", "/path/to/app.apk")


class TestBatchInstall:
    """测试 batch_install 方法"""

    @pytest.mark.asyncio
    async def test_batch_install_all_success(self, service, mock_adb):
        """测试所有设备安装成功"""
        result = await service.batch_install(
            ["device-1", "device-2", "device-3"],
            "/path/to/app.apk"
        )

        assert len(result) == 3
        assert all("success" in r for r in result)
        assert mock_adb.install.call_count == 3

    @pytest.mark.asyncio
    async def test_batch_install_partial_failure(self, service, mock_adb):
        """测试部分设备安装失败不影响其他"""
        async def install_side_effect(device_id, apk_path):
            if device_id == "device-2":
                raise AdbError("INSTALL_FAILED")

        mock_adb.install.side_effect = install_side_effect

        result = await service.batch_install(
            ["device-1", "device-2", "device-3"],
            "/path/to/app.apk"
        )

        assert len(result) == 3
        assert "success" in result[0]
        assert "failed" in result[1]
        assert "success" in result[2]

    @pytest.mark.asyncio
    async def test_batch_install_empty_list(self, service):
        """测试空设备列表"""
        result = await service.batch_install([], "/path/to/app.apk")
        assert result == []


# ---------------------------------------------------------------------------
# 截图测试
# ---------------------------------------------------------------------------

class TestScreenshot:
    """测试 screenshot 方法"""

    @pytest.mark.asyncio
    async def test_screenshot_delegates_to_adb(self, service, mock_adb):
        """测试截图委托给 ADB 驱动"""
        mock_adb.screenshot.return_value = b"\x89PNG\r\n\x1a\nfake_png_data"

        result = await service.screenshot("emulator-5554")

        mock_adb.screenshot.assert_called_once_with("emulator-5554")
        assert result == b"\x89PNG\r\n\x1a\nfake_png_data"


# ---------------------------------------------------------------------------
# TCP/IP 连接测试
# ---------------------------------------------------------------------------

class TestConnectTcp:
    """测试 connect_tcp 方法"""

    @pytest.mark.asyncio
    async def test_connect_success(self, service, mock_adb, mock_repo):
        """测试成功通过 TCP/IP 连接"""
        device = _make_device("192.168.1.5:5555")
        mock_adb.connect_tcp.return_value = "192.168.1.5:5555"
        mock_adb.get_device_info.return_value = device

        result = await service.connect_tcp("192.168.1.5", 5555)

        assert result == "192.168.1.5:5555"
        mock_adb.connect_tcp.assert_called_once_with("192.168.1.5", 5555)
        mock_adb.get_device_info.assert_called_once_with("192.168.1.5:5555")
        mock_repo.save.assert_called_once_with(device)

    @pytest.mark.asyncio
    async def test_connect_failure_propagates(self, service, mock_adb):
        """测试连接失败时异常被传播"""
        mock_adb.connect_tcp.side_effect = AdbError("Connection refused")

        with pytest.raises(AdbError, match="Connection refused"):
            await service.connect_tcp("192.168.1.5", 5555)


class TestDisconnectTcp:
    """测试 disconnect_tcp 方法"""

    @pytest.mark.asyncio
    async def test_disconnect_removes_from_repo(self, service, mock_adb, mock_repo):
        """测试断开连接后从仓库删除设备"""
        await service.disconnect_tcp("192.168.1.5", 5555)

        mock_adb.disconnect_tcp.assert_called_once_with("192.168.1.5", 5555)
        mock_repo.delete.assert_called_once_with("192.168.1.5:5555")


# ---------------------------------------------------------------------------
# 后台刷新测试
# ---------------------------------------------------------------------------

class TestStartStop:
    """测试 start/stop 方法"""

    @pytest.mark.asyncio
    async def test_start_creates_task(self, service):
        """测试启动创建后台任务"""
        await service.start()

        assert service._refresh_task is not None
        assert not service._refresh_task.done()

        # 清理
        await service.stop()

    @pytest.mark.asyncio
    async def test_start_idempotent(self, service):
        """测试重复启动不会创建多个任务"""
        await service.start()
        first_task = service._refresh_task

        await service.start()  # 第二次调用应该是 no-op
        assert service._refresh_task is first_task

        await service.stop()

    @pytest.mark.asyncio
    async def test_stop_cancels_task(self, service):
        """测试停止取消后台任务"""
        await service.start()
        task = service._refresh_task

        await service.stop()

        assert task.cancelled() or task.done()
        assert service._refresh_task is None

    @pytest.mark.asyncio
    async def test_stop_when_not_running(self, service):
        """测试未启动时停止是安全的"""
        await service.stop()  # 不应抛异常
        assert service._refresh_task is None


# ---------------------------------------------------------------------------
# 刷新循环测试
# ---------------------------------------------------------------------------

class TestRefreshLoop:
    """测试 _refresh_loop 方法"""

    @pytest.mark.asyncio
    async def test_detects_new_device(self, service, mock_adb, mock_repo):
        """测试检测到新连接的设备"""
        device = _make_device("new-device")

        # 第一次调用返回新设备，第二次抛 CancelledError 退出循环
        call_count = 0

        async def list_devices_side_effect():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return ["new-device"]
            # 第二次循环前等待时会被取消
            await asyncio.sleep(100)
            return []

        mock_adb.list_devices.side_effect = list_devices_side_effect
        mock_adb.get_device_info.return_value = device

        # 手动运行一次刷新
        # 模拟 _refresh_interval 非常短
        service._refresh_interval = 0.01
        task = asyncio.create_task(service._refresh_loop())

        # 等待足够时间让第一次刷新完成
        await asyncio.sleep(0.05)

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        mock_repo.save.assert_called()

    @pytest.mark.asyncio
    async def test_detects_disconnected_device(self, service, mock_adb, mock_repo):
        """测试检测到断开的设备"""
        # 初始状态有一个设备
        service._previous_devices = {"old-device"}

        # 下次刷新时设备消失
        mock_adb.list_devices.return_value = []

        service._refresh_interval = 0.01
        task = asyncio.create_task(service._refresh_loop())

        await asyncio.sleep(0.05)

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        mock_repo.delete.assert_called_with("old-device")

    @pytest.mark.asyncio
    async def test_refresh_error_does_not_stop_loop(self, service, mock_adb):
        """测试单次刷新失败不会停止整个循环"""
        call_count = 0

        async def list_devices_side_effect():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("ADB crashed")
            elif call_count == 2:
                # 第二次循环前等待时会被取消
                await asyncio.sleep(100)
            return []

        mock_adb.list_devices.side_effect = list_devices_side_effect

        service._refresh_interval = 0.01
        task = asyncio.create_task(service._refresh_loop())

        await asyncio.sleep(0.05)

        # 循环应该仍在运行（尽管第一次失败了）
        assert not task.done()

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


# ---------------------------------------------------------------------------
# 事件回调测试
# ---------------------------------------------------------------------------

class TestEventCallbacks:
    """测试设备事件回调机制"""

    @pytest.mark.asyncio
    async def test_register_connected_callback(self, service):
        """测试注册设备连接回调"""
        callback = AsyncMock()

        service.on_device_connected(callback)

        assert callback in service._on_device_connected

    @pytest.mark.asyncio
    async def test_register_disconnected_callback(self, service):
        """测试注册设备断开回调"""
        callback = AsyncMock()

        service.on_device_disconnected(callback)

        assert callback in service._on_device_disconnected

    @pytest.mark.asyncio
    async def test_trigger_connected_callbacks(self, service):
        """测试触发所有设备连接回调"""
        callback1 = AsyncMock()
        callback2 = AsyncMock()
        service.on_device_connected(callback1)
        service.on_device_connected(callback2)

        device = _make_device()
        await service._trigger_connected_callbacks(device)

        callback1.assert_called_once_with(device)
        callback2.assert_called_once_with(device)

    @pytest.mark.asyncio
    async def test_trigger_disconnected_callbacks(self, service):
        """测试触发所有设备断开回调"""
        callback1 = AsyncMock()
        callback2 = AsyncMock()
        service.on_device_disconnected(callback1)
        service.on_device_disconnected(callback2)

        await service._trigger_disconnected_callbacks("emulator-5554")

        callback1.assert_called_once_with("emulator-5554")
        callback2.assert_called_once_with("emulator-5554")

    @pytest.mark.asyncio
    async def test_callback_error_does_not_propagate(self, service):
        """测试单个回调失败不影响其他回调"""
        failing_callback = AsyncMock(side_effect=RuntimeError("oops"))
        good_callback = AsyncMock()

        service.on_device_connected(failing_callback)
        service.on_device_connected(good_callback)

        device = _make_device()
        await service._trigger_connected_callbacks(device)

        # 即使第一个失败，第二个也应该被调用
        good_callback.assert_called_once_with(device)

    @pytest.mark.asyncio
    async def test_new_device_triggers_callback(self, service, mock_adb, mock_repo):
        """测试刷新循环中新设备触发连接回调"""
        device = _make_device("new-device")
        callback = AsyncMock()
        service.on_device_connected(callback)

        # 第一次返回新设备，后续挂起
        call_count = 0

        async def list_devices_side_effect():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return ["new-device"]
            await asyncio.sleep(100)
            return []

        mock_adb.list_devices.side_effect = list_devices_side_effect
        mock_adb.get_device_info.return_value = device

        service._refresh_interval = 0.01
        task = asyncio.create_task(service._refresh_loop())

        await asyncio.sleep(0.05)

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        callback.assert_called()

    @pytest.mark.asyncio
    async def test_disconnected_device_triggers_callback(self, service, mock_adb, mock_repo):
        """测试刷新循环中断开设备触发断开回调"""
        callback = AsyncMock()
        service.on_device_disconnected(callback)

        # 初始有一个设备
        service._previous_devices = {"old-device"}
        mock_adb.list_devices.return_value = []

        service._refresh_interval = 0.01
        task = asyncio.create_task(service._refresh_loop())

        await asyncio.sleep(0.05)

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        callback.assert_called_with("old-device")
