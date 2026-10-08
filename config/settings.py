"""
Configuration Settings

Loads configuration from:
1. config/base.yaml (defaults)
2. config/{env}.yaml (environment-specific)
3. Environment variables (highest priority)
"""

import os
import sys
from pathlib import Path
from typing import Any

import yaml
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppConfig(BaseSettings):
    name: str = "OpenScrcpy"
    version: str = "0.7.1"
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
    codec: str = "h264"
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


class SecurityConfig(BaseSettings):
    cors_origins: list[str] = ["http://localhost:8080"]


class Settings(BaseSettings):
    app: AppConfig = Field(default_factory=AppConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    adb: AdbConfig = Field(default_factory=AdbConfig)
    stream: StreamConfig = Field(default_factory=StreamConfig)
    debug: DebugConfig = Field(default_factory=DebugConfig)
    metrics: MetricsConfig = Field(default_factory=MetricsConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)

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
