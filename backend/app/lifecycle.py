"""
应用生命周期钩子
=================

层：横切关注点（启动引导）。

定义异步上下文管理器，在 FastAPI 应用启动时运行一次，
在关闭时运行一次。通过 create_app() 的 `lifespan=` 参数注册。

启动顺序：
    1. setup_logging()  — 配置 structlog（生产环境 JSON，开发环境控制台）
    2. init_db()        — 如果不存在则创建 SQLite 表

关闭顺序：
    （当前无需清理；aiosqlite 连接是每次操作独立的。）

添加新的启动工作时（如连接池、后台任务），放在 `yield` 之前。
添加关闭工作时，放在 `yield` 之后。
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.logging import get_logger, setup_logging
from app.infrastructure.persistence.sqlite import init_db

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    应用生命周期上下文管理器。

    运行启动钩子，让出控制权给运行中的应用，然后在应用停止时
    （SIGTERM、Ctrl+C 等）运行关闭钩子。

    参数：
        app: FastAPI 应用实例（规范要求的参数，本模块未使用）。
    """
    logger.info("application_starting")
    setup_logging()
    await init_db()
    logger.info("application_started")

    yield

    logger.info("application_shutting_down")
    logger.info("application_stopped")
