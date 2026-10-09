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
    - batch_install():  在多个设备上安装同一个 APK（并发执行，信号量上限 5）
    - screenshot():     截取设备屏幕为 PNG 字节
    - start():          启动后台设备状态自动刷新
    - stop():           停止后台刷新
    - on_device_connected():    注册设备连接回调
    - on_device_disconnected(): 注册设备断开回调

注意：batch_install 已用 asyncio.gather() + 信号量（并发上限 5）实现并发；
单设备失败被捕获并计入结果列表，不中止整批。

方案 35：设备列表查询并发化 + 失败兜底保留 + 冷却负缓存 + 查询任务复用
（见 list_devices / _get_info_cached / _fetch_info / _fallback_device）。
"""

import asyncio
import time
from dataclasses import replace
from typing import Awaitable, Callable, Optional

from app.core.logging import get_logger
from app.domain.device import DeviceInfo
from app.domain.ports import AdbDriver, DeviceRepository

logger = get_logger(__name__)

# 信息查询失败后的冷却时长（秒，方案 35 D4）：卡设备 30s 内不重试，
# 边界设备重试频率低（对设备友好）；到期自动恢复查询。
_COOLDOWN_SECONDS = 30

# 设备信息查询的设备级并发上限（方案 35 D1）：防设备数增长后进程失控。
_INFO_CONCURRENCY = 8


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
        self._refresh_task: asyncio.Task[None] | None = None
        self._refresh_interval = 5  # 刷新间隔（秒）
        self._previous_devices: set[str] = set()
        self._on_device_connected: list[Callable[[DeviceInfo], Awaitable[None]]] = []
        self._on_device_disconnected: list[Callable[[str], Awaitable[None]]] = []
        self._info_semaphore = asyncio.Semaphore(_INFO_CONCURRENCY)
        self._info_cooldown: dict[str, float] = {}  # device_id → 冷却截止（time.monotonic）
        self._info_tasks: dict[str, asyncio.Task[Optional[DeviceInfo]]] = {}

    async def list_devices(self) -> list[DeviceInfo]:
        """
        扫描已连接的设备并返回完整信息。

        并发查询所有设备详情（方案 35 D1）——总时长从「Σ 各设备」降为
        「max(单设备)」；查询失败的设备不消失，走缓存/最小信息兜底并标记
        `slow`（D3/D7）。

        副作用：
            - 查询 ADB 获取已连接设备序列号列表
            - 获取每个设备的详细信息（型号、OS、分辨率等）
            - 查询成功的设备持久化到仓库（upsert）

        返回：
            所有当前已连接设备的 DeviceInfo 对象列表（含失败兜底条目）。
        """
        logger.info("listing_devices")
        device_ids = await self.adb.list_devices()
        results = await asyncio.gather(*(self._get_info_cached(d) for d in device_ids))

        devices: list[DeviceInfo] = []
        for device_id, info in zip(device_ids, results):
            if info is not None:
                devices.append(info)
            else:
                devices.append(await self._fallback_device(device_id))
        return devices

    async def _get_info_cached(self, device_id: str) -> Optional[DeviceInfo]:
        """
        取设备信息：冷却中直接返回 None（走兜底）；否则复用或创建查询任务。

        任务复用（方案 35 D5）：并发的列表请求对同一设备共享一个在途
        查询任务，不重复发起 adb 查询。检查-创建之间无 await——asyncio
        单线程下即为原子。
        """
        if time.monotonic() < self._info_cooldown.get(device_id, 0.0):
            return None
        task = self._info_tasks.get(device_id)
        if task is None or task.done():
            task = asyncio.create_task(self._fetch_info(device_id))
            self._info_tasks[device_id] = task
        return await task

    async def _fetch_info(self, device_id: str) -> Optional[DeviceInfo]:
        """
        单设备信息查询任务体：成功清冷却并落库，失败记冷却（方案 35 D4）。

        异常不出任务（避免 "Task exception was never retrieved" 噪音），
        冷却置位与结果一并落地；失败返回 None 由调用方走兜底。
        """
        async with self._info_semaphore:
            try:
                info = await self.adb.get_device_info(device_id)
            except Exception as e:
                logger.warning("device_info_failed", device=device_id, error=str(e))
                self._info_cooldown[device_id] = time.monotonic() + _COOLDOWN_SECONDS
                return None
            self._info_cooldown.pop(device_id, None)
            await self.repo.save(info)
            return info

    async def _fallback_device(self, device_id: str) -> DeviceInfo:
        """
        查询失败设备的兜底条目（方案 35 D3/D7）。

        有缓存返回缓存内容（slow=True，**不**回写覆盖——避免坏数据污染
        好数据）；无缓存（新设备首查失败）返回最小信息。
        """
        cached = await self.repo.get(device_id)
        if cached is not None:
            return replace(cached, slow=True)
        return DeviceInfo(
            id=device_id,
            model="",
            os_version="",
            resolution=(0, 0),
            battery=0,
            status="online",
            slow=True,
        )

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

    async def screenshot_raw_gzip(self, device_id: str) -> bytes:
        """
        截取设备屏幕 raw 帧并经设备端 gzip 压缩（方案 29）。

        参数：
            device_id: ADB 序列号。

        返回：
            gzip 压缩的 raw RGBA 帧数据（含 screencap 帧头）。
        """
        return await self.adb.screenshot_raw_gzip(device_id)

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
        # 连接成功后获取设备信息并保存；信息查询失败不阻断连接本身
        # （方案 35 D6：get_device_info 命令走 5s 短超时，失败仅告警）
        try:
            info = await self.adb.get_device_info(device_id)
        except Exception as e:
            logger.warning("device_info_failed", device=device_id, error=str(e))
            return device_id
        await self.repo.save(info)
        return device_id

    async def disconnect_tcp(self, ip: str, port: int = 5555) -> None:
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
        # 清理查询缓存（方案 35 D4）：防止断开后残留冷却/任务占用内存
        self._info_cooldown.pop(device_id, None)
        self._info_tasks.pop(device_id, None)

    async def start(self) -> None:
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

    async def stop(self) -> None:
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

    async def _refresh_loop(self) -> None:
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

    def on_device_connected(self, callback: Callable[[DeviceInfo], Awaitable[None]]) -> None:
        """
        注册设备连接回调。

        当新设备连接时，会调用所有注册的回调函数。

        参数：
            callback: 异步回调函数，接收 DeviceInfo 参数。
        """
        self._on_device_connected.append(callback)
        logger.debug("device_connected_callback_registered", callback=callback.__name__)

    def on_device_disconnected(self, callback: Callable[[str], Awaitable[None]]) -> None:
        """
        注册设备断开回调。

        当设备断开连接时，会调用所有注册的回调函数。

        参数：
            callback: 异步回调函数，接收 device_id 参数。
        """
        self._on_device_disconnected.append(callback)
        logger.debug("device_disconnected_callback_registered", callback=callback.__name__)

    async def _trigger_connected_callbacks(self, device: DeviceInfo) -> None:
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

    async def _trigger_disconnected_callbacks(self, device_id: str) -> None:
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
