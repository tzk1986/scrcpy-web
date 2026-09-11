"""
scrcpy 二进制控制协议
=====================

参考 py-scrcpy-client 的 control.py 和 scrcpy 官方源码实现。
通过控制 socket 发送二进制消息到 scrcpy-server。

协议格式验证：
    - 触摸事件：32 字节（type + action + pointer_id + position + pressure + buttons）
    - 按键事件：13 字节（type + action + keycode + repeat + metaState）
    - 文本事件：5 + text 字节（type + length + text）

与 scrcpy-server v2.4 协议完全兼容。
"""

import asyncio
import struct

from app.core.logging import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# 控制消息类型（与 scrcpy 协议一致）
# ---------------------------------------------------------------------------

TYPE_INJECT_KEYCODE = 0
TYPE_INJECT_TEXT = 1
TYPE_INJECT_TOUCH_EVENT = 2
TYPE_INJECT_SCROLL_EVENT = 3
TYPE_BACK_OR_SCREEN_ON = 4
TYPE_EXPAND_NOTIFICATION_PANEL = 5
TYPE_EXPAND_SETTINGS_PANEL = 6
TYPE_COLLAPSE_PANELS = 7
TYPE_SET_DISPLAY_POWER = 10

# ---------------------------------------------------------------------------
# 触摸/按键动作
# ---------------------------------------------------------------------------

ACTION_DOWN = 0
ACTION_UP = 1
ACTION_MOVE = 2


class ControlSender:
    """
    scrcpy 控制消息发送器。

    通过已建立的控制 socket 发送二进制控制消息到 scrcpy-server。
    所有方法都是异步的，使用 asyncio.Lock 保护并发写入。
    """

    def __init__(self, writer: asyncio.StreamWriter, resolution: tuple[int, int]):
        """
        初始化控制消息发送器。

        参数：
            writer: 控制 socket 的 StreamWriter
            resolution: 屏幕分辨率 (width, height)
        """
        self._writer = writer
        self._resolution = resolution  # (width, height)
        self._lock = asyncio.Lock()

    @property
    def resolution(self) -> tuple[int, int]:
        """获取当前屏幕分辨率。"""
        return self._resolution

    def update_resolution(self, resolution: tuple[int, int]):
        """更新屏幕分辨率（屏幕旋转时调用）。"""
        self._resolution = resolution
        logger.info("control_resolution_updated", resolution=resolution)

    async def touch(
        self,
        x: int,
        y: int,
        action: int,
        pointer_id: int = -1,
    ):
        """
        发送触摸事件（32 字节）。

        参数：
            x: X 坐标（设备屏幕坐标）
            y: Y 坐标（设备屏幕坐标）
            action: 动作类型（ACTION_DOWN/UP/MOVE）
            pointer_id: 触摸点 ID（-1 表示虚拟鼠标）

        格式：
            type(1) + action(1) + pointer_id(8) + position(12) + pressure(2) + buttons(8)
            = 32 字节
        """
        # 构建触摸事件消息
        # 注意：先 pack type，再 pack 其余字段
        package = struct.pack(">B", TYPE_INJECT_TOUCH_EVENT)
        package += struct.pack(
            ">BqiiHHHii",
            action,              # 1 byte: action
            pointer_id,          # 8 bytes: pointer_id (signed long long)
            int(x),              # 4 bytes: x
            int(y),              # 4 bytes: y
            self._resolution[0], # 2 bytes: screen_width
            self._resolution[1], # 2 bytes: screen_height
            0xFFFF,              # 2 bytes: pressure (max = 1.0)
            1,                   # 4 bytes: action_button (primary)
            1,                   # 4 bytes: buttons (primary pressed)
        )
        await self._send(package)

    async def keycode(
        self,
        keycode: int,
        action: int = ACTION_DOWN,
        repeat: int = 0,
        meta_state: int = 0,
    ):
        """
        发送按键事件（13 字节）。

        参数：
            keycode: Android keycode（如 KEYCODE_HOME=3, KEYCODE_BACK=4）
            action: 动作类型（ACTION_DOWN/UP）
            repeat: 重复次数
            meta_state: 元状态（Shift/Ctrl 等）

        格式：
            type(1) + action(1) + keycode(4) + repeat(4) + metaState(4)
            = 13 字节
        """
        package = struct.pack(">B", TYPE_INJECT_KEYCODE)
        package += struct.pack(
            ">Biii",
            action,      # 1 byte: action
            keycode,     # 4 bytes: keycode
            repeat,      # 4 bytes: repeat
            meta_state,  # 4 bytes: metaState
        )
        await self._send(package)

    async def text(self, text: str):
        """
        发送文本输入。

        参数：
            text: 要输入的文本（UTF-8 编码）

        格式：
            type(1) + length(4) + text
            = 5 + len(text) 字节
        """
        package = struct.pack(">B", TYPE_INJECT_TEXT)
        buffer = text.encode("utf-8")
        package += struct.pack(">i", len(buffer)) + buffer
        await self._send(package)

    async def scroll(
        self,
        x: int,
        y: int,
        h_scroll: int,
        v_scroll: int,
    ):
        """
        发送滚动事件（21 字节）。

        参数：
            x: X 坐标
            y: Y 坐标
            h_scroll: 水平滚动量（-16 到 16）
            v_scroll: 垂直滚动量（-16 到 16）

        格式：
            type(1) + position(12) + hScroll(2) + vScroll(2) + buttons(4)
            = 21 字节
        """
        package = struct.pack(">B", TYPE_INJECT_SCROLL_EVENT)
        package += struct.pack(
            ">iiHHhh",
            int(x),              # 4 bytes: x
            int(y),              # 4 bytes: y
            self._resolution[0], # 2 bytes: screen_width
            self._resolution[1], # 2 bytes: screen_height
            h_scroll,            # 2 bytes: hScroll (i16 fixed point)
            v_scroll,            # 2 bytes: vScroll (i16 fixed point)
        )
        # 追加 buttons (4 bytes)
        package += struct.pack(">i", 0)
        await self._send(package)

    async def back_or_screen_on(self, action: int = ACTION_DOWN):
        """
        发送返回或唤醒屏幕事件。

        参数：
            action: 动作类型（ACTION_DOWN 时唤醒屏幕）

        格式：
            type(1) + action(1) = 2 字节
        """
        package = struct.pack(">B", TYPE_BACK_OR_SCREEN_ON)
        package += struct.pack(">B", action)
        await self._send(package)

    async def expand_notification_panel(self):
        """发送展开通知面板命令（1 字节）。"""
        package = struct.pack(">B", TYPE_EXPAND_NOTIFICATION_PANEL)
        await self._send(package)

    async def expand_settings_panel(self):
        """发送展开设置面板命令（1 字节）。"""
        package = struct.pack(">B", TYPE_EXPAND_SETTINGS_PANEL)
        await self._send(package)

    async def collapse_panels(self):
        """发送折叠面板命令（1 字节）。"""
        package = struct.pack(">B", TYPE_COLLAPSE_PANELS)
        await self._send(package)

    async def set_display_power(self, on: bool):
        """
        设置屏幕电源状态。

        参数：
            on: True 开启屏幕，False 关闭

        格式：
            type(1) + on(1) = 2 字节
        """
        package = struct.pack(">B", TYPE_SET_DISPLAY_POWER)
        package += struct.pack(">?", on)
        await self._send(package)

    async def _send(self, data: bytes):
        """
        线程安全发送数据。

        使用 asyncio.Lock 保护并发写入，确保消息完整性。
        """
        async with self._lock:
            try:
                self._writer.write(data)
                await asyncio.wait_for(self._writer.drain(), timeout=1.0)
            except asyncio.TimeoutError:
                logger.warning("control_send_timeout", data_len=len(data))
                raise
            except Exception as e:
                logger.error("control_send_error", error=str(e))
                raise
