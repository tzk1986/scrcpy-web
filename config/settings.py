"""
Configuration Settings

Loads configuration from:
1. config/base.yaml (defaults)
2. config/user.yaml (user overrides)
3. config/{env}.yaml (environment-specific)
4. Environment variables (highest priority)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppConfig(BaseSettings):
    name: str = "OpenScrcpy"
    version: str = "0.7.8"
    debug: bool = False
    log_level: str = "INFO"
    config_watch: bool = True  # 配置文件（config/*.yaml）热重载监听开关
    config_watch_interval: float = 2.0  # 热重载的 mtime 轮询间隔（秒）


class ServerConfig(BaseSettings):
    host: str = "0.0.0.0"
    port: int = 8765  # 后端 HTTP/WS 端口；可被 base.yaml server.port 或 BACKEND_PORT 环境变量覆盖
    workers: int = 4


def _is_writable(directory: Path) -> bool:
    """以创建-删除临时文件探测目录可写性；目录不存在同样视为不可写。"""
    try:
        probe = directory / f".write_probe_{os.getpid()}"
        probe.touch()
        probe.unlink()
        return True
    except OSError:
        return False


def _frozen_base_dir() -> Path | None:
    """冻结运行的基目录：exe 同级（可写时）；不可写（Program Files 等）回落
    %LOCALAPPDATA%/OpenScrcpy。源码运行返回 None。"""
    if not getattr(sys, "frozen", False):
        return None
    exe_dir = Path(sys.executable).parent
    if _is_writable(exe_dir):
        return exe_dir
    local_appdata = os.environ.get("LOCALAPPDATA")
    return Path(local_appdata) / "OpenScrcpy" if local_appdata else exe_dir


def app_base_dir() -> Path:
    """
    应用数据基目录：数据等相对路径的锚点。

    - 源码运行：仓库根（不随启动 cwd 漂移）
    - 冻结运行（PyInstaller）：exe 同级（绿色版可整体迁移/备份）；
      同级不可写（如 Program Files）时回落 %LOCALAPPDATA%/OpenScrcpy
    """
    frozen_dir = _frozen_base_dir()
    return frozen_dir if frozen_dir is not None else Path(__file__).parent.parent


def config_base_dir() -> Path:
    """
    配置文件搜索目录（load_yaml_config 与 config_watch 共用）。

    - 源码运行：仓库 config/
    - 冻结运行：exe 同级 config/ 优先（存在 base.yaml 才生效，用户可改
      并触发热重载），否则回落 bundle 内 config/（只读基线）
    """
    frozen_dir = _frozen_base_dir()
    if frozen_dir is not None:
        exe_config = frozen_dir / "config"
        if (exe_config / "base.yaml").exists():
            return exe_config
    return Path(__file__).parent


def _to_abs_path(value: str) -> str:
    """相对路径锚定 app_base_dir()，绝对路径原样返回。"""
    path = Path(value)
    if path.is_absolute():
        return value
    return str(app_base_dir() / path)


class DatabaseConfig(BaseSettings):
    # pydantic-settings 会把字段名作为 env 候选（populate_by_name 下必加），字段名
    # 恰为 path 时会误读系统 PATH；env_prefix 只作用于字段名候选（env_prefix_target
    # 默认 variable），使候选变为 [DB_PATH, OPENSCRCPY_PATH]，系统 PATH 不再命中
    path: str = Field(default="./data/debug.sqlite", validation_alias="DB_PATH", validate_default=True)
    echo: bool = False

    model_config = SettingsConfigDict(populate_by_name=True, env_prefix="OPENSCRCPY_")

    @field_validator("path")
    @classmethod
    def _normalize_path(cls, value: str) -> str:
        return _to_abs_path(value)


class AdbConfig(BaseSettings):
    adb_path: str = Field(default="", alias="ADB_PATH")  # 使用不同名字避免与 PATH 冲突
    timeout: int = 30
    probe_timeout: float = 1.5
    reconnect_interval: int = 5
    scan_concurrency: int = 254  # 网段扫描 TCP 探测并发数（方案 36）
    use_conpty: bool = True  # Windows 交互 shell 走 ConPTY 伪控制台，不可用时自动降级管道

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )

    @property
    def path(self) -> str:
        """获取 ADB 可执行文件路径（自动检测）"""
        if self.adb_path:
            return self.adb_path
        return self._detect_adb_path()

    def _detect_adb_path(self) -> str:
        """
        自动检测 ADB 可执行文件路径。

        优先级：
        1. 项目 tools 目录下的 adb.exe（Windows）或 adb（Unix）
        2. 系统 PATH 中的 adb
        """
        import sys

        # 获取项目根目录
        project_root = Path(__file__).parent.parent

        # 检查项目 tools 目录
        if sys.platform == "win32":
            local_adb = project_root / "tools" / "adb.exe"
        else:
            local_adb = project_root / "tools" / "adb"

        if local_adb.exists():
            return str(local_adb)

        # 回退到系统 PATH
        return "adb"


class StreamConfig(BaseSettings):
    max_size: int = 1080
    bit_rate: str = "4M"
    codec: Literal["h264", "h265"] = "h264"
    fps: int = 30
    # 自适应码率：scrcpy 协议无运行中改码率消息，通过按档重启编码器实现（每次切换约 1-3s 黑屏）
    adaptive_bitrate: bool = True
    bitrate_tiers: str = "8M,4M,2M,1M"  # 降序档位阶梯，起始档取不超过 bit_rate 的最大档
    # 视频流协议兜底开关：False（默认）走 12 字节包头协议（方案 17 实施项 1b）；
    # 真机验证失败时置 True 回退 raw_stream 裸流 + 启发式解析（实施项 1a/1a 尾步骤路径）
    raw_stream_fallback: bool = False
    # 空闲保活（方案 19 实施项 1a）：静止无帧超过 N 秒经控制 socket 发 RESET_VIDEO；0=关闭
    idle_reset_seconds: float = 5.0


class DebugConfig(BaseSettings):
    log_buffer_size: int = 50000
    session_ttl_days: int = 7
    log_retention_days: int = 7
    shell_history_days: int = 30
    max_db_size_mb: int = 1000  # 最大数据库大小（MB），超过时自动清理旧日志
    cleanup_interval_hours: int = 1  # 自动清理间隔（小时）
    min_log_level: str = "I"  # 最低日志级别：V/D/I/W/E/F（生产环境建议 W）
    log_rate_limit: int = 100  # 每秒最大日志数，超过时自动丢弃低级别日志
    db_pool_size: int = 5  # SQLite 连接池大小
    log_batch_size: int = 100  # 日志批量写入缓冲条数
    restore_max_sessions: int = 20  # 重启最多恢复的会话数（限制恢复时间）


class AlertRuleConfig(BaseSettings):
    """单条性能告警规则（方案 24 §3.2）。"""

    enabled: bool = True
    above: float | None = None  # 越大越坏判据（与 below 二选一，方向即判据）
    below: float | None = None  # 越小越坏判据
    consecutive: int = 3        # 连续越限 N 次触发（按有效判定样本计）
    clear_margin: float = 5.0   # 迟滞余量：解除需回落/回升越过该余量
    clear_consecutive: int = 3  # 连续安全 M 次解除
    cooldown: float = 60.0      # 通知冷却（秒）；冷却内触发进徽标但不弹 toast


class MetricAlertsConfig(BaseSettings):
    """各指标的告警规则集（metrics.thresholds，方案 24）。"""

    cpu_percent: AlertRuleConfig = Field(default_factory=lambda: AlertRuleConfig(above=80.0))
    memory_percent: AlertRuleConfig = Field(default_factory=lambda: AlertRuleConfig(above=90.0))
    fps: AlertRuleConfig = Field(default_factory=lambda: AlertRuleConfig(below=30.0))
    rx_rate_kbps: AlertRuleConfig = Field(
        default_factory=lambda: AlertRuleConfig(enabled=False, above=0.0)
    )
    tx_rate_kbps: AlertRuleConfig = Field(
        default_factory=lambda: AlertRuleConfig(enabled=False, above=0.0)
    )


class MetricsConfig(BaseSettings):
    buffer_size: int = 3600  # Perf 内存暂存条数（@1s 采样约 1 小时/设备）
    network_buffer_size: int = 1800  # Network 内存暂存条数（@2s 采样约 1 小时/设备）
    network_interval: float = 2.0  # Network 采样间隔（秒）
    idle_ttl_seconds: int = 300  # 无订阅者/访问且未录制时的空闲停采宽限（秒）
    lost_failures: int = 3  # 连续采样失败阈值 → 判定设备失联
    recording: bool = False  # 录制（落盘暂存）总开关，默认关闭
    retention_days: int = 3  # 缓存态保留期（天）
    max_rows_total: int = 500000  # 缓存态容量上限（Perf+Network 合计行数）
    thresholds: MetricAlertsConfig = Field(default_factory=MetricAlertsConfig)


class RecordingConfig(BaseSettings):
    """录屏配置节（方案 37 子项 B）。

    注意：dir 保持配置期相对路径原样（勿加路径归一 validator），
    运行态解析（锚定 app_base_dir()）属录制服务（B2）职责。
    """

    dir: str = "./data/recordings"  # 主机落盘目录（相对仓库根运行态解析）
    bit_rate: str = "4M"            # screenrecord 码率（默认）
    time_limit: int = 60            # 设备端 --time-limit 兜底（秒，前端倒计时同口径）
    retention_days: int = 3         # 双限-时间（天）
    max_total_mb: int = 512         # 双限-容量（目录总字节上限）
    degraded_size: str = "256x144"  # err=-38 降档分辨率


class SecurityConfig(BaseSettings):
    cors_origins: list[str] = ["http://localhost:8080"]


# 设备级覆盖的设备键白名单（方案 45 T8）：ADB 序列号 / ip:port / emulator-*。
# 写入侧（PUT settings）据此 422 拒绝坏键；读取侧（helpers）仅做 dict 查询不校验。
DeviceId = Annotated[
    str, StringConstraints(pattern=r"^[A-Za-z0-9._:-]{1,128}$")
]


class Settings(BaseSettings):
    app: AppConfig = Field(default_factory=AppConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    adb: AdbConfig = Field(default_factory=AdbConfig)
    stream: StreamConfig = Field(default_factory=StreamConfig)
    debug: DebugConfig = Field(default_factory=DebugConfig)
    metrics: MetricsConfig = Field(default_factory=MetricsConfig)
    recording: RecordingConfig = Field(default_factory=RecordingConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    # 设备级参数覆盖节（方案 45 T8）：<device_id> → stream/recording 可选覆盖。
    # user.yaml 层合并写入；流/录屏启动时经 device_*_settings helper 消费。
    devices: dict[DeviceId, DeviceOverride] = Field(default_factory=dict)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


def load_yaml_config(env: str = "base") -> dict[str, Any]:
    """Load YAML configuration file"""
    config_dir = config_base_dir()
    config_file = config_dir / f"{env}.yaml"

    if not config_file.exists():
        return {}

    with open(config_file, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep merge two dictionaries"""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def get_settings() -> Settings:
    """Get application settings"""
    import os

    # 载入项目根 .env（override=False：已存在的 OS 环境变量优先），
    # 使 BACKEND_HOST/BACKEND_PORT 无论来自 OS env 还是 .env 都能生效
    try:
        from dotenv import load_dotenv

        load_dotenv(Path(__file__).parent.parent / ".env")
    except Exception:
        pass

    env = os.getenv("APP_ENV", "base")
    config = load_yaml_config("base")

    # 用户覆盖层：config/user.yaml（不入仓，设置页写入点）；缺失/为空跳过。
    # 非 mapping（列表/标量损坏）视为失败抛 ValueError → reload_settings 保旧配置。
    user_config = load_yaml_config("user")
    if user_config:
        if not isinstance(user_config, dict):
            raise ValueError(
                "config/user.yaml must be a YAML mapping; keeping previous settings"
            )
        config = _deep_merge(config, user_config)

    if env != "base":
        env_config = load_yaml_config(env)
        config = _deep_merge(config, env_config)

    # 环境变量优先级最高：打包/部署时可据此把后端挪离默认端口，
    # 且 base.yaml 的 server 节点会作为 init kwargs 传入（压过 pydantic env 读取），
    # 故此处显式覆盖，确保 BACKEND_HOST/BACKEND_PORT 生效。
    server = config.setdefault("server", {})
    if os.getenv("BACKEND_HOST"):
        server["host"] = os.environ["BACKEND_HOST"]
    if os.getenv("BACKEND_PORT"):
        server["port"] = int(os.environ["BACKEND_PORT"])

    # 同上：yaml 的 database 节点作为 init kwargs 会压过 pydantic env 读取，
    # 故显式覆盖，使容器部署（docker-compose 的 DB_PATH）能真正生效
    if os.getenv("DB_PATH"):
        database = config.setdefault("database", {})
        database["path"] = os.environ["DB_PATH"]

    return Settings(**config)


_settings: Settings | None = None


def settings() -> Settings:
    """Get cached settings instance"""
    global _settings
    if _settings is None:
        _settings = get_settings()
    return _settings


def reload_settings() -> Settings:
    """
    重新加载配置并替换全局缓存单例（配置热重载入口）。

    重新执行 get_settings()（YAML / 环境变量全部重读），成功则替换缓存
    并返回新实例；解析失败时抛出异常，旧缓存保持不变（服务继续用
    旧配置运行）。base.yaml 缺失或内容为空同样视为失败：否则会静默回落
    到内置默认值，与「失败保留旧配置」的语义不符（空/清空/误删文件
    都可能触发大规模配置漂移）。仅对运行时读取 settings() 的消费点生效；
    数据库路径、连接池大小等构造时读取的资源不会热切换。
    """
    if not load_yaml_config("base"):
        raise ValueError("config/base.yaml missing or empty; keeping previous settings")

    global _settings
    _settings = get_settings()
    return _settings


class StreamOverride(BaseModel):
    """stream 节写入白名单（方案 45 T2；T8 设备级覆盖复用）。"""

    model_config = ConfigDict(extra="forbid")

    max_size: int | None = Field(default=None, ge=0, le=4096)
    bit_rate: str | None = Field(default=None, pattern=r"^\d+[MK]$")
    fps: int | None = Field(default=None, ge=1, le=120)
    codec: Literal["h264", "h265"] | None = None
    adaptive_bitrate: bool | None = None


class RecordingOverride(BaseModel):
    """recording 节写入白名单（方案 45 T2；T8 设备级覆盖复用）。"""

    model_config = ConfigDict(extra="forbid")

    bit_rate: str | None = Field(default=None, pattern=r"^\d+[MK]$")
    time_limit: int | None = Field(default=None, ge=1, le=1800)


class DeviceOverride(BaseModel):
    """单设备覆盖节（stream/recording 各可选；节为 None 表示不覆盖该侧）。"""

    model_config = ConfigDict(extra="forbid")

    stream: StreamOverride | None = None
    recording: RecordingOverride | None = None


def device_stream_settings(device_id: str) -> StreamConfig:
    """设备级 stream 合并视图：覆盖节内非空字段深优于全局，未覆盖键回落全局。"""
    base = settings().stream
    dev = settings().devices.get(device_id)
    if dev is None or dev.stream is None:
        return base
    patch = dev.stream.model_dump(exclude_none=True)
    return base.model_copy(update=patch) if patch else base


def device_recording_settings(device_id: str) -> RecordingConfig:
    """设备级 recording 合并视图（同 device_stream_settings 语义）。"""
    base = settings().recording
    dev = settings().devices.get(device_id)
    if dev is None or dev.recording is None:
        return base
    patch = dev.recording.model_dump(exclude_none=True)
    return base.model_copy(update=patch) if patch else base
