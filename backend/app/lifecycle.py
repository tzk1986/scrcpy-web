"""
应用生命周期钩子
=================

层：横切关注点（启动引导）。

定义异步上下文管理器，在 FastAPI 应用启动时运行一次，
在关闭时运行一次。通过 create_app() 的 `lifespan=` 参数注册。

启动顺序：
    1. setup_logging()  — 配置 structlog（生产环境 JSON，开发环境控制台）
    2. init_db()        — 如果不存在则创建 SQLite 表
    3. restore_sessions() — 恢复 TTL 内的活跃调试会话（续跑 logcat 采集）
    4. start_cleanup_task() — 启动定期日志清理后台任务
    5. ConfigWatcher.start() — 启动配置文件热重载监听（可配置关闭）

关闭顺序：
    1. config_watcher.stop() — 停止配置热重载监听
    2. stop_cleanup_task() — 停止日志清理任务
    3. writer.flush()      — 冲刷批量日志缓冲

添加新的启动工作时（如连接池、后台任务），放在 `yield` 之前。
添加关闭工作时，放在 `yield` 之后。
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import settings
from app.core.config_watch import ConfigWatcher
from app.core.logging import get_logger, setup_logging
from app.infrastructure.persistence.sqlite import init_db

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
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

    # 恢复上次进程遗留的活跃调试会话（续跑 logcat 采集）
    from app.deps import get_debug_service
    debug_service = get_debug_service()
    await debug_service.restore_sessions()

    # 启动日志清理任务
    await debug_service.start_cleanup_task()

    # 启动配置热重载监听（app.config_watch 可关闭）
    config_watcher: ConfigWatcher | None = None
    if settings().app.config_watch:
        config_watcher = ConfigWatcher(interval=settings().app.config_watch_interval)
        config_watcher.start()

    logger.info("application_started")

    yield

    logger.info("application_shutting_down")

    # 停止配置热重载监听
    if config_watcher is not None:
        await config_watcher.stop()

    # 停止日志清理任务
    await debug_service.stop_cleanup_task()

    # 冲刷批量写入缓冲，避免退出时丢日志
    await debug_service.writer.flush()

    logger.info("application_stopped")
