"""
FastAPI 应用工厂
==================

层：接口层（启动引导）。

本模块是组合根（composition root）——所有层在此处组装在一起的唯一位置。
职责：

1. 使用 config/settings.py 的配置实例化 FastAPI 应用
2. 注册 CORS 中间件（来源由配置驱动）
3. 注册统一异常处理器（OpenScrcpy / HTTP / 通用）
4. 挂载 HTTP 路由：interfaces/http/（devices、debug、sessions）
5. 挂载 WebSocket 端点（/ws/video/{device_id}、/ws/debug/{session_id}）
6. 暴露 /health 端点供容器编排器使用

注意：WebSocket 路由在此处内联定义而不是在路由器中，因为
FastAPI 的 @app.websocket 装饰器与 APIRouter 的路径参数路由配合不佳。
处理逻辑仍然委托给 interfaces/ws/ 中的处理器。
"""

import sys
import asyncio

# Windows 上需要 ProactorEventLoop 才能使用 asyncio.create_subprocess_exec
# 必须在任何其他 asyncio 代码之前设置
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import settings
from app.core.exceptions import (
    OpenScrcpyException,
    generic_exception_handler,
    http_exception_handler,
    openscrcpy_exception_handler,
)
from app.interfaces.http import debug, devices, sessions
from app.interfaces.ws import debug as ws_debug
from app.interfaces.ws import video as ws_video
from app.lifecycle import lifespan


def create_app() -> FastAPI:
    """
    构建并返回完全配置的 FastAPI 应用。

    工厂模式（相对于模块级 `app = FastAPI(...)`）允许测试
    使用覆盖的设置创建隔离的应用实例。

    返回：
        FastAPI: 配置好的应用实例。
    """
    s = settings()

    app = FastAPI(
        title=s.app.name,
        version=s.app.version,
        debug=s.app.debug,
        lifespan=lifespan,
    )

    # --- 中间件 -----------------------------------------------------------
    # CORS：来源从 config/security.cors_origins 读取（环境变量覆盖）。
    # 开发环境为 ["*"]；生产环境必须显式设置。
    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.security.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # --- 异常处理器 --------------------------------------------------------
    # 三层：领域异常 → HTTP 异常 → 兜底。
    app.add_exception_handler(OpenScrcpyException, openscrcpy_exception_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, generic_exception_handler)

    # --- HTTP 路由（interfaces/http/）--------------------------------------
    app.include_router(devices.router)     # /api/devices
    app.include_router(debug.router)       # /api/debug
    app.include_router(sessions.router)    # /api/sessions

    # --- WebSocket 端点 ---------------------------------------------------
    # 每个 WS 端点委托给 interfaces/ws/ 中的处理器，
    # 那里包含实际的协议逻辑（帧中继、JSON 命令等）。
    @app.websocket("/ws/video/{device_id}")
    async def video_ws(websocket: WebSocket, device_id: str):
        from app.deps import get_stream_service
        await ws_video.video_stream(websocket, device_id, get_stream_service())

    @app.websocket("/ws/debug/{session_id}")
    async def debug_ws(websocket: WebSocket, session_id: str):
        from app.deps import get_debug_service
        await ws_debug.debug_stream(websocket, session_id, get_debug_service())

    # --- 健康检查 ---------------------------------------------------------
    # 供 Docker HEALTHCHECK 和容器编排器（k8s、compose）使用。
    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app


# 创建默认应用实例，供 `uvicorn app.main:app` 使用。
# 测试可直接调用 create_app() 以获得隔离性。
app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
