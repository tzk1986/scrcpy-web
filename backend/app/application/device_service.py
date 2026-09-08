"""
设备管理服务
=============

层：应用层。

设备相关操作的用例编排器。

依赖（通过 domain.ports 的 Protocol 类注入）：
    - AdbDriver：与物理设备通信
    - DeviceRepository：持久化设备状态

用例：
    - list_devices():   扫描 ADB 获取连接的设备，获取信息，持久化
    - get_device():     从仓库检索设备
    - install_apk():    在单个设备上安装 APK
    - batch_install():  在多个设备上安装同一个 APK（顺序执行）
    - screenshot():     截取设备屏幕为 PNG 字节

注意：batch_install 当前是顺序执行的。对于大型设备集群，
考虑使用 asyncio.gather() + 信号量实现并发。
"""

from typing import Optional

from app.core.logging import get_logger
from app.domain.device import DeviceInfo
from app.domain.ports import AdbDriver, DeviceRepository

logger = get_logger(__name__)


class DeviceService:
    """设备管理用例。"""

    def __init__(self, adb: AdbDriver, repo: DeviceRepository):
        """
        参数：
            adb: 用于设备通信的 ADB 驱动。
            repo: 用于持久化的设备仓库。
        """
        self.adb = adb
        self.repo = repo

    async def list_devices(self) -> list[DeviceInfo]:
        """
        扫描已连接的设备并返回完整信息。

        副作用：
            - 查询 ADB 获取已连接设备序列号列表
            - 获取每个设备的详细信息（型号、OS、分辨率等）
            - 将每个设备持久化到仓库（upsert）

        返回：
            所有当前已连接设备的 DeviceInfo 对象列表。
        """
        logger.info("listing_devices")
        device_ids = await self.adb.list_devices()
        devices = []
        for device_id in device_ids:
            info = await self.adb.get_device_info(device_id)
            await self.repo.save(info)
            devices.append(info)
        return devices

    async def get_device(self, device_id: str) -> Optional[DeviceInfo]:
        """
        通过 ADB 序列号从仓库检索设备。

        参数：
            device_id: ADB 序列号。

        返回：
            找到则返回 DeviceInfo，否则返回 None。
        """
        return await self.repo.get(device_id)

    async def install_apk(self, device_id: str, apk_path: str) -> str:
        """
        在单个设备上安装 APK。

        参数：
            device_id: ADB 序列号。
            apk_path: APK 文件的主机路径。

        返回：
            人可读的成功消息。

        异常：
            AdbError: 安装失败时。
        """
        logger.info("installing_apk", device_id=device_id, apk=apk_path)
        await self.adb.install(device_id, apk_path)
        return f"APK installed on {device_id}"

    async def batch_install(self, device_ids: list[str], apk_path: str) -> list[str]:
        """
        在多个设备上安装同一个 APK（顺序执行）。

        单个设备上的错误会被捕获并作为结果列表的一部分报告——
        它们不会中止整个批量操作。

        参数：
            device_ids: ADB 序列号列表。
            apk_path: APK 文件的主机路径。

        返回：
            每个设备的结果字符串列表（如 "emulator-5554: success"）。
        """
        logger.info("batch_install", devices=device_ids, apk=apk_path)
        results = []
        for device_id in device_ids:
            try:
                await self.adb.install(device_id, apk_path)
                results.append(f"{device_id}: success")
            except Exception as e:
                results.append(f"{device_id}: failed - {e}")
        return results

    async def screenshot(self, device_id: str) -> bytes:
        """
        从设备截取屏幕截图。

        参数：
            device_id: ADB 序列号。

        返回：
            PNG 图像数据字节。
        """
        return await self.adb.screenshot(device_id)
