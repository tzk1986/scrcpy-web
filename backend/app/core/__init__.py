"""
核心模块
=========

层：横切关注点。

包含所有其他层都使用的、与框架无关的工具：

    config.py     — Pydantic Settings；加载 YAML + 环境变量覆盖
    logging.py    — structlog 设置（生产环境 JSON，开发环境控制台）
    exceptions.py — 领域异常层级 + FastAPI 异常处理器
    telemetry.py  — OpenTelemetry 占位符（未来）

core/ 中的内容不应导入 application/、domain/、infrastructure/
或 interfaces/。它位于依赖图的最底层。
"""
