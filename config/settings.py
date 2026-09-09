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


class ServerConfig(BaseSettings):
    host: str = "0.0.0.0"
    port: int = 8000
    workers: int = 4


class DatabaseConfig(BaseSettings):
    path: str = "./data/debug.sqlite"
    echo: bool = False


class AdbConfig(BaseSettings):
    adb_path: str = Field(default="", alias="ADB_PATH")  # 使用不同名字避免与 PATH 冲突
    timeout: int = 30
    reconnect_interval: int = 5

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


class DebugConfig(BaseSettings):
    log_buffer_size: int = 50000
    session_ttl_days: int = 7
    log_retention_days: int = 7
    shell_history_days: int = 30
    max_db_size_mb: int = 1000  # 最大数据库大小（MB），超过时自动清理旧日志
    cleanup_interval_hours: int = 1  # 自动清理间隔（小时）
    min_log_level: str = "I"  # 最低日志级别：V/D/I/W/E/F（生产环境建议 W）
    log_rate_limit: int = 100  # 每秒最大日志数，超过时自动丢弃低级别日志


class SecurityConfig(BaseSettings):
    jwt_secret: str = Field(default="change-me-in-production")
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440
    cors_origins: list[str] = ["http://localhost:5173"]


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


def _deep_merge(base: dict, override: dict) -> dict:
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

    env = os.getenv("APP_ENV", "base")
    config = load_yaml_config("base")

    if env != "base":
        env_config = load_yaml_config(env)
        config = _deep_merge(config, env_config)

    return Settings(**config)


_settings: Settings | None = None


def settings() -> Settings:
    """Get cached settings instance"""
    global _settings
    if _settings is None:
        _settings = get_settings()
    return _settings
