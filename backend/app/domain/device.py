"""
设备实体
=========

层：领域层。

表示通过 ADB 连接的 Android 设备（USB 或 WiFi）。

这是一个纯数据类（dataclass），除了简单的属性访问器外没有行为。
被以下模块使用：
    - AdbDriver.get_device_info()      → 返回 DeviceInfo
    - DeviceService.list_devices()     → 返回 list[DeviceInfo]
    - DeviceRepository.save/get/list   → DeviceInfo 的持久化

属性：
    id:         ADB 序列号（如 "emulator-5554" 或 "192.168.1.5:5555"）
    model:      设备型号（如 "Pixel 6"）
    os_version: Android 版本字符串（如 "13"）
    resolution: 屏幕分辨率 (宽, 高) 元组
    battery:    电量 0-100
    status:     连接状态 — "online"、"offline" 或 "busy"
    ip:         （可选）WiFi 连接设备的 IP 地址
    port:       （可选）WiFi 连接设备的端口
    slow:       （瞬态）信息查询失败的标记（方案 35 D7）——不入库
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class DeviceInfo:
    """Android 设备信息快照。"""

    id: str
    model: str
    os_version: str
    resolution: tuple[int, int]
    battery: int
    status: str  # "online" | "offline" | "busy"
    ip: Optional[str] = None
    port: Optional[int] = None
    # 瞬态展示字段：该设备本轮信息查询失败（最常见原因为 adb 响应慢），
    # 显示内容可能为缓存/默认值；不参与 SQLite 持久化（列映射不涉及）。
    slow: bool = False

    @property
    def is_online(self) -> bool:
        """设备是否已连接并可接受命令。"""
        return self.status == "online"

    @property
    def is_busy(self) -> bool:
        """设备是否正忙（如正在流式传输视频）。"""
        return self.status == "busy"
