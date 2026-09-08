"""
输入控制器
===========

通过 ADB 向 Android 设备发送输入事件。

支持的输入类型：
    - tap(x, y): 触摸点击
    - swipe(x1, y1, x2, y2, duration): 滑动
    - key(keycode): 按键（使用 Android keycode）
    - text(string): 输入文本
    - long_press(x, y, duration): 长按

ADB 命令映射：
    触摸点击：adb shell input tap <x> <y>
    滑动：adb shell input swipe <x1> <y1> <x2> <y2> <duration>
    按键：adb shell input keyevent <keycode>
    文本输入：adb shell input text "<text>"

使用示例：
    ```python
    controller = InputController()

    # 触摸点击
    await controller.tap(device_id, x=100, y=200)

    # 滑动
    await controller.swipe(
        device_id,
        x1=100, y1=200,
        x2=300, y2=400,
        duration=300
    )

    # 按键
    await controller.key(device_id, keycode=4)  # BACK 键

    # 输入文本
    await controller.text(device_id, "hello world")
    ```

注意事项：
    - 所有方法都是异步的，使用 asyncio.create_subprocess_exec
    - 坐标基于设备的实际分辨率（不是视频显示尺寸）
    - 文本输入中空格需要转义为 %s
    - 如果设备锁屏，输入可能被阻止
"""

import asyncio

from app.core.logging import get_logger
from .constants import ADB_TIMEOUT_SECONDS

logger = get_logger(__name__)


class InputController:
    """
    ADB 输入控制器。

    封装 ADB input 命令，提供异步接口。
    """

    async def tap(self, device_id: str, x: int, y: int):
        """
        触摸点击。

        参数：
            device_id: 设备的 ADB 序列号。
            x: X 坐标（基于设备分辨率）。
            y: Y 坐标（基于设备分辨率）。

        实现：
            adb shell input tap <x> <y>
        """
        await self._run_adb(device_id, "shell", "input", "tap", str(x), str(y))
        logger.debug("input_tap", device=device_id, x=x, y=y)

    async def swipe(
        self,
        device_id: str,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        duration: int = 300
    ):
        """
        滑动。

        参数：
            device_id: 设备的 ADB 序列号。
            x1, y1: 起始坐标。
            x2, y2: 结束坐标。
            duration: 滑动持续时间（毫秒），默认 300ms。

        实现：
            adb shell input swipe <x1> <y1> <x2> <y2> <duration>
        """
        await self._run_adb(
            device_id,
            "shell", "input", "swipe",
            str(x1), str(y1), str(x2), str(y2), str(duration)
        )
        logger.debug(
            "input_swipe",
            device=device_id,
            start=(x1, y1),
            end=(x2, y2),
            duration=duration
        )

    async def key(self, device_id: str, keycode: int):
        """
        按键。

        参数：
            device_id: 设备的 ADB 序列号。
            keycode: Android keycode（见 constants.py）。

        常用 keycode：
            3: HOME
            4: BACK
            26: POWER
            82: MENU
            24: VOLUME_UP
            25: VOLUME_DOWN

        实现：
            adb shell input keyevent <keycode>
        """
        await self._run_adb(device_id, "shell", "input", "keyevent", str(keycode))
        logger.debug("input_key", device=device_id, keycode=keycode)

    async def text(self, device_id: str, text: str):
        """
        输入文本。

        参数：
            device_id: 设备的 ADB 序列号。
            text: 要输入的文本。

        注意事项：
            - 空格需要转义为 %s（ADB input text 命令的限制）
            - 不支持中文字符（只支持 ASCII）
            - 特殊字符需要转义

        实现：
            adb shell input text "<text>"
        """
        # 转义空格和特殊字符
        escaped_text = text.replace(" ", "%s")
        escaped_text = escaped_text.replace("&", "\\&")
        escaped_text = escaped_text.replace("<", "\\<")
        escaped_text = escaped_text.replace(">", "\\>")
        escaped_text = escaped_text.replace("|", "\\|")

        await self._run_adb(device_id, "shell", "input", "text", escaped_text)
        logger.debug("input_text", device=device_id, text=text)

    async def long_press(self, device_id: str, x: int, y: int, duration: int = 1000):
        """
        长按。

        参数：
            device_id: 设备的 ADB 序列号。
            x, y: 长按位置坐标。
            duration: 长按持续时间（毫秒），默认 1000ms。

        实现：
            使用 swipe 命令，起始和结束坐标相同，持续时间为 duration。
        """
        await self.swipe(device_id, x, y, x, y, duration)
        logger.debug("input_long_press", device=device_id, x=x, y=y, duration=duration)

    async def _run_adb(self, device_id: str, *args: str):
        """
        执行 ADB 命令的内部辅助方法。

        参数：
            device_id: 设备的 ADB 序列号。
            *args: ADB 命令参数（如 "shell", "input", "tap", "100", "200"）。

        实现：
            使用 asyncio.create_subprocess_exec 异步执行命令。
            如果命令失败，抛出 RuntimeError。

        异常：
            RuntimeError: ADB 命令执行失败时。
        """
        cmd = ["adb", "-s", device_id] + list(args)

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=ADB_TIMEOUT_SECONDS
            )

            if process.returncode != 0:
                error_msg = stderr.decode().strip()
                logger.error(
                    "adb_command_failed",
                    command=" ".join(cmd),
                    returncode=process.returncode,
                    error=error_msg
                )
                raise RuntimeError(f"ADB command failed: {error_msg}")

        except asyncio.TimeoutError:
            logger.error("adb_command_timeout", command=" ".join(cmd))
            raise RuntimeError("ADB command timed out")
        except Exception as e:
            logger.error("adb_command_error", command=" ".join(cmd), error=str(e))
            raise

    async def handle_input_event(self, device_id: str, data: dict):
        """
        处理输入事件（从 WebSocket JSON 解析）。

        参数：
            device_id: 设备的 ADB 序列号。
            data: JSON 数据，包含 action 字段和对应参数。

        支持的 action：
            - "touch": {"action": "touch", "x": 100, "y": 200}
            - "swipe": {"action": "swipe", "x1": 100, "y1": 200, "x2": 300, "y2": 400}
            - "key": {"action": "key", "keycode": 4}
            - "text": {"action": "text", "text": "hello"}
            - "long_press": {"action": "long_press", "x": 100, "y": 200, "duration": 1000}

        异常：
            ValueError: 未知的 action 类型。
        """
        action = data.get("action")

        if action == "touch":
            await self.tap(device_id, data["x"], data["y"])
        elif action == "swipe":
            duration = data.get("duration", 300)
            await self.swipe(
                device_id,
                data["x1"], data["y1"],
                data["x2"], data["y2"],
                duration
            )
        elif action == "key":
            await self.key(device_id, data["keycode"])
        elif action == "text":
            await self.text(device_id, data["text"])
        elif action == "long_press":
            duration = data.get("duration", 1000)
            await self.long_press(device_id, data["x"], data["y"], duration)
        else:
            raise ValueError(f"Unknown input action: {action}")
