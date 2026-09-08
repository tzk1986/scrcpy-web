"""
配置模块
=========

层：横切关注点。

从 config/settings.py 重新导出 Pydantic Settings 单例。

实际的 Settings 类位于项目根目录的 config/ 包中（不在 backend/ 内部），
以便跨服务共享并独立于 FastAPI 应用进行测试。

使用方式：
    from app.core.config import settings
    s = settings()  # 缓存的单例
    print(s.app.name, s.database.path)
"""

from config.settings import Settings, settings

__all__ = ["Settings", "settings"]
