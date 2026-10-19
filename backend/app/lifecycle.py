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
    4. start_cleanup_task() — 启动定期清理后台任务（debug 日志 +
       recording 目录双限/自愈，方案 37 B3；recording 清理循环首轮
       即含强杀残留扫盘清，方案 42 §3）
    5. ConfigWatcher.start() — 启动配置文件热重载监听（可配置关闭）
    6. DeviceService.start() — 启动设备状态 5s 轮询循环，上下线经
       SSE /api/devices/events 自动播报（接通路线图 §10 发现的
       「生产者未接线」断点）

关闭顺序：
    1. device_service.stop() — 停止设备状态轮询（与启动逆序）
    2. config_watcher.stop() — 停止配置热重载监听
    3. performance_service.cleanup() / network_service.cleanup() — 停止采样并
       释放内存暂存（含录制 final flush，方案 18 §3.7）
    4. stop_cleanup_task() — 停止后台清理任务（recording + debug 日志）；
       随后 RecordingService.cleanup_all_recordings() 清空录像暂存目录
       （方案 42 §3）
    5. writer.flush()      — 冲刷批量日志缓冲

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
    from app.deps import (
        get_debug_service,
        get_device_service,
        get_network_service,
        get_performance_service,
        get_recording_service,
    )
    debug_service = get_debug_service()
    await debug_service.restore_sessions()

    # 启动日志清理任务
    await debug_service.start_cleanup_task()

    # 启动录屏目录双限/会话自愈周期任务（方案 37 B3）
    await get_recording_service().start_cleanup_task()

    # 启动配置热重载监听（app.config_watch 可关闭）
    config_watcher: ConfigWatcher | None = None
    if settings().app.config_watch:
        config_watcher = ConfigWatcher(interval=settings().app.config_watch_interval)
        config_watcher.start()

    # 启动设备状态轮询循环（上下线经 SSE 自动播报，路线图 §10 断点接线）
    device_service = get_device_service()
    await device_service.start()

    logger.info("application_started")

    yield

    logger.info("application_shutting_down")

    # 停止设备状态轮询（与启动逆序）
    await device_service.stop()

    # 停止配置热重载监听
    if config_watcher is not None:
        await config_watcher.stop()

    # 停止性能/网络采样并释放内存暂存（均幂等，见方案 18 §3.7）
    await get_performance_service().cleanup()
    await get_network_service().cleanup()

    # 停止录屏周期清理任务（方案 37 B3）
    recording_service = get_recording_service()
    await recording_service.stop_cleanup_task()

    # 退出清盘：录像属暂存——退出后会话内存态随进程消失，残留文件已无
    # 下载入口（纯垃圾），清空符合磁盘数据保留策略（方案 42 §3）
    recording_service.cleanup_all_recordings()

    # 停止日志清理任务
    await debug_service.stop_cleanup_task()

    # 冲刷批量写入缓冲，避免退出时丢日志
    await debug_service.writer.flush()

    logger.info("application_stopped")
