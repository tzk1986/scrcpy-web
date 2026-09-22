"""
Configuration Settings

Loads configuration from:
1. config/base.yaml (defaults)
2. config/{env}.yaml (environment-specific)
3. Environment variables (highest priority)
"""

from pathlib import Path
from typing import Any

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppConfig(BaseSettings):
    name: str = "OpenScrcpy"
    version: str = "0.1.0"
    debug: bool = False
    log_level: str = "INFO"
    config_watch: bool = True  # 配置文件（config/*.yaml）热重载监听开关
    config_watch_interval: float = 2.0  # 热重载的 mtime 轮询间隔（秒）


class ServerConfig(BaseSettings):
    host: str = "0.0.0.0"
    port: int = 8765  # 后端 HTTP/WS 端口；可被 base.yaml server.port 或 BACKEND_PORT 环境变量覆盖
    workers: int = 4


class DatabaseConfig(BaseSettings):
    path: str = "./data/debug.sqlite"
    echo: bool = False


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
    scrcpy_path: str = Field(default="D:/scrcpy-win64-v4.1/scrcpy.exe", alias="SCRCPY_PATH")
    # 自适应码率：scrcpy 协议无运行中改码率消息，通过按档重启编码器实现（每次切换约 1-3s 黑屏）
    adaptive_bitrate: bool = True
    bitrate_tiers: str = "8M,4M,2M,1M"  # 降序档位阶梯，起始档取不超过 bit_rate 的最大档
    # 视频流协议兜底开关：False（默认）走 12 字节包头协议（方案 17 实施项 1b）；
    # 真机验证失败时置 True 回退 raw_stream 裸流 + 启发式解析（实施项 1a/1a 尾步骤路径）
    raw_stream_fallback: bool = False


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


class SecurityConfig(BaseSettings):
    jwt_secret: str = Field(default="change-me-in-production")
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440
    cors_origins: list[str] = ["http://localhost:8080"]


class Settings(BaseSettings):
    app: AppConfig = Field(default_factory=AppConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    adb: AdbConfig = Field(default_factory=AdbConfig)
    stream: StreamConfig = Field(default_factory=StreamConfig)
    debug: DebugConfig = Field(default_factory=DebugConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


def load_yaml_config(env: str = "base") -> dict[str, Any]:
    """Load YAML configuration file"""
    config_dir = Path(__file__).parent
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
