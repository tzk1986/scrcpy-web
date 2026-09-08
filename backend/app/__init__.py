"""
OpenScrcpy 后端应用
=====================

项目根包，是整个后端服务的入口。

架构（4 层，依赖倒置）：
    interfaces/    → HTTP 和 WebSocket 端点（薄控制器）
    application/   → 用例编排服务
    domain/        → 纯业务实体和端口协议
    infrastructure/ → 具体实现（ADB CLI、SQLite、scrcpy 等）
    core/          → 横切关注点（配置、日志、异常）

依赖方向：interfaces → application → domain ← infrastructure
           domain 不依赖任何东西（纯 Python）。

技术栈：
    - FastAPI + asyncio（Web 框架）
    - Pydantic Settings（配置管理，YAML + 环境变量）
    - aiosqlite（异步 SQLite 持久化）
    - structlog（结构化 JSON 日志）
    - scrcpy-server（H.264 视频编码）
    - ADB CLI 子进程（设备通信）

仅官方支持 Chrome 浏览器（WebCodecs、WebTransport 等特性）。
"""

__version__ = "0.1.0"
