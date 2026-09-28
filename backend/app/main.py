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
from pathlib import Path

# Windows 上需要 ProactorEventLoop 才能使用 asyncio.create_subprocess_exec
# 必须在任何其他 asyncio 代码之前设置
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import settings
from app.core.exceptions import (
    OpenScrcpyException,
    generic_exception_handler,
    http_exception_handler,
    openscrcpy_exception_handler,
    validation_exception_handler,
)
from app.interfaces.http import apps, debug, devices, network, performance, sessions, system
from app.interfaces.ws import debug as ws_debug
from app.interfaces.ws import performance as ws_performance
from app.interfaces.ws import video as ws_video
from app.lifecycle import lifespan


def _compute_frontend_dist() -> Path:
    """
    前端 dist 目录。

    - 源码运行：仓库根（backend/app/main.py 上溯三级）→ <仓库>/frontend/dist
    - 冻结运行（PyInstaller 6 onedir）：bundle 根 sys._MEIPASS（= <产物>/_internal）
      → <产物>/_internal/frontend/dist，与 spec datas 目标 "frontend/dist" 一致。
      注意：frozen 下 __file__ = _MEIPASS/app/main.py，上溯级数与源码不同
      （无 backend/ 段），不能复用同一表达式。
    """
    if getattr(sys, "frozen", False):
        bundle_root = getattr(sys, "_MEIPASS", None)
        if bundle_root:
            return Path(bundle_root) / "frontend" / "dist"
        return Path(__file__).resolve().parent.parent / "frontend" / "dist"
    return Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"


FRONTEND_DIST = _compute_frontend_dist()


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
    # CORS：来源从 config/security.cors_origins 读取（环境变量可覆盖），
    # 默认见 config/base.yaml；生产环境必须显式设置。
    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.security.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        # 导出端点元数据头（方案 18 O2）：前端回显行数与区间
        expose_headers=["X-Export-Count", "X-Export-Oldest-Ts", "X-Export-Newest-Ts"],
    )

    # --- 异常处理器 --------------------------------------------------------
    # 四层：领域异常 → 校验错误 → HTTP 异常 → 兜底。
    # Starlette 的类型签名只接受 (Request, Exception) 处理器；
    # FastAPI 实际按异常类型精确分发，这里收窄的参数类型是安全的
    app.add_exception_handler(OpenScrcpyException, openscrcpy_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, generic_exception_handler)

    # --- HTTP 路由（interfaces/http/）--------------------------------------
    app.include_router(apps.router)        # /api/apps
    app.include_router(devices.router)     # /api/devices
    app.include_router(debug.router)       # /api/debug
    app.include_router(network.router)     # /api/network
    app.include_router(performance.router) # /api/perf
    app.include_router(sessions.router)    # /api/sessions
    app.include_router(system.router)      # /api/system（配置热重载）

    # --- WebSocket 端点 ---------------------------------------------------
    # 每个 WS 端点委托给 interfaces/ws/ 中的处理器，
    # 那里包含实际的协议逻辑（帧中继、JSON 命令等）。
    @app.websocket("/ws/video/{device_id}")
    async def video_ws(websocket: WebSocket, device_id: str) -> None:
        from app.deps import get_stream_service
        await ws_video.video_stream(websocket, device_id, get_stream_service())

    @app.websocket("/ws/debug/{session_id}")
    async def debug_ws(websocket: WebSocket, session_id: str) -> None:
        from app.deps import get_debug_service
        await ws_debug.debug_stream(websocket, session_id, get_debug_service())

    @app.websocket("/ws/perf/{device_id}")
    async def perf_ws(websocket: WebSocket, device_id: str) -> None:
        from app.deps import get_performance_service
        await ws_performance.stream_metrics(websocket, device_id, get_performance_service())

    # --- 健康检查 ---------------------------------------------------------
    # 供 Docker HEALTHCHECK 和容器编排器（k8s、compose）使用。
    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    # --- 前端静态托管（方案 15 任务 2）--------------------------------------
    # dist 完整（已构建或打包注入，index.html 存在为完整性判据）才挂载；
    # 源码运行未构建/构建中断时跳过，避免 fallback 指向不存在的文件。
    # 顺序敏感：本块必须在所有 API/WS 路由之后，否则 catch-all 会吞掉 API。
    if FRONTEND_DIST.is_dir() and (FRONTEND_DIST / "index.html").is_file():
        root = FRONTEND_DIST.resolve()
        assets_dir = root / "assets"
        if assets_dir.is_dir():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

        @app.api_route("/{frontend_path:path}", methods=["GET", "HEAD"], include_in_schema=False)
        async def spa_fallback(frontend_path: str) -> FileResponse:
            # API/WS 命名空间（首路径段）的未匹配路径保持 404 语义，
            # 不被前端 fallback 吞成 200 HTML
            if frontend_path.split("/", 1)[0] in ("api", "ws"):
                raise HTTPException(status_code=404, detail="Not Found")
            # 命中 dist 内真实文件则直接返回（favicon 等根级资源），
            # 否则一律 fallback 到 index.html（createWebHistory 深链/刷新）；
            # containment 判定先于 stat，杜绝符号链接/编码逃逸路径被直接返回
            candidate = (root / frontend_path).resolve()
            if frontend_path and candidate.is_relative_to(root) and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(root / "index.html")

    return app


# 创建默认应用实例，供 `uvicorn app.main:app` 使用。
# 测试可直接调用 create_app() 以获得隔离性。
app = create_app()


if __name__ == "__main__":
    import uvicorn

    srv = settings().server
    uvicorn.run(app, host=srv.host, port=srv.port)
