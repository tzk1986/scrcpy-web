"""
调试会话实体
=============

层：领域层。

表示绑定到特定设备和用户的持久化调试会话。

DebugSession 跟踪：
    - 日志缓冲区：最近 logcat 条目的内存环形缓冲区（最多
      DebugService 中定义的 MAX_LOG_BUFFER 条）。这支持快速过滤
      而无需访问数据库。
    - Shell 历史：在此会话中执行的 shell 命令的有序列表。
    - 活动时间戳：created_at 和 last_active 用于空闲检测。

会话 ID 格式："{device_id}_{user_id}_{unix_timestamp}"

每次交互（收到日志、执行 shell 命令）都应调用 `touch()` 方法
以保持会话"活跃"用于空闲超时检测。
"""

import time
from dataclasses import dataclass, field


@dataclass
class DebugSession:
    """带有日志缓冲区和 shell 历史的持久化调试会话。"""

    id: str
    device_id: str
    user_id: str
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    log_buffer: list[dict] = field(default_factory=list)
    shell_history: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    def touch(self):
        """标记会话为最近活跃（更新 last_active 时间戳）。"""
        self.last_active = time.time()

    @property
    def is_active(self) -> bool:
        """
        检查会话是否被认为活跃。

        如果会话在最近 5 分钟内被 touch 过，则认为是活跃的。
        UI 使用此阈值来显示/隐藏过期的会话。
        """
        return (time.time() - self.last_active) < 300
