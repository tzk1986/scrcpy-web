"""
常量定义测试
=============

测试 scrcpy 模块的常量定义。

测试内容：
    - Android keycode 值正确性
    - 编码器配置默认值
    - NALU 类型常量
    - scrcpy-server 配置
    - ADB 配置参数
"""

import pytest

from app.scrcpy.constants import (
    # Keycode
    KEYCODE_HOME,
    KEYCODE_BACK,
    KEYCODE_POWER,
    KEYCODE_VOLUME_UP,
    KEYCODE_VOLUME_DOWN,
    KEYCODE_MENU,
    KEYCODE_ENTER,

    # NALU 类型
    NALU_TYPE_IDR,
    NALU_TYPE_SPS,
    NALU_TYPE_PPS,
    NALU_TYPE_SLICE,

    # 编码器配置
    EncoderOpts,
    DEFAULT_ENCODER_OPTS,
    LOW_LATENCY_ENCODER_OPTS,
    HIGH_QUALITY_ENCODER_OPTS,

    # scrcpy-server 配置
    SCRCPY_SERVER_VERSION,
    SCRCPY_SERVER_JAR_NAME,
    SCRCPY_SERVER_REMOTE_PATH,
    SCRCPY_SERVER_CLASS,

    # ADB 配置
    ADB_READ_BLOCK_SIZE,
    ADB_TIMEOUT_SECONDS,

    # 输入事件类型
    INPUT_ACTION_TOUCH,
    INPUT_ACTION_SWIPE,
    INPUT_ACTION_KEY,
    INPUT_ACTION_TEXT,
    INPUT_ACTION_LONG_PRESS,
)


# ---------------------------------------------------------------------------
# Keycode 测试
# ---------------------------------------------------------------------------

class TestKeycodes:
    """Android keycode 测试"""

    def test_home_keycode(self):
        """测试 HOME 键 keycode 值"""
        assert KEYCODE_HOME == 3

    def test_back_keycode(self):
        """测试返回键 keycode 值"""
        assert KEYCODE_BACK == 4

    def test_power_keycode(self):
        """测试电源键 keycode 值"""
        assert KEYCODE_POWER == 26

    def test_volume_up_keycode(self):
        """测试音量加键 keycode 值"""
        assert KEYCODE_VOLUME_UP == 24

    def test_volume_down_keycode(self):
        """测试音量减键 keycode 值"""
        assert KEYCODE_VOLUME_DOWN == 25

    def test_menu_keycode(self):
        """测试菜单键 keycode 值"""
        assert KEYCODE_MENU == 82

    def test_enter_keycode(self):
        """测试回车键 keycode 值"""
        assert KEYCODE_ENTER == 66


# ---------------------------------------------------------------------------
# NALU 类型测试
# ---------------------------------------------------------------------------

class TestNaluTypes:
    """H264 NALU 类型测试"""

    def test_idr_type(self):
        """测试 IDR 关键帧类型值"""
        assert NALU_TYPE_IDR == 5

    def test_sps_type(self):
        """测试 SPS 类型值"""
        assert NALU_TYPE_SPS == 7

    def test_pps_type(self):
        """测试 PPS 类型值"""
        assert NALU_TYPE_PPS == 8

    def test_slice_type(self):
        """测试切片类型值"""
        assert NALU_TYPE_SLICE == 1


# ---------------------------------------------------------------------------
# 编码器配置测试
# ---------------------------------------------------------------------------

class TestEncoderOpts:
    """编码器配置选项测试"""

    def test_default_values(self):
        """测试默认编码器配置"""
        opts = EncoderOpts()
        assert opts.max_size == 1080
        assert opts.bit_rate == "4M"
        assert opts.codec == "h264"
        assert opts.fps == 30

    def test_custom_values(self):
        """测试自定义编码器配置"""
        opts = EncoderOpts(
            max_size=720,
            bit_rate="2M",
            codec="h264",
            fps=60,
        )
        assert opts.max_size == 720
        assert opts.bit_rate == "2M"
        assert opts.codec == "h264"
        assert opts.fps == 60

    def test_default_encoder_opts(self):
        """测试预定义默认配置"""
        assert DEFAULT_ENCODER_OPTS.max_size == 1080
        assert DEFAULT_ENCODER_OPTS.bit_rate == "4M"
        assert DEFAULT_ENCODER_OPTS.fps == 30

    def test_low_latency_encoder_opts(self):
        """测试预定义低延迟配置"""
        assert LOW_LATENCY_ENCODER_OPTS.max_size == 720
        assert LOW_LATENCY_ENCODER_OPTS.bit_rate == "2M"
        assert LOW_LATENCY_ENCODER_OPTS.fps == 30

    def test_high_quality_encoder_opts(self):
        """测试预定义高质量配置"""
        assert HIGH_QUALITY_ENCODER_OPTS.max_size == 1080
        assert HIGH_QUALITY_ENCODER_OPTS.bit_rate == "8M"
        assert HIGH_QUALITY_ENCODER_OPTS.fps == 60

    def test_encoder_opts_immutability(self):
        """测试编码器配置对象的独立性"""
        opts1 = EncoderOpts()
        opts2 = EncoderOpts()
        opts1.max_size = 480

        # 修改 opts1 不应影响 opts2
        assert opts2.max_size == 1080


# ---------------------------------------------------------------------------
# scrcpy-server 配置测试
# ---------------------------------------------------------------------------

class TestScrcpyServerConfig:
    """scrcpy-server 配置测试"""

    def test_server_version(self):
        """测试 server 版本号"""
        assert SCRCPY_SERVER_VERSION == "2.4"

    def test_server_jar_name(self):
        """测试 server JAR 文件名"""
        assert SCRCPY_SERVER_JAR_NAME == "scrcpy-server.jar"

    def test_server_remote_path(self):
        """测试 server 在设备上的路径"""
        assert SCRCPY_SERVER_REMOTE_PATH == "/data/local/tmp/scrcpy-server.jar"

    def test_server_class(self):
        """测试 server 主类名"""
        assert SCRCPY_SERVER_CLASS == "com.genymobile.scrcpy.Server"


# ---------------------------------------------------------------------------
# ADB 配置测试
# ---------------------------------------------------------------------------

class TestAdbConfig:
    """ADB 配置测试"""

    def test_read_block_size(self):
        """测试读取块大小"""
        assert ADB_READ_BLOCK_SIZE == 65536
        assert ADB_READ_BLOCK_SIZE == 64 * 1024  # 64KB

    def test_timeout_seconds(self):
        """测试 ADB 命令超时时间"""
        assert ADB_TIMEOUT_SECONDS == 10
        assert ADB_TIMEOUT_SECONDS > 0


# ---------------------------------------------------------------------------
# 输入事件类型测试
# ---------------------------------------------------------------------------

class TestInputActionTypes:
    """输入事件类型测试"""

    def test_touch_action(self):
        """测试触摸事件类型"""
        assert INPUT_ACTION_TOUCH == "touch"

    def test_swipe_action(self):
        """测试滑动事件类型"""
        assert INPUT_ACTION_SWIPE == "swipe"

    def test_key_action(self):
        """测试按键事件类型"""
        assert INPUT_ACTION_KEY == "key"

    def test_text_action(self):
        """测试文本输入事件类型"""
        assert INPUT_ACTION_TEXT == "text"

    def test_long_press_action(self):
        """测试长按事件类型"""
        assert INPUT_ACTION_LONG_PRESS == "long_press"
