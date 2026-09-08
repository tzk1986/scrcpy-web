"""
领域端口 — 抽象接口
=====================

层：领域层（最内层）。

本模块定义了领域层对基础设施所需的 Protocol 类（抽象接口）。
这是依赖倒置原则的核心：

    - 领域层定义它所需要的（这些 Protocol）
    - 基础设施层提供如何实现（具体实现）
    - 应用层依赖这些 Protocol，而不是具体类

实现类（见 infrastructure/）：
    AdbDriver:         AdbCliDriver（基于子进程的 ADB CLI）
    VideoEncoder:      ScrcpyEncoder（scrcpy-server H.264）
    Transport:         WebSocketTransport, WebTransportTransport（占位符）
    DebugRepository:   SqliteDebugRepository（aiosqlite）
    DeviceRepository:  SqliteDeviceRepository（aiosqlite）

此处定义的数据类（跨层使用）：
    LogEntry:    解析后的 logcat 行（时间戳、级别、PID、TID、标签、消息）
    LogFilter:   日志检索的查询条件（级别、标签、PID、搜索）
    EncoderOpts: 视频编码器配置（max_size、bit_rate、codec、fps）
"""

from dataclasses import dataclass
from typing import AsyncIterator, Protocol, runtime_checkable

from app.domain.device import DeviceInfo
from app.domain.session import DebugSession


# ---------------------------------------------------------------------------
# 端口使用的数据传输对象
# ---------------------------------------------------------------------------

@dataclass
class LogEntry:
    """
    threadtime 格式解析后的 logcat 行。

    属性：
        ts:      Unix 时间戳（自 epoch 以来的秒数）。
        level:   单字符日志级别：V/D/I/W/E/F。
        pid:     进程 ID。
        tid:     线程 ID。
        tag:     日志标签（如 "ActivityManager"）。
        message: 日志消息正文。
        raw:     原始未修改的日志行。
    """
    ts: float
    level: str
    pid: int
    tid: int
    tag: str
    message: str
    raw: str


@dataclass
class LogFilter:
    """
    日志条目查询条件。

    所有字段都是可选的；None 表示"不按此字段过滤"。

    属性：
        level:  按日志级别过滤（V/D/I/W/E/F）。
        tag:    按标签子串匹配过滤。
        pid:    按精确 PID 过滤。
        search: 消息正文全文搜索。
        limit:  最大返回条目数（默认 1000）。
    """
    level: str | None = None
    tag: str | None = None
    pid: int | None = None
    search: str | None = None
    limit: int = 1000


@dataclass
class EncoderOpts:
    """
    视频编码器配置。

    这些选项传递给 VideoEncoder.start() 方法。

    属性：
        max_size: 最大帧尺寸（宽或高，取较大值）。
        bit_rate: 目标码率（如 "4M" = 4 Mbps）。
        codec:    视频编码名称（当前仅支持 "h264"）。
        fps:      目标帧率。
    """
    max_size: int = 1080
    bit_rate: str = "4M"
    codec: str = "h264"
    fps: int = 30


# ---------------------------------------------------------------------------
# Protocol 类（抽象接口）
# ---------------------------------------------------------------------------
# @runtime_checkable 允许使用 isinstance() 检查进行依赖验证。

@runtime_checkable
class AdbDriver(Protocol):
    """
    ADB 驱动抽象 — 所有设备通信都通过这里。

    实现类：
        - AdbCliDriver（infrastructure/adb/cli.py）：基于子进程
        - 未来：asyncadb、pure-python-adb、网络 ADB

    所有方法都是异步的，因为 ADB 操作涉及 I/O（USB/WiFi）。
    """

    async def list_devices(self) -> list[str]:
        """
        列出已连接设备的序列号。

        返回：
            ADB 序列字符串列表（如 ["emulator-5554", "192.168.1.5:5555"]）。
        """
        ...

    async def get_device_info(self, device_id: str) -> DeviceInfo:
        """
        查询设备属性并构建 DeviceInfo 快照。

        参数：
            device_id: ADB 序列号。

        返回：
            包含型号、OS 版本、分辨率、电量等的 DeviceInfo。

        异常：
            AdbError: ADB 命令失败时。
        """
        ...

    async def shell(self, device_id: str, cmd: str) -> str:
        """
        在设备上执行 shell 命令并返回 stdout。

        参数：
            device_id: ADB 序列号。
            cmd: 要执行的 shell 命令（如 "ls /sdcard"）。

        返回：
            命令的 stdout 字符串。

        异常：
            AdbError: 命令返回非零退出码时。
        """
        ...

    async def stream_logcat(self, device_id: str) -> AsyncIterator[str]:
        """
        逐行流式输出 logcat（无限生成器）。

        调用方负责取消迭代（如通过 asyncio.Task.cancel()）
        以停止日志收集。

        参数：
            device_id: ADB 序列号。

        产出：
            每次迭代产出一行 logcat（原始字符串，threadtime 格式）。
        """
        ...

    async def push(self, device_id: str, local: str, remote: str):
        """
        将文件从主机推送到设备。

        参数：
            device_id: ADB 序列号。
            local: 主机文件路径。
            remote: 设备文件路径。
        """
        ...

    async def pull(self, device_id: str, remote: str, local: str):
        """
        将文件从设备拉到主机。

        参数：
            device_id: ADB 序列号。
            remote: 设备文件路径。
            local: 主机文件路径。
        """
        ...

    async def install(self, device_id: str, apk_path: str):
        """
        在设备上安装 APK（如已存在则替换）。

        参数：
            device_id: ADB 序列号。
            apk_path: APK 文件的主机路径。

        异常：
            AdbError: 安装失败时（如 APK 不兼容）。
        """
        ...

    async def screenshot(self, device_id: str) -> bytes:
        """
        截图并返回 PNG 字节。

        参数：
            device_id: ADB 序列号。

        返回：
            PNG 图像数据。
        """
        ...


@runtime_checkable
class VideoEncoder(Protocol):
    """
    视频编码器抽象 — 将设备屏幕转换为 H.264 帧。

    实现类：
        - ScrcpyEncoder（infrastructure/stream/scrcpy.py）：scrcpy-server
        - 未来：MediaCodec（Android）、FFmpeg（软件编码）

    编码器作为长生命周期的异步生成器运行；产出的帧是原始 H.264 NAL 单元，
    前端通过 WebCodecs 解码。
    """

    async def start(self, device_id: str, opts: EncoderOpts) -> AsyncIterator[bytes]:
        """
        开始编码并产出 H.264 帧。

        参数：
            device_id: 要编码的设备的 ADB 序列号。
            opts: 编码器配置（分辨率、码率、编码格式、帧率）。

        产出：
            H.264 帧数据（原始字节，可能包含多个 NAL 单元）。
        """
        ...

    async def stop(self):
        """停止编码并释放资源（子进程等）。"""
        ...


@runtime_checkable
class Transport(Protocol):
    """
    传输层抽象 — 双向二进制帧传递。

    实现类：
        - WebSocketTransport（infrastructure/transport/websocket.py）
        - WebTransportTransport（infrastructure/transport/webtransport.py，占位符）

    用于视频流管道，将编码器与网络层解耦。
    """

    async def send(self, frame: bytes):
        """
        发送二进制帧给远端。

        参数：
            frame: 要发送的原始字节（H.264 数据等）。
        """
        ...

    async def receive(self) -> bytes:
        """
        从远端接收二进制帧。

        返回：
            接收到的原始字节。
        """
        ...

    async def close(self):
        """关闭传输连接。"""
        ...


@runtime_checkable
class DebugRepository(Protocol):
    """
    调试数据持久化抽象（会话、日志、shell 历史）。

    实现类：
        - SqliteDebugRepository（infrastructure/persistence/sqlite.py）
        - 未来：PostgreSQL、Redis（用于分布式部署）

    所有方法都是异步的，因为持久化涉及 I/O。
    """

    async def save_session(self, session: DebugSession):
        """
        保存或更新调试会话。

        参数：
            session: 要持久化的会话。
        """
        ...

    async def get_session(self, session_id: str) -> DebugSession | None:
        """
        按 ID 检索调试会话。

        参数：
            session_id: 唯一的会话标识符。

        返回：
            找到则返回 DebugSession，否则返回 None。
        """
        ...

    async def save_log(self, session_id: str, entry: LogEntry):
        """
        持久化单条日志条目。

        参数：
            session_id: 此日志所属的会话。
            entry: 解析后的日志条目。
        """
        ...

    async def query_logs(self, session_id: str, filter: LogFilter) -> list[LogEntry]:
        """
        带过滤的日志条目查询。

        参数：
            session_id: 要查询的会话。
            filter: 过滤条件（级别、标签、PID、搜索、限制）。

        返回：
            匹配的 LogEntry 对象列表，按时间戳排序（最旧在前）。
        """
        ...

    async def save_shell_history(self, session_id: str, command: str, output: str):
        """
        记录 shell 命令及其输出。

        参数：
            session_id: 执行命令的会话。
            command: shell 命令字符串。
            output: 命令的 stdout/stderr 输出。
        """
        ...

    async def get_shell_history(
        self, session_id: str, limit: int = 100
    ) -> list[tuple[str, str]]:
        """
        检索 shell 命令历史。

        参数：
            session_id: 要查询的会话。
            limit: 最大返回条目数。

        返回：
            (命令, 输出) 元组列表，最旧在前。
        """
        ...


@runtime_checkable
class DeviceRepository(Protocol):
    """
    设备持久化抽象。

    实现类：
        - SqliteDeviceRepository（infrastructure/persistence/sqlite.py）
        - 未来：PostgreSQL、内存（用于测试）
    """

    async def save(self, device: DeviceInfo):
        """
        保存或更新设备信息。

        参数：
            device: 要持久化的 DeviceInfo。
        """
        ...

    async def get(self, device_id: str) -> DeviceInfo | None:
        """
        按 ADB 序列号检索设备。

        参数：
            device_id: ADB 序列号。

        返回：
            找到则返回 DeviceInfo，否则返回 None。
        """
        ...

    async def list_all(self) -> list[DeviceInfo]:
        """
        列出所有已知设备。

        返回：
            仓库中所有 DeviceInfo 对象的列表。
        """
        ...

    async def delete(self, device_id: str):
        """
        从仓库中删除设备。

        参数：
            device_id: 要删除的设备的 ADB 序列号。
        """
        ...
