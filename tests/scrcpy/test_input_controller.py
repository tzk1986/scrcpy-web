"""
输入控制器测试
===============

测试 ADB 输入控制器的功能。

测试内容：
    - 触摸点击命令
    - 滑动命令
    - 按键命令
    - 文本输入命令
    - 长按命令
    - 输入事件路由
    - 错误处理
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.scrcpy.input_controller import InputController
from app.scrcpy.constants import (
    KEYCODE_HOME,
    KEYCODE_BACK,
    KEYCODE_POWER,
    INPUT_ACTION_TOUCH,
    INPUT_ACTION_SWIPE,
    INPUT_ACTION_KEY,
    INPUT_ACTION_TEXT,
    INPUT_ACTION_LONG_PRESS,
)


# ---------------------------------------------------------------------------
# 触摸点击测试
# ---------------------------------------------------------------------------

class TestTapCommand:
    """触摸点击命令测试"""

    @pytest.mark.asyncio
    async def test_tap_basic(self, mock_device_id, mock_adb_success):
        """测试基本触摸点击"""
        with patch('asyncio.create_subprocess_exec', side_effect=mock_adb_success):
            controller = InputController()
            await controller.tap(mock_device_id, x=100, y=200)
            # 验证 ADB 命令被正确调用
            # 实际测试中应该验证命令参数

    @pytest.mark.asyncio
    async def test_tap_coordinates(self, mock_device_id, mock_adb_success):
        """测试不同坐标值"""
        with patch('asyncio.create_subprocess_exec', side_effect=mock_adb_success):
            controller = InputController()

            # 测试左上角
            await controller.tap(mock_device_id, x=0, y=0)

            # 测试右下角
            await controller.tap(mock_device_id, x=1080, y=1920)

            # 测试中心点
            await controller.tap(mock_device_id, x=540, y=960)

    @pytest.mark.asyncio
    async def test_tap_with_negative_coordinates(self, mock_device_id, mock_adb_success):
        """测试负数坐标（ADB 可能接受也可能拒绝）"""
        with patch('asyncio.create_subprocess_exec', side_effect=mock_adb_success):
            controller = InputController()

            # 负数坐标会被传递给 ADB，是否失败取决于 ADB 实现
            # 这里只验证命令被调用，不验证是否抛出异常
            await controller.tap(mock_device_id, x=-10, y=-20)


# ---------------------------------------------------------------------------
# 滑动命令测试
# ---------------------------------------------------------------------------

class TestSwipeCommand:
    """滑动命令测试"""

    @pytest.mark.asyncio
    async def test_swipe_basic(self, mock_device_id, mock_adb_success):
        """测试基本滑动"""
        with patch('asyncio.create_subprocess_exec', side_effect=mock_adb_success):
            controller = InputController()
            await controller.swipe(
                mock_device_id,
                x1=100, y1=200,
                x2=300, y2=400,
                duration=300
            )

    @pytest.mark.asyncio
    async def test_swipe_default_duration(self, mock_device_id, mock_adb_success):
        """测试使用默认持续时间"""
        with patch('asyncio.create_subprocess_exec', side_effect=mock_adb_success):
            controller = InputController()

            # 不指定 duration，应该使用默认值 300ms
            await controller.swipe(
                mock_device_id,
                x1=100, y1=200,
                x2=300, y2=400
            )

    @pytest.mark.asyncio
    async def test_swipe_custom_duration(self, mock_device_id, mock_adb_success):
        """测试自定义持续时间"""
        with patch('asyncio.create_subprocess_exec', side_effect=mock_adb_success):
            controller = InputController()

            # 慢速滑动
            await controller.swipe(
                mock_device_id,
                x1=100, y1=200,
                x2=300, y2=400,
                duration=1000
            )

            # 快速滑动
            await controller.swipe(
                mock_device_id,
                x1=100, y1=200,
                x2=300, y2=400,
                duration=100
            )

    @pytest.mark.asyncio
    async def test_swipe_directions(self, mock_device_id, mock_adb_success):
        """测试不同方向的滑动"""
        with patch('asyncio.create_subprocess_exec', side_effect=mock_adb_success):
            controller = InputController()

            # 向上滑动
            await controller.swipe(mock_device_id, 540, 1500, 540, 500, 300)

            # 向下滑动
            await controller.swipe(mock_device_id, 540, 500, 540, 1500, 300)

            # 向左滑动
            await controller.swipe(mock_device_id, 900, 960, 180, 960, 300)

            # 向右滑动
            await controller.swipe(mock_device_id, 180, 960, 900, 960, 300)


# ---------------------------------------------------------------------------
# 按键命令测试
# ---------------------------------------------------------------------------

class TestKeyCommand:
    """按键命令测试"""

    @pytest.mark.asyncio
    async def test_key_home(self, mock_device_id, mock_adb_success):
        """测试 HOME 键"""
        with patch('asyncio.create_subprocess_exec', side_effect=mock_adb_success):
            controller = InputController()
            await controller.key(mock_device_id, KEYCODE_HOME)

    @pytest.mark.asyncio
    async def test_key_back(self, mock_device_id, mock_adb_success):
        """测试返回键"""
        with patch('asyncio.create_subprocess_exec', side_effect=mock_adb_success):
            controller = InputController()
            await controller.key(mock_device_id, KEYCODE_BACK)

    @pytest.mark.asyncio
    async def test_key_power(self, mock_device_id, mock_adb_success):
        """测试电源键"""
        with patch('asyncio.create_subprocess_exec', side_effect=mock_adb_success):
            controller = InputController()
            await controller.key(mock_device_id, KEYCODE_POWER)

    @pytest.mark.asyncio
    async def test_key_various_keycodes(self, mock_device_id, mock_adb_success):
        """测试各种 keycode"""
        with patch('asyncio.create_subprocess_exec', side_effect=mock_adb_success):
            controller = InputController()

            # 测试各种 keycode
            for keycode in [3, 4, 26, 82, 24, 25]:
                await controller.key(mock_device_id, keycode)


# ---------------------------------------------------------------------------
# 文本输入命令测试
# ---------------------------------------------------------------------------

class TestTextCommand:
    """文本输入命令测试"""

    @pytest.mark.asyncio
    async def test_text_simple(self, mock_device_id, mock_adb_success):
        """测试简单文本输入"""
        with patch('asyncio.create_subprocess_exec', side_effect=mock_adb_success):
            controller = InputController()
            await controller.text(mock_device_id, "hello")

    @pytest.mark.asyncio
    async def test_text_with_spaces(self, mock_device_id, mock_adb_success):
        """测试包含空格的文本输入"""
        with patch('asyncio.create_subprocess_exec', side_effect=mock_adb_success):
            controller = InputController()
            # 空格应该被转义为 %s
            await controller.text(mock_device_id, "hello world")

    @pytest.mark.asyncio
    async def test_text_with_special_chars(self, mock_device_id, mock_adb_success):
        """测试包含特殊字符的文本输入"""
        with patch('asyncio.create_subprocess_exec', side_effect=mock_adb_success):
            controller = InputController()
            # 特殊字符应该被转义
            await controller.text(mock_device_id, "hello&world<>|test")

    @pytest.mark.asyncio
    async def test_text_empty_string(self, mock_device_id, mock_adb_success):
        """测试空字符串输入"""
        with patch('asyncio.create_subprocess_exec', side_effect=mock_adb_success):
            controller = InputController()
            await controller.text(mock_device_id, "")

    @pytest.mark.asyncio
    async def test_text_numbers(self, mock_device_id, mock_adb_success):
        """测试数字输入"""
        with patch('asyncio.create_subprocess_exec', side_effect=mock_adb_success):
            controller = InputController()
            await controller.text(mock_device_id, "12345")


# ---------------------------------------------------------------------------
# 长按命令测试
# ---------------------------------------------------------------------------

class TestLongPressCommand:
    """长按命令测试"""

    @pytest.mark.asyncio
    async def test_long_press_basic(self, mock_device_id, mock_adb_success):
        """测试基本长按"""
        with patch('asyncio.create_subprocess_exec', side_effect=mock_adb_success):
            controller = InputController()
            await controller.long_press(mock_device_id, x=100, y=200, duration=1000)

    @pytest.mark.asyncio
    async def test_long_press_default_duration(self, mock_device_id, mock_adb_success):
        """测试使用默认持续时间"""
        with patch('asyncio.create_subprocess_exec', side_effect=mock_adb_success):
            controller = InputController()
            # 不指定 duration，应该使用默认值 1000ms
            await controller.long_press(mock_device_id, x=100, y=200)

    @pytest.mark.asyncio
    async def test_long_press_custom_duration(self, mock_device_id, mock_adb_success):
        """测试自定义持续时间"""
        with patch('asyncio.create_subprocess_exec', side_effect=mock_adb_success):
            controller = InputController()

            # 短按
            await controller.long_press(mock_device_id, x=100, y=200, duration=500)

            # 长按
            await controller.long_press(mock_device_id, x=100, y=200, duration=3000)


# ---------------------------------------------------------------------------
# 输入事件路由测试
# ---------------------------------------------------------------------------

class TestInputEventRouting:
    """输入事件路由测试"""

    @pytest.mark.asyncio
    async def test_route_touch_event(self, mock_device_id, mock_adb_success):
        """测试路由触摸事件"""
        with patch('asyncio.create_subprocess_exec', side_effect=mock_adb_success):
            controller = InputController()

            data = {
                "action": INPUT_ACTION_TOUCH,
                "x": 100,
                "y": 200,
            }

            await controller.handle_input_event(mock_device_id, data)

    @pytest.mark.asyncio
    async def test_route_swipe_event(self, mock_device_id, mock_adb_success):
        """测试路由滑动事件"""
        with patch('asyncio.create_subprocess_exec', side_effect=mock_adb_success):
            controller = InputController()

            data = {
                "action": INPUT_ACTION_SWIPE,
                "x1": 100, "y1": 200,
                "x2": 300, "y2": 400,
                "duration": 300,
            }

            await controller.handle_input_event(mock_device_id, data)

    @pytest.mark.asyncio
    async def test_route_key_event(self, mock_device_id, mock_adb_success):
        """测试路由按键事件"""
        with patch('asyncio.create_subprocess_exec', side_effect=mock_adb_success):
            controller = InputController()

            data = {
                "action": INPUT_ACTION_KEY,
                "keycode": KEYCODE_BACK,
            }

            await controller.handle_input_event(mock_device_id, data)

    @pytest.mark.asyncio
    async def test_route_text_event(self, mock_device_id, mock_adb_success):
        """测试路由文本输入事件"""
        with patch('asyncio.create_subprocess_exec', side_effect=mock_adb_success):
            controller = InputController()

            data = {
                "action": INPUT_ACTION_TEXT,
                "text": "hello",
            }

            await controller.handle_input_event(mock_device_id, data)

    @pytest.mark.asyncio
    async def test_route_long_press_event(self, mock_device_id, mock_adb_success):
        """测试路由长按事件"""
        with patch('asyncio.create_subprocess_exec', side_effect=mock_adb_success):
            controller = InputController()

            data = {
                "action": INPUT_ACTION_LONG_PRESS,
                "x": 100,
                "y": 200,
                "duration": 1000,
            }

            await controller.handle_input_event(mock_device_id, data)

    @pytest.mark.asyncio
    async def test_route_unknown_action(self, mock_device_id, mock_adb_success):
        """测试路由未知动作（应该抛出异常）"""
        with patch('asyncio.create_subprocess_exec', side_effect=mock_adb_success):
            controller = InputController()

            data = {
                "action": "unknown_action",
            }

            with pytest.raises(ValueError, match="Unknown input action"):
                await controller.handle_input_event(mock_device_id, data)


# ---------------------------------------------------------------------------
# 错误处理测试
# ---------------------------------------------------------------------------

class TestErrorHandling:
    """错误处理测试"""

    @pytest.mark.asyncio
    async def test_adb_command_failure(self, mock_device_id, mock_adb_failure):
        """测试 ADB 命令失败"""
        with patch('asyncio.create_subprocess_exec', side_effect=mock_adb_failure):
            controller = InputController()

            with pytest.raises(RuntimeError, match="ADB command failed"):
                await controller.tap(mock_device_id, x=100, y=200)

    @pytest.mark.asyncio
    async def test_adb_timeout(self, mock_device_id):
        """测试 ADB 命令超时"""
        async def timeout_mock(*args, **kwargs):
            import asyncio
            # 创建一个会超时的 mock 进程
            mock_proc = MagicMock()
            mock_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
            mock_proc.wait = AsyncMock(side_effect=asyncio.TimeoutError())
            return mock_proc

        with patch('asyncio.create_subprocess_exec', side_effect=timeout_mock):
            controller = InputController()

            with pytest.raises(RuntimeError, match="timed out"):
                await controller.tap(mock_device_id, x=100, y=200)

    @pytest.mark.asyncio
    async def test_invalid_device_id(self, mock_adb_success):
        """测试无效的设备 ID"""
        with patch('asyncio.create_subprocess_exec', side_effect=mock_adb_success):
            controller = InputController()

            # 无效设备 ID 应该导致 ADB 错误
            # 实际测试中应该验证错误处理
            pass  # 需要真实设备测试
