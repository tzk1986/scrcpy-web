"""
Scrcpy 常量定义
================

定义 Android keycode、编码器默认参数、NALU 类型等常量。
"""

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Android Keycode（常用）
# ---------------------------------------------------------------------------
# 完整列表：https://developer.android.com/reference/android/view/KeyEvent

KEYCODE_UNKNOWN = 0
KEYCODE_HOME = 3              # HOME 键
KEYCODE_BACK = 4              # 返回键
KEYCODE_CALL = 5              # 拨号键
KEYCODE_ENDCALL = 6           # 挂断键
KEYCODE_VOLUME_UP = 24        # 音量+
KEYCODE_VOLUME_DOWN = 25      # 音量-
KEYCODE_POWER = 26            # 电源键
KEYCODE_CAMERA = 27           # 相机键
KEYCODE_MENU = 82             # 菜单键
KEYCODE_SEARCH = 84           # 搜索键
KEYCODE_ENTER = 66            # 回车键
KEYCODE_DEL = 67              # 退格键
KEYCODE_SPACE = 62            # 空格键
KEYCODE_TAB = 61              # Tab 键
KEYCODE_ESCAPE = 111          # Esc 键

# 导航键
KEYCODE_DPAD_UP = 19          # 方向键上
KEYCODE_DPAD_DOWN = 20        # 方向键下
KEYCODE_DPAD_LEFT = 21        # 方向键左
KEYCODE_DPAD_RIGHT = 22       # 方向键右
KEYCODE_DPAD_CENTER = 23      # 方向键确认

# 数字键
KEYCODE_0 = 7
KEYCODE_1 = 8
KEYCODE_2 = 9
KEYCODE_3 = 10
KEYCODE_4 = 11
KEYCODE_5 = 12
KEYCODE_6 = 13
KEYCODE_7 = 14
KEYCODE_8 = 15
KEYCODE_9 = 16

# 字母键
KEYCODE_A = 29
KEYCODE_B = 30
KEYCODE_C = 31
KEYCODE_D = 32
KEYCODE_E = 33
KEYCODE_F = 34
KEYCODE_G = 35
KEYCODE_H = 36
KEYCODE_I = 37
KEYCODE_J = 38
KEYCODE_K = 39
KEYCODE_L = 40
KEYCODE_M = 41
KEYCODE_N = 42
KEYCODE_O = 43
KEYCODE_P = 44
KEYCODE_Q = 45
KEYCODE_R = 46
KEYCODE_S = 47
KEYCODE_T = 48
KEYCODE_U = 49
KEYCODE_V = 50
KEYCODE_W = 51
KEYCODE_X = 52
KEYCODE_Y = 53
KEYCODE_Z = 54

# 媒体控制键
KEYCODE_MEDIA_PLAY = 126
KEYCODE_MEDIA_PAUSE = 127
KEYCODE_MEDIA_PLAY_PAUSE = 85
KEYCODE_MEDIA_STOP = 86
KEYCODE_MEDIA_NEXT = 87
KEYCODE_MEDIA_PREVIOUS = 88

# ---------------------------------------------------------------------------
# H264 NALU 类型
# ---------------------------------------------------------------------------
# NALU 头部第 1 字节的低 5 位

NALU_TYPE_UNSPECIFIED = 0     # 未指定
NALU_TYPE_SLICE = 1           # 非 IDR 切片（P/B 帧）
NALU_TYPE_SLICE_DPA = 2       # 切片数据分区 A
NALU_TYPE_SLICE_DPB = 3       # 切片数据分区 B
NALU_TYPE_SLICE_DPC = 4       # 切片数据分区 C
NALU_TYPE_IDR = 5             # IDR 关键帧
NALU_TYPE_SEI = 6             # 补充增强信息
NALU_TYPE_SPS = 7             # 序列参数集
NALU_TYPE_PPS = 8             # 图像参数集
NALU_TYPE_AUD = 9             # 访问单元分隔符
NALU_TYPE_END_SEQUENCE = 10   # 序列结束
NALU_TYPE_END_STREAM = 11     # 流结束

# ---------------------------------------------------------------------------
# 编码器默认参数
# ---------------------------------------------------------------------------

@dataclass
class EncoderOpts:
    """
    编码器配置选项。

    属性：
        max_size: 最大帧尺寸（宽或高，取较大值）。
        bit_rate: 目标码率（如 "4M" = 4 Mbps）。
        codec: 视频编码名称（目前仅支持 "h264"）。
        fps: 目标帧率。
    """
    max_size: int = 1080
    bit_rate: str = "4M"
    codec: str = "h264"
    fps: int = 30


# 预设配置
DEFAULT_ENCODER_OPTS = EncoderOpts()                    # 1080p, 4Mbps, 30fps
LOW_LATENCY_ENCODER_OPTS = EncoderOpts(
    max_size=720,
    bit_rate="2M",
    fps=30,
)                                                       # 720p, 2Mbps, 30fps
HIGH_QUALITY_ENCODER_OPTS = EncoderOpts(
    max_size=1080,
    bit_rate="8M",
    fps=60,
)                                                       # 1080p, 8Mbps, 60fps

# ---------------------------------------------------------------------------
# scrcpy-server 配置
# ---------------------------------------------------------------------------

SCRCPY_SERVER_VERSION = "4.1"                           # scrcpy-server 版本（使用 v4.1 协议）
SCRCPY_SERVER_JAR_NAME = "scrcpy-server.jar"            # server JAR 文件名
SCRCPY_SERVER_REMOTE_PATH = "/data/local/tmp/scrcpy-server.jar"  # 设备上的路径

# scrcpy-server 启动参数（固定值）
SCRCPY_SERVER_CLASS = "com.genymobile.scrcpy.Server"    # server 主类

# scrcpy-server socket 名称（v4.1 使用 scid 格式化）
# scid 为 31 位随机数，格式化为 8 位十六进制零填充（%08x）
# 参考 scrcpy 源码：app/src/server.c 中的 "scrcpy_%08x"
SCRCPY_SOCKET_NAME_TEMPLATE = "scrcpy_{:08x}"           # socket 名称模板（十六进制）

# ---------------------------------------------------------------------------
# ADB 配置
# ---------------------------------------------------------------------------

ADB_READ_BLOCK_SIZE = 65536                             # 读取块大小（64KB）
ADB_TIMEOUT_SECONDS = 10                                # ADB 命令超时（秒）

# ---------------------------------------------------------------------------
# 输入事件类型（WebSocket JSON 协议）
# ---------------------------------------------------------------------------

INPUT_ACTION_TOUCH = "touch"                            # 触摸点击
INPUT_ACTION_SWIPE = "swipe"                            # 滑动
INPUT_ACTION_KEY = "key"                                # 按键
INPUT_ACTION_TEXT = "text"                              # 文本输入
INPUT_ACTION_LONG_PRESS = "long_press"                  # 长按

# ---------------------------------------------------------------------------
# 视频流协议
# ---------------------------------------------------------------------------

VIDEO_STREAM_FRAME_SIZE = 65536                         # 帧读取块大小（64KB）
VIDEO_STREAM_QUEUE_SIZE = 10                            # 订阅队列大小
