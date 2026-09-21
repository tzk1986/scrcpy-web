"""
真实设备集成测试
==================

使用真实 ADB 连接验证设备管理服务。

测试内容：
    - TCP/IP 连接
    - 设备列表查询
    - 设备信息获取
    - 连接稳定性检测
    - 设备服务集成

注意：
    - 仅执行只读操作
    - 不执行数据操作或破坏性测试
    - 需要真实设备已启用 ADB 调试

前置条件：
    1. 设备已启用开发者选项和 ADB 调试
    2. 设备 IP: 192.168.8.22，端口: 5555
    3. ADB 已安装并可用
"""

import pytest
import asyncio
from unittest.mock import AsyncMock

from app.infrastructure.adb.cli import AdbCliDriver
from app.application.device_service import DeviceService
from app.core.exceptions import AdbError


# ---------------------------------------------------------------------------
# 测试配置
# ---------------------------------------------------------------------------

REAL_DEVICE_IP = "192.168.8.22"
REAL_DEVICE_PORT = 5555
REAL_DEVICE_ID = f"{REAL_DEVICE_IP}:{REAL_DEVICE_PORT}"


# ---------------------------------------------------------------------------
# Mock 仓库（内存实现）
# ---------------------------------------------------------------------------

class InMemoryDeviceRepository:
    """
    内存设备仓库（用于测试）。

    实现 DeviceRepository 协议。
    """

    def __init__(self):
        self._devices = {}

    async def save(self, device):
        self._devices[device.id] = device

    async def get(self, device_id):
        return self._devices.get(device_id)

    async def list_all(self):
        return list(self._devices.values())

    async def delete(self, device_id):
        self._devices.pop(device_id, None)


# ---------------------------------------------------------------------------
# ADB CLI 驱动真实测试
# ---------------------------------------------------------------------------

class TestAdbCliRealDevice:
    """测试 AdbCliDriver 与真实设备"""

    @pytest.mark.asyncio
    async def test_connect_tcp(self):
        """测试 TCP/IP 连接到真实设备"""
        driver = AdbCliDriver()

        # 连接应该成功（设备可能已连接）
        try:
            device_id = await driver.connect_tcp(REAL_DEVICE_IP, REAL_DEVICE_PORT)
            assert device_id == REAL_DEVICE_ID
        except AdbError as e:
            # 如果已连接，也会报错，这是正常的
            if "already connected" not in str(e).lower():
                raise

    @pytest.mark.asyncio
    async def test_list_devices_includes_real(self):
        """测试设备列表包含真实设备"""
        driver = AdbCliDriver()

        devices = await driver.list_devices()

        # 应该至少有一个设备
        assert len(devices) >= 1
        # 真实设备应该在列表中
        assert REAL_DEVICE_ID in devices

    @pytest.mark.asyncio
    async def test_get_device_info_real(self):
        """测试获取真实设备信息"""
        driver = AdbCliDriver()

        info = await driver.get_device_info(REAL_DEVICE_ID)

        # 验证基本信息
        assert info.id == REAL_DEVICE_ID
        assert info.model != ""  # 应该有型号
        assert info.os_version != ""  # 应该有 Android 版本
        assert info.resolution != (0, 0)  # 应该有分辨率
        assert 0 <= info.battery <= 100  # 电量在合理范围
        assert info.status == "online"

        # 打印设备信息（调试用）
        print(f"\n设备信息：")
        print(f"  ID: {info.id}")
        print(f"  型号: {info.model}")
        print(f"  Android: {info.os_version}")
        print(f"  分辨率: {info.resolution}")
        print(f"  电量: {info.battery}%")
        print(f"  状态: {info.status}")

    @pytest.mark.asyncio
    async def test_check_connection_real(self):
        """测试真实设备连接稳定性"""
        driver = AdbCliDriver()

        is_connected = await driver.check_connection(REAL_DEVICE_ID)

        assert is_connected is True

    @pytest.mark.asyncio
    async def test_shell_echo_real(self):
        """测试在真实设备上执行 shell 命令"""
        driver = AdbCliDriver()

        result = await driver.shell(REAL_DEVICE_ID, "echo 'test'")

        assert "test" in result

    @pytest.mark.asyncio
    async def test_disconnect_tcp(self):
        """测试断开 TCP/IP 连接"""
        driver = AdbCliDriver()

        # 断开连接不应该抛出异常
        await driver.disconnect_tcp(REAL_DEVICE_IP, REAL_DEVICE_PORT)

        # 断开后应该检测不到连接
        # 注意：某些设备断开后仍可能短暂显示为连接状态
        # 所以这里不做断言


# ---------------------------------------------------------------------------
# DeviceService 真实测试
# ---------------------------------------------------------------------------

class TestDeviceServiceRealDevice:
    """测试 DeviceService 与真实设备"""

    @pytest.mark.asyncio
    async def test_list_devices_service(self):
        """测试服务层列出设备"""
        driver = AdbCliDriver()
        repo = InMemoryDeviceRepository()
        service = DeviceService(adb=driver, repo=repo)

        devices = await service.list_devices()

        # 应该至少有一个设备
        assert len(devices) >= 1

        # 所有设备应该被持久化到仓库
        all_in_repo = await repo.list_all()
        assert len(all_in_repo) >= 1

    @pytest.mark.asyncio
    async def test_get_device_service(self):
        """测试服务层获取设备信息"""
        driver = AdbCliDriver()
        repo = InMemoryDeviceRepository()
        service = DeviceService(adb=driver, repo=repo)

        # 先列出设备（会持久化）
        await service.list_devices()

        # 然后获取特定设备
        device = await service.get_device(REAL_DEVICE_ID)

        # 设备应该存在
        assert device is not None
        assert device.id == REAL_DEVICE_ID
        assert device.model != ""

    @pytest.mark.asyncio
    async def test_connect_tcp_service(self):
        """测试服务层 TCP 连接"""
        driver = AdbCliDriver()
        repo = InMemoryDeviceRepository()
        service = DeviceService(adb=driver, repo=repo)

        try:
            device_id = await service.connect_tcp(REAL_DEVICE_IP, REAL_DEVICE_PORT)
            assert device_id == REAL_DEVICE_ID

            # 设备应该被保存到仓库
            device = await repo.get(REAL_DEVICE_ID)
            assert device is not None
        except AdbError as e:
            # 如果已连接，这是正常的
            if "already connected" not in str(e).lower():
                raise

    @pytest.mark.asyncio
    async def test_background_refresh_detects_real_device(self):
        """测试后台刷新能检测到真实设备"""
        driver = AdbCliDriver()
        repo = InMemoryDeviceRepository()
        service = DeviceService(adb=driver, repo=repo)

        # 设置很短的刷新间隔（0.2秒）
        service._refresh_interval = 0.2

        # 启动后台刷新
        await service.start()

        try:
            # 等待足够时间让刷新执行至少一次（刷新间隔 + 执行时间 + 缓冲）
            await asyncio.sleep(1.0)

            # 检查设备是否被检测到
            all_devices = await repo.list_all()
            assert len(all_devices) >= 1, "后台刷新未能检测到设备"

            # 检查真实设备是否在列表中
            device_ids = [d.id for d in all_devices]
            assert REAL_DEVICE_ID in device_ids, f"真实设备 {REAL_DEVICE_ID} 未在列表中"
        finally:
            # 停止后台刷新
            await service.stop()

    @pytest.mark.asyncio
    async def test_event_callbacks_real_device(self):
        """测试真实设备触发事件回调"""
        driver = AdbCliDriver()
        repo = InMemoryDeviceRepository()
        service = DeviceService(adb=driver, repo=repo)

        connected_devices = []
        disconnected_devices = []

        async def on_connected(device):
            connected_devices.append(device.id)

        async def on_disconnected(device_id):
            disconnected_devices.append(device_id)

        service.on_device_connected(on_connected)
        service.on_device_disconnected(on_disconnected)

        # 清空之前的设备记录，模拟新连接
        service._previous_devices = set()

        # 设置较短的刷新间隔（0.2秒）
        service._refresh_interval = 0.2

        await service.start()

        try:
            # 等待刷新检测（刷新间隔 + 执行时间 + 缓冲）
            await asyncio.sleep(1.0)

            # 应该触发连接回调
            assert len(connected_devices) >= 1, "未触发设备连接回调"
            assert REAL_DEVICE_ID in connected_devices, f"设备 {REAL_DEVICE_ID} 未触发连接回调"
        finally:
            await service.stop()


# ---------------------------------------------------------------------------
# 性能测试（可选）
# ---------------------------------------------------------------------------

class TestPerformance:
    """简单性能测试"""

    @pytest.mark.asyncio
    async def test_list_devices_performance(self):
        """测试列出设备的性能"""
        driver = AdbCliDriver()

        import time
        start = time.time()

        for _ in range(10):
            await driver.list_devices()

        elapsed = time.time() - start
        avg = elapsed / 10

        print(f"\n列出设备 10 次：")
        print(f"  总耗时: {elapsed:.2f}s")
        print(f"  平均耗时: {avg:.2f}s")

        # 平均应该在 1 秒内
        assert avg < 1.0

    @pytest.mark.asyncio
    async def test_get_device_info_performance(self):
        """测试获取设备信息的性能"""
        driver = AdbCliDriver()

        import time
        start = time.time()

        for _ in range(5):
            await driver.get_device_info(REAL_DEVICE_ID)

        elapsed = time.time() - start
        avg = elapsed / 5

        print(f"\n获取设备信息 5 次：")
        print(f"  总耗时: {elapsed:.2f}s")
        print(f"  平均耗时: {avg:.2f}s")

        # 平均应该在 2 秒内（需要多个 ADB 命令）
        assert avg < 2.0


# ---------------------------------------------------------------------------
# 清理测试
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def ensure_device_connected():
    """确保测试前后设备都已连接"""
    # 测试前连接
    import subprocess
    subprocess.run(["adb", "connect", f"{REAL_DEVICE_IP}:{REAL_DEVICE_PORT}"], check=False)

    yield

    # 测试后保持连接（不主动断开，方便后续测试）
    pass
