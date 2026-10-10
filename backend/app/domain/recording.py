"""
录屏会话实体
=============

层：领域层。

表示一次设备端 screenrecord 录屏会话（方案 37 子项 B）。

RecordingSession 跟踪：
    - 生命周期状态：recording → stopping → pulling → done / failed
    - 设备端产物路径：remote_path（由录制服务 pull 至主机后清理）

消费方：B2 录制服务、B3 screenrecord 编排、B4 降档重试。
"""

import time
from dataclasses import dataclass, field
from enum import Enum


class RecordingState(str, Enum):
    """录屏会话状态机（py3.10 兼容的 str 枚举）。"""

    RECORDING = "recording"
    STOPPING = "stopping"
    PULLING = "pulling"
    DONE = "done"
    FAILED = "failed"


@dataclass
class RecordingSession:
    """设备端录屏会话：从启动录制到产物拉回主机。"""

    id: str
    device_id: str
    started_at: float = field(default_factory=time.time)
    state: str = RecordingState.RECORDING.value
    remote_path: str = ""
