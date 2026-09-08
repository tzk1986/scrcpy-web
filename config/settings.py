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
    path: str = "adb"
    timeout: int = 30
    reconnect_interval: int = 5


class StreamConfig(BaseSettings):
    max_size: int = 1080
    bit_rate: str = "4M"
    codec: str = "h264"
    fps: int = 30


class DebugConfig(BaseSettings):
    log_buffer_size: int = 50000
    session_ttl_days: int = 7
    shell_history_days: int = 30


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

    with open(config_file) as f:
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
