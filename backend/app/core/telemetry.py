"""
OpenTelemetry 设置（占位符）
==============================

层：横切关注点。

本模块是未来分布式追踪和指标集成的占位符，
通过 OpenTelemetry 实现。目前只记录一条跳过日志。

实现时应：
    1. 配置 TracerProvider 和 OTLP 导出器
    2. 设置 MetricsProvider 用于请求/错误计数器
    3. 添加 FastAPI 插桩中间件

在 lifespan.py 启动时调用（当前未接入）。
"""

from app.core.logging import get_logger

logger = get_logger(__name__)


def setup_telemetry():
    """配置 OpenTelemetry 追踪和指标（尚未实现）。"""
    logger.info("telemetry_setup_skipped")
