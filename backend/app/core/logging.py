"""
结构化日志设置
===============

层：横切关注点。

将 structlog 配置为应用范围的日志框架。

按环境区分行为：
    - dev  (debug=True):  彩色控制台渲染，人类可读
    - prod (debug=False): JSON 渲染，机器可解析

所有日志条目自动包含：
    - 时间戳（ISO 8601）
    - 日志级别
    - 上下文变量（从 contextvars 合并）
    - 异常信息（存在时）

使用方式：
    from app.core.logging import get_logger
    logger = get_logger(__name__)
    logger.info("operation_name", key1=value1, key2=value2)

setup_logging() 在应用启动时由 lifespan.py 调用一次。
"""

import logging

import structlog

from app.core.config import settings


def setup_logging():
    """
    配置 structlog 处理器和输出格式。

    在应用启动时调用一次。调用后，所有 structlog.get_logger() 的调用
    都会返回一个正确配置的绑定日志记录器。
    """
    s = settings()

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.TimeStamper(fmt="iso"),
            # 开发环境：彩色控制台；生产环境：JSON 行
            structlog.dev.ConsoleRenderer()
            if s.app.debug
            else structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, s.app.log_level.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.BoundLogger:
    """
    返回绑定到给定模块名的结构化日志记录器。

    参数：
        name: 通常是调用模块的 ``__name__``。

    返回：
        一个 structlog BoundLogger，支持关键字参数日志：
        ``logger.info("event_name", key=value)``。
    """
    return structlog.get_logger(name)
