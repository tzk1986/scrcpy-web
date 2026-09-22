"""
统一异常处理
=============

层：横切关注点（被 domain 使用 + 在 interface 层注册）。

定义：
    1. 以 OpenScrcpyException 为根的异常层级
    2. FastAPI 异常处理器，将异常转换为 JSON 响应

异常层级：
    OpenScrcpyException          — 基类；默认 HTTP 400
    ├── DeviceNotFoundError      — 特定设备 ID 未连接（404）
    ├── SessionNotFoundError     — 调试会话 ID 无效/过期（404）
    ├── PermissionDeniedError    — 调用者无权限执行该操作（403）
    ├── AdbError                 — ADB 子进程返回非零退出码（400）
    ├── DeviceUnreachableError   — TCP 可达性预检失败（400，connect/disconnect 前置）
    └── ConfigReloadError        — 配置热重载失败（400，旧配置保持生效）

所有异常都携带一个机器可读的 ``code`` 字段（如 "DEVICE_NOT_FOUND"）
以及一个人可读的 ``message``。这让前端可以根据 ``error.code`` 进行
国际化或条件逻辑处理。

main.py 中注册的处理器：
    OpenScrcpyException      → exc.status_code（默认 400；未找到 404、权限 403）
    HTTPException            → 原始状态码（404、422 等）
    RequestValidationError   → 422（统一为 {"error": {...}} 形状）
    Exception                → 500（兜底，消息不对客户端暴露）
"""

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class OpenScrcpyException(Exception):
    """
    所有 OpenScrcpy 领域错误的基类。

    类属性：
        status_code: 映射的 HTTP 状态码（子类可覆盖）。

    属性：
        message: 人可读的错误描述。
        code: 机器可读的错误码（大写蛇形命名）。
    """

    status_code: int = 400

    def __init__(self, message: str, code: str = "UNKNOWN_ERROR"):
        self.message = message
        self.code = code
        super().__init__(message)


class DeviceNotFoundError(OpenScrcpyException):
    """当请求的设备 ID 未连接或不在数据库中时抛出。"""

    status_code = 404

    def __init__(self, device_id: str):
        super().__init__(f"Device not found: {device_id}", code="DEVICE_NOT_FOUND")


class SessionNotFoundError(OpenScrcpyException):
    """当调试会话 ID 不存在或已过期时抛出。"""

    status_code = 404

    def __init__(self, session_id: str):
        super().__init__(f"Session not found: {session_id}", code="SESSION_NOT_FOUND")


class PermissionDeniedError(OpenScrcpyException):
    """当调用者权限不足以执行该操作时抛出（如非 admin 转移控制权）。"""

    status_code = 403

    def __init__(self, message: str):
        super().__init__(message, code="PERMISSION_DENIED")


class AdbError(OpenScrcpyException):
    """当 ADB 子进程以非零退出码退出时抛出。"""

    def __init__(self, message: str):
        super().__init__(message, code="ADB_ERROR")


class DeviceUnreachableError(OpenScrcpyException):
    """TCP 可达性预检失败：目标 host:port 无法建立连接。"""

    def __init__(self, host: str, port: int, reason: str):
        super().__init__(f"设备 {host}:{port} 不可达：{reason}", code="DEVICE_UNREACHABLE")


class ConfigReloadError(OpenScrcpyException):
    """配置热重载失败（YAML 解析错误等），旧配置保持生效。"""

    def __init__(self, reason: str):
        super().__init__(f"Config reload failed: {reason}", code="CONFIG_RELOAD_FAILED")


# ---------------------------------------------------------------------------
# FastAPI 异常处理器
# ---------------------------------------------------------------------------

async def openscrcpy_exception_handler(request: Request, exc: OpenScrcpyException) -> JSONResponse:
    """将领域异常转换为标准化的 JSON 响应（状态码取 exc.status_code）。"""
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message}},
    )


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """将 HTTP 异常（404、422 等）转换为 JSON 格式。"""
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": "HTTP_ERROR", "message": exc.detail}},
    )


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """
    将 FastAPI 参数校验错误（默认 {"detail": [...]}）统一为
    {"error": {"code": "VALIDATION_ERROR", "message": ...}} 形状。

    message 拼接各字段的错误位置与原因，便于前端直接展示。
    """
    details = exc.errors()
    parts = []
    for err in details:
        loc = ".".join(str(item) for item in err.get("loc", ()))
        parts.append(f"{loc}: {err.get('msg', '')}")
    message = "; ".join(parts) if parts else "Invalid request parameters"
    return JSONResponse(
        status_code=422,
        content={"error": {"code": "VALIDATION_ERROR", "message": message}},
    )


async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    未预期错误的兜底处理器。

    安全：实际的异常消息不会返回给客户端，
    以避免泄露内部细节。应在服务端记录日志。
    """
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "INTERNAL_ERROR", "message": "An internal error occurred"}},
    )
