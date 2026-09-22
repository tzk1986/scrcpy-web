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
    DebugRepository:   SqliteDebugRepository（aiosqlite）
    DeviceRepository:  SqliteDeviceRepository（aiosqlite）

此处定义的数据类（跨层使用）：
    LogEntry:    解析后的 logcat 行（时间戳、级别、PID、TID、标签、消息）
    LogFilter:   日志检索的查询条件（级别、标签、PID、搜索）
    EncoderOpts: 视频编码器配置（max_size、bit_rate、codec、fps）
"""

from dataclasses import dataclass
from typing import Any, AsyncGenerator, AsyncIterator, Callable, Protocol, runtime_checkable

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
class ShellSession(Protocol):
    """
    交互式 Shell 会话抽象 — 持久化 PTY 终端会话。

    实现类：
        - InteractiveShell（infrastructure/adb/shell.py）：基于 `adb shell -tt`

    与单次 shell 命令不同，ShellSession 保持会话状态（如 cd、su），
    支持完整终端功能（Ctrl+C、Tab、↑↓ 历史、readline）。
    """

    @property
    def is_alive(self) -> bool:
        """
        检查 shell 进程是否存活。

        返回：
            True 表示进程运行中，False 表示已退出。
        """
        ...

    async def start(
        self,
        device_id: str,
        initial_output_callback: Callable[[str], None] | None = None,
    ) -> None:
        """
        启动交互式 shell 会话。

        参数：
            device_id: ADB 序列号。
            initial_output_callback: 可选回调，接收初始输出（用于显示 prompt）。

        异常：
            AdbError: 启动失败时。
        """
        ...

    def execute(self, cmd: str) -> AsyncGenerator[str, None]:
        """
        执行命令并流式返回输出（用于 HTTP API 降级）。

        返回异步生成器（而非仅 AsyncIterator）：调用方可在消费者中断时
        `aclose()` 显式收尾（释放会话锁、复位执行状态），不必等 GC。

        参数：
            cmd: 要执行的 shell 命令。

        产出：
            每行输出（UTF-8 字符串）。

        异常：
            ShellExitedError: 进程意外退出时。
        """
        ...

    async def send_input(self, data: bytes) -> None:
        """
        发送原始输入（按键）到 shell（用于 WebSocket 透传）。

        参数：
            data: 原始字节数据（如按键序列）。

        异常：
            ShellExitedError: 进程已退出时。
        """
        ...

    async def get_output(self) -> str | None:
        """
        从输出队列获取一行输出（阻塞）。

        用于 PTY 模式的输出转发。当 shell 退出时返回 None。

        返回：
            一行输出（UTF-8 字符串），或 None（EOF）。
        """
        ...

    def get_initial_output(self) -> str:
        """
        获取初始输出（包括设备 prompt）。

        返回：
            初始输出字符串。
        """
        ...

    async def stop(self) -> None:
        """
        安全关闭 shell 进程。

        先关闭 stdin，等待进程自然退出，超时则强制 kill。
        """
        ...


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

    def shell_stream(self, device_id: str, cmd: str) -> AsyncIterator[str]:
        """
        流式执行 shell 命令，逐行产出输出。

        适用于长时间运行的命令（如 logcat -d、find、grep -r），
        允许调用方在命令仍在执行时处理输出行。

        参数：
            device_id: ADB 序列号。
            cmd: 要执行的 shell 命令。

        产出：
            每次迭代产出一行输出（UTF-8 字符串）。

        异常：
            AdbError: 命令启动失败时。
        """
        ...

    def stream_logcat(self, device_id: str) -> AsyncIterator[str]:
        """
        逐行流式输出 logcat（无限生成器）。

        语义：从调用时刻起收（设备端以当前时刻为时间下界），
        不回放设备端现存日志缓冲——重启采集不会重复推送旧日志。

        调用方负责取消迭代（如通过 asyncio.Task.cancel()）
        以停止日志收集。

        参数：
            device_id: ADB 序列号。

        产出：
            每次迭代产出一行 logcat（原始字符串，threadtime 格式）。
        """
        ...

    async def push(self, device_id: str, local: str, remote: str) -> None:
        """
        将文件从主机推送到设备。

        参数：
            device_id: ADB 序列号。
            local: 主机文件路径。
            remote: 设备文件路径。
        """
        ...

    async def pull(self, device_id: str, remote: str, local: str) -> None:
        """
        将文件从设备拉到主机。

        参数：
            device_id: ADB 序列号。
            remote: 设备文件路径。
            local: 主机文件路径。
        """
        ...

    async def install(self, device_id: str, apk_path: str) -> None:
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

    async def connect_tcp(self, ip: str, port: int = 5555) -> str:
        """
        通过 TCP/IP 连接设备，返回 "ip:port" 形式的设备 ID。

        异常：
            AdbError: 连接失败时。
        """
        ...

    async def disconnect_tcp(self, ip: str, port: int = 5555) -> None:
        """断开 TCP/IP 连接（失败仅记警告，不抛出）。"""
        ...

    async def create_shell(self, device_id: str) -> "ShellSession":
        """
        创建交互式 shell 会话（PTY 模式）。

        参数：
            device_id: ADB 序列号。

        返回：
            ShellSession 实例（已启动）。

        异常：
            AdbError: 启动失败时。
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

    async def stop(self) -> None:
        """停止编码并释放资源（子进程等）。"""
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

    async def save_session(self, session: DebugSession) -> None:
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

    async def list_recent_sessions(self, since_ts: float, limit: int = 20) -> list[DebugSession]:
        """
        列出 last_active >= since_ts 的会话（重启恢复用），按最近活跃优先。
        """
        ...

    async def save_log(self, session_id: str, entry: LogEntry, seq: int = 0) -> None:
        """
        持久化单条日志条目。

        参数：
            session_id: 此日志所属的会话。
            entry: 解析后的日志条目。
            seq: 会话内单调递增序列号（断线续传游标）。
        """
        ...

    async def save_logs_bulk(self, rows: list[tuple[Any, ...]]) -> None:
        """
        批量插入日志行（单事务）。rows 元素为 9 元组
        (session_id, ts, level, pid, tid, tag, message, raw, seq)。
        """
        ...

    async def next_seq(self, session_id: str) -> int:
        """
        返回该会话下一条日志应使用的 seq（max(seq)+1，无记录为 0）。
        """
        ...

    async def query_logs_since(self, session_id: str, from_seq: int, limit: int = 1000) -> list[dict[str, Any]]:
        """
        按 seq 增量查询（断线续传补发），返回 dict 列表（含 seq），seq 升序。
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

    async def save_shell_history(self, session_id: str, command: str, output: str) -> None:
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

    async def delete_old_logs(self, retention_seconds: float) -> int:
        """
        删除超过保留期的日志。

        参数：
            retention_seconds: 保留时间（秒）。

        返回：
            删除的日志条数。
        """
        ...

    async def delete_old_shell_history(self, retention_seconds: float) -> int:
        """
        删除超过保留期的 shell 历史。

        参数：
            retention_seconds: 保留时间（秒）。

        返回：
            删除的记录数。
        """
        ...

    async def delete_session_logs(self, session_id: str) -> int:
        """
        删除指定会话的所有日志和 shell 历史。

        参数：
            session_id: 要清理的会话 ID。

        返回：
            删除的日志条数。
        """
        ...

    async def get_db_size_bytes(self) -> int:
        """
        获取数据库文件大小（字节）。

        返回：
            文件大小（字节）。
        """
        ...

    async def trim_to_db_size(self, max_size_bytes: int) -> int:
        """
        库级字节兜底：当数据库超过指定大小时按顺序删除最旧数据——
        指标 → shell 历史 → 日志（方案 18 D5：先删可再生采样，
        日志是不可再生的主业务数据，最后删）。

        参数：
            max_size_bytes: 最大数据库大小（字节）。

        返回：
            删除的总条数（跨表）。
        """
        ...

    async def vacuum(self) -> None:
        """
        执行 SQLite VACUUM 回收未使用空间。
        """
        ...


@runtime_checkable
class MetricsRepository(Protocol):
    """
    指标采样数据持久化抽象（缓存态，方案 18）。

    实现类：
        - SqliteMetricsRepository（infrastructure/persistence/sqlite.py）

    数据仅在「录制」开启时落盘；不录制时指标表零写入。
    清理（时间 + 容量双限）与导出查询都走本接口。
    """

    async def save_perf_samples_bulk(self, rows: list[tuple[Any, ...]]) -> None:
        """
        批量插入性能采样行（单事务）。rows 元素为 9 元组
        (device_id, ts, cpu_percent, total_memory_mb, used_memory_mb,
         fps, jank_count, current_activity, top_package)。
        """
        ...

    async def save_network_samples_bulk(self, rows: list[tuple[Any, ...]]) -> None:
        """
        批量插入网络采样行（单事务）。rows 元素为 9 元组
        (device_id, ts, rx_bytes, tx_bytes, rx_rate_kbps, tx_rate_kbps,
         active_connections, wifi_connected, wifi_ssid)。
        """
        ...

    async def query_perf_samples(
        self,
        device_id: str,
        from_ts: float | None = None,
        to_ts: float | None = None,
        limit: int = 50000,
    ) -> list[dict[str, Any]]:
        """
        按设备与 ts 区间查询性能采样（ts 升序，最多 limit 条）。

        参数：
            device_id: 设备 ID。
            from_ts: 区间下界（含），None 表示无下界。
            to_ts: 区间上界（含），None 表示无上界。
            limit: 最大返回条数。

        返回：
            行字典列表（键为列名）。
        """
        ...

    async def query_network_samples(
        self,
        device_id: str,
        from_ts: float | None = None,
        to_ts: float | None = None,
        limit: int = 50000,
    ) -> list[dict[str, Any]]:
        """
        按设备与 ts 区间查询网络采样（ts 升序，最多 limit 条）。

        参数：
            device_id: 设备 ID。
            from_ts: 区间下界（含），None 表示无下界。
            to_ts: 区间上界（含），None 表示无上界。
            limit: 最大返回条数。

        返回：
            行字典列表（键为列名）。
        """
        ...

    async def perf_sample_stats(
        self, device_id: str
    ) -> tuple[int, float | None, float | None]:
        """
        该设备性能采样的聚合统计（跨批次叠加）。

        返回：
            (rows, oldest_ts, newest_ts)；无数据时 (0, None, None)。
        """
        ...

    async def network_sample_stats(
        self, device_id: str
    ) -> tuple[int, float | None, float | None]:
        """
        该设备网络采样的聚合统计（跨批次叠加）。

        返回：
            (rows, oldest_ts, newest_ts)；无数据时 (0, None, None)。
        """
        ...

    async def delete_old_metrics(self, retention_seconds: float) -> int:
        """
        删除超过保留期的指标（两表，按 ts 删最旧）。

        参数：
            retention_seconds: 保留时间（秒）。

        返回：
            删除的总行数（两表之和）。
        """
        ...

    async def trim_metrics_rows(self, max_rows: int) -> int:
        """
        两表合计行数超过 max_rows 时按 ts 删最旧，直到合计不超限。

        参数：
            max_rows: 两表合计行数上限。

        返回：
            删除的总行数。
        """
        ...

    async def count_metrics_rows(self) -> int:
        """
        返回两表行数之和。
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

    async def save(self, device: DeviceInfo) -> None:
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

    async def delete(self, device_id: str) -> None:
        """
        从仓库中删除设备。

        参数：
            device_id: 要删除的设备的 ADB 序列号。
        """
        ...
