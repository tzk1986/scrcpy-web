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
    - start():          启动后台设备状态自动刷新
    - stop():           停止后台刷新
    - on_device_connected():    注册设备连接回调
    - on_device_disconnected(): 注册设备断开回调

注意：batch_install 当前是顺序执行的。对于大型设备集群，
考虑使用 asyncio.gather() + 信号量实现并发。
"""

import asyncio
from typing import Awaitable, Callable, Optional

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
        self._refresh_task: asyncio.Task | None = None
        self._refresh_interval = 5  # 刷新间隔（秒）
        self._previous_devices: set[str] = set()
        self._on_device_connected: list[Callable[[DeviceInfo], Awaitable[None]]] = []
        self._on_device_disconnected: list[Callable[[str], Awaitable[None]]] = []

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
        在多个设备上安装同一个 APK（并发执行）。

        单个设备上的错误会被捕获并作为结果列表的一部分报告——
        它们不会中止整个批量操作。使用信号量限制并发数为 5。

        参数：
            device_ids: ADB 序列号列表。
            apk_path: APK 文件的主机路径。

        返回：
            每个设备的结果字符串列表（如 "emulator-5554: success"）。
        """
        logger.info("batch_install", devices=device_ids, apk=apk_path)

        semaphore = asyncio.Semaphore(5)  # 限制并发数为 5

        async def install_one(device_id: str) -> str:
            """在单个设备上安装 APK"""
            async with semaphore:
                try:
                    await self.adb.install(device_id, apk_path)
                    return f"{device_id}: success"
                except Exception as e:
                    logger.error("batch_install_failed", device=device_id, error=str(e))
                    return f"{device_id}: failed - {e}"

        # 并发执行所有安装任务
        results = await asyncio.gather(*[install_one(d) for d in device_ids])
        return list(results)

    async def screenshot(self, device_id: str) -> bytes:
        """
        从设备截取屏幕截图。

        参数：
            device_id: ADB 序列号。

        返回：
            PNG 图像数据字节。
        """
        return await self.adb.screenshot(device_id)

    async def connect_tcp(self, ip: str, port: int = 5555) -> str:
        """
        通过 TCP/IP 连接到设备。

        参数：
            ip: 设备的 IP 地址。
            port: ADB 端口（默认 5555）。

        返回：
            设备 ID（格式为 "ip:port"）。

        异常：
            AdbError: 连接失败时。
        """
        device_id = await self.adb.connect_tcp(ip, port)
        # 连接成功后获取设备信息并保存
        info = await self.adb.get_device_info(device_id)
        await self.repo.save(info)
        return device_id

    async def disconnect_tcp(self, ip: str, port: int = 5555):
        """
        断开 TCP/IP 连接。

        参数：
            ip: 设备的 IP 地址。
            port: ADB 端口（默认 5555）。
        """
        device_id = f"{ip}:{port}"
        await self.adb.disconnect_tcp(ip, port)
        # 从仓库中删除设备
        await self.repo.delete(device_id)

    async def start(self):
        """
        启动后台设备状态自动刷新。

        每 5 秒刷新一次设备列表，自动检测设备上下线。
        应在应用启动时调用（如 lifespan.py）。
        """
        if self._refresh_task is not None:
            logger.warning("device_refresh_already_running")
            return

        logger.info("starting_device_refresh")
        self._refresh_task = asyncio.create_task(self._refresh_loop())

    async def stop(self):
        """
        停止后台设备状态刷新。

        应在应用关闭时调用（如 lifespan.py）。
        """
        if self._refresh_task is None:
            logger.debug("device_refresh_not_running")
            return

        logger.info("stopping_device_refresh")
        self._refresh_task.cancel()
        try:
            await self._refresh_task
        except asyncio.CancelledError:
            pass
        self._refresh_task = None

    async def _refresh_loop(self):
        """
        后台设备刷新循环。

        每 _refresh_interval 秒刷新一次设备列表，检测设备上下线。
        当任务被取消时优雅退出。
        """
        try:
            while True:
                try:
                    current_device_ids = set(await self.adb.list_devices())
                    previous_device_ids = self._previous_devices.copy()

                    # 检测新连接的设备
                    new_devices = current_device_ids - previous_device_ids
                    for device_id in new_devices:
                        try:
                            info = await self.adb.get_device_info(device_id)
                            await self.repo.save(info)
                            await self._trigger_connected_callbacks(info)
                        except Exception as e:
                            logger.error("new_device_error", device=device_id, error=str(e))

                    # 检测断开的设备
                    disconnected_devices = previous_device_ids - current_device_ids
                    for device_id in disconnected_devices:
                        try:
                            await self.repo.delete(device_id)
                            await self._trigger_disconnected_callbacks(device_id)
                        except Exception as e:
                            logger.error("disconnect_device_error", device=device_id, error=str(e))

                    self._previous_devices = current_device_ids

                except Exception as e:
                    logger.error("device_refresh_failed", error=str(e))

                await asyncio.sleep(self._refresh_interval)
        except asyncio.CancelledError:
            logger.debug("device_refresh_cancelled")
            raise

    def on_device_connected(self, callback: Callable[[DeviceInfo], Awaitable[None]]):
        """
        注册设备连接回调。

        当新设备连接时，会调用所有注册的回调函数。

        参数：
            callback: 异步回调函数，接收 DeviceInfo 参数。
        """
        self._on_device_connected.append(callback)
        logger.debug("device_connected_callback_registered", callback=callback.__name__)

    def on_device_disconnected(self, callback: Callable[[str], Awaitable[None]]):
        """
        注册设备断开回调。

        当设备断开连接时，会调用所有注册的回调函数。

        参数：
            callback: 异步回调函数，接收 device_id 参数。
        """
        self._on_device_disconnected.append(callback)
        logger.debug("device_disconnected_callback_registered", callback=callback.__name__)

    async def _trigger_connected_callbacks(self, device: DeviceInfo):
        """触发所有设备连接回调"""
        for callback in self._on_device_connected:
            try:
                await callback(device)
            except Exception as e:
                logger.error(
                    "device_connected_callback_failed",
                    device=device.id,
                    callback=callback.__name__,
                    error=str(e)
                )

    async def _trigger_disconnected_callbacks(self, device_id: str):
        """触发所有设备断开回调"""
        for callback in self._on_device_disconnected:
            try:
                await callback(device_id)
            except Exception as e:
                logger.error(
                    "device_disconnected_callback_failed",
                    device=device_id,
                    callback=callback.__name__,
                    error=str(e)
                )
