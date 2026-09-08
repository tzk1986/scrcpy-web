"""
统一异常处理
=============

层：横切关注点（被 domain 使用 + 在 interface 层注册）。

定义：
    1. 以 OpenScrcpyException 为根的异常层级
    2. FastAPI 异常处理器，将异常转换为 JSON 响应

异常层级：
    OpenScrcpyException          — 基类；始终返回 HTTP 400
    ├── DeviceNotFoundError      — 特定设备 ID 未连接
    ├── SessionNotFoundError     — 调试会话 ID 无效/过期
    └── AdbError                 — ADB 子进程返回非零退出码

所有异常都携带一个机器可读的 ``code`` 字段（如 "DEVICE_NOT_FOUND"）
以及一个人可读的 ``message``。这让前端可以根据 ``error.code`` 进行
国际化或条件逻辑处理。

main.py 中注册的三个处理器：
    OpenScrcpyException  → 400（领域级错误）
    HTTPException        → 原始状态码（404、422 等）
    Exception            → 500（兜底，消息不对客户端暴露）
"""

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class OpenScrcpyException(Exception):
    """
    所有 OpenScrcpy 领域错误的基类。

    属性：
        message: 人可读的错误描述。
        code: 机器可读的错误码（大写蛇形命名）。
    """

    def __init__(self, message: str, code: str = "UNKNOWN_ERROR"):
        self.message = message
        self.code = code
        super().__init__(message)


class DeviceNotFoundError(OpenScrcpyException):
    """当请求的设备 ID 未连接或不在数据库中时抛出。"""

    def __init__(self, device_id: str):
        super().__init__(f"Device not found: {device_id}", code="DEVICE_NOT_FOUND")


class SessionNotFoundError(OpenScrcpyException):
    """当调试会话 ID 不存在或已过期时抛出。"""

    def __init__(self, session_id: str):
        super().__init__(f"Session not found: {session_id}", code="SESSION_NOT_FOUND")


class AdbError(OpenScrcpyException):
    """当 ADB 子进程以非零退出码退出时抛出。"""

    def __init__(self, message: str):
        super().__init__(message, code="ADB_ERROR")


# ---------------------------------------------------------------------------
# FastAPI 异常处理器
# ---------------------------------------------------------------------------

async def openscrcpy_exception_handler(request: Request, exc: OpenScrcpyException):
    """将领域异常转换为标准化的 400 JSON 响应。"""
    return JSONResponse(
        status_code=400,
        content={"error": {"code": exc.code, "message": exc.message}},
    )


async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """将 HTTP 异常（404、422 等）转换为 JSON 格式。"""
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": "HTTP_ERROR", "message": exc.detail}},
    )


async def generic_exception_handler(request: Request, exc: Exception):
    """
    未预期错误的兜底处理器。

    安全：实际的异常消息不会返回给客户端，
    以避免泄露内部细节。应在服务端记录日志。
    """
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "INTERNAL_ERROR", "message": "An internal error occurred"}},
    )
