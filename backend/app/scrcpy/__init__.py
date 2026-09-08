"""
Scrcpy 复用模块
================

提供 scrcpy 核心功能的封装，包括：
- ScrcpyEncoder: 视频编码器
- H264Parser: H264 帧解析器
- InputController: 输入控制器
- ServerManager: server 部署管理
"""

from .constants import (
    EncoderOpts,
    DEFAULT_ENCODER_OPTS,
    LOW_LATENCY_ENCODER_OPTS,
    HIGH_QUALITY_ENCODER_OPTS,
    KEYCODE_HOME,
    KEYCODE_BACK,
    KEYCODE_POWER,
    KEYCODE_VOLUME_UP,
    KEYCODE_VOLUME_DOWN,
)
from .encoder import ScrcpyEncoder
from .h264_parser import H264Parser
from .input_controller import InputController
from .server_manager import ServerManager

__all__ = [
    # 编码器
    "ScrcpyEncoder",
    "EncoderOpts",
    "DEFAULT_ENCODER_OPTS",
    "LOW_LATENCY_ENCODER_OPTS",
    "HIGH_QUALITY_ENCODER_OPTS",

    # 解析器
    "H264Parser",

    # 控制器
    "InputController",
    "ServerManager",

    # 常量
    "KEYCODE_HOME",
    "KEYCODE_BACK",
    "KEYCODE_POWER",
    "KEYCODE_VOLUME_UP",
    "KEYCODE_VOLUME_DOWN",
]
