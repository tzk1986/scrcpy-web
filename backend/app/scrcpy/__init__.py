"""
Scrcpy 复用模块
================

提供 scrcpy 核心功能的封装，包括：
- ControlSender: 二进制控制消息发送器（scrcpy 协议）
- H264Parser: H264 帧解析器
- ServerManager: server 部署管理

注意：
    ScrcpyEncoder 位于 infrastructure 层（app.infrastructure.stream.scrcpy），
    避免循环导入，需要时直接从该路径导入。
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
from .control_sender import ControlSender
from .h264_parser import H264Parser
from .server_manager import ServerManager

__all__ = [
    # 控制器
    "ControlSender",

    # 解析器
    "H264Parser",

    # 管理器
    "ServerManager",

    # 配置
    "EncoderOpts",
    "DEFAULT_ENCODER_OPTS",
    "LOW_LATENCY_ENCODER_OPTS",
    "HIGH_QUALITY_ENCODER_OPTS",

    # 常量
    "KEYCODE_HOME",
    "KEYCODE_BACK",
    "KEYCODE_POWER",
    "KEYCODE_VOLUME_UP",
    "KEYCODE_VOLUME_DOWN",
]
