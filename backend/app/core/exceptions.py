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
    ├── InvalidIdentifierError   — 外部标识符（设备 ID/包名）格式非法（400，方案 37 A5）
    ├── AdbError                 — ADB 子进程返回非零退出码（400）
    ├── DeviceUnreachableError   — TCP 可达性预检失败（400，connect/disconnect 前置）
    ├── InvalidPairRequestError  — 无线调试配对请求参数非法（400，方案 47 T4）
    ├── PairError                — adb 无线调试配对失败，message 为分类中文文案（400，方案 47 T4）
    ├── ConfigReloadError        — 配置热重载失败（400，旧配置保持生效）
    ├── SettingsStoreError       — 用户设置存储 user.yaml 读写失败（500，方案 45 T2）
    ├── RecordingDisabledError   — 录制总开关关闭时请求开启（400）
    ├── ScanBusyError            — 已有扫描进行中，重复请求被拒（409，方案 36）
    ├── InvalidScanRangeError    — 扫描网段非法或超上限（422，方案 36）
    ├── ConflictError            — 同一设备已有进行态录屏会话（409，方案 37 B3）
    ├── RecordingNotFoundError   — 设备没有录屏会话（404，方案 37 B4）
    ├── RecordingDeleteError     — 录屏产物删除失败（500，方案 46 T2）
    ├── InvalidPathError         — 设备文件路径格式非法（400，方案 39）
    ├── PathNotFoundError        — 设备端路径不存在（404，方案 39）
    ├── TransferBusyError        — 同一设备已有进行中的文件传输（409，方案 39）
    ├── TargetExistsError        — 重命名目标已存在（409，方案 39）
    ├── FileTooLargeError        — 上传超出单文件上限（413，方案 39）
    ├── JobNotFoundError         — 传输任务 ID 无效/过期/跨设备（404，方案 39）
    └── NotAFileError            — 下载目标是目录（400，方案 39）

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


class InvalidIdentifierError(OpenScrcpyException):
    """外部标识符（设备 ID / 包名）格式非法（方案 37 A5，注入面防护）。

    消息不回显原值：避免攻击载荷反射面（A1 报告 L4 同源问题）。
    """

    def __init__(self, kind: str) -> None:
        super().__init__(f"Invalid {kind} format", code="INVALID_IDENTIFIER")


class AdbError(OpenScrcpyException):
    """当 ADB 子进程以非零退出码退出时抛出。"""

    def __init__(self, message: str):
        super().__init__(message, code="ADB_ERROR")


class DeviceUnreachableError(OpenScrcpyException):
    """TCP 可达性预检失败：目标 host:port 无法建立连接。"""

    def __init__(self, host: str, port: int, reason: str):
        super().__init__(f"设备 {host}:{port} 不可达：{reason}", code="DEVICE_UNREACHABLE")


class InvalidPairRequestError(OpenScrcpyException):
    """无线调试配对请求参数非法（方案 47 T4）。

    走 400 结构化错误而非裸 ValueError（后者落 generic 处理器得 500）；
    消息只回显字段名、不回显原值（InvalidIdentifierError 防反射面范式）。
    """

    def __init__(self, field: str) -> None:
        super().__init__(f"Invalid pair request field: {field}", code="INVALID_PAIR_REQUEST")


class PairError(OpenScrcpyException):
    """adb 无线调试配对失败（方案 47 T4），message 为分类中文文案。"""

    CODE_WRONG_HINT = "配对失败：配对码不正确或已过期，请核对手机弹窗显示的 6 位配对码后重试"
    OLD_ADB_HINT = "配对失败：当前 ADB 不支持 pair 命令，请将 platform-tools 升级到 31 及以上（Android 11+ 无线调试配对需要）"
    GENERIC_HINT = (
        "配对失败：请确认手机「使用配对码配对设备」弹窗仍保持打开，"
        "且输入的是弹窗显示的配对端口（弹窗超时或端口变化后需重新发起配对）"
    )

    def __init__(self, message: str) -> None:
        super().__init__(message, code="PAIR_FAILED")

    @classmethod
    def classify(cls, detail: str) -> "PairError":
        """adb pair 失败文本 → 分类 PairError（stderr/stdout 两形态共用）。"""
        low = detail.lower()
        if "command 'pair' not found" in low or "unknown command" in low:
            return cls(cls.OLD_ADB_HINT)
        if "pairing code" in low or "wrong pairing" in low or "invalid pairing" in low:
            return cls(cls.CODE_WRONG_HINT)
        return cls(cls.GENERIC_HINT)


class ConfigReloadError(OpenScrcpyException):
    """配置热重载失败（YAML 解析错误等），旧配置保持生效。"""

    def __init__(self, reason: str):
        super().__init__(f"Config reload failed: {reason}", code="CONFIG_RELOAD_FAILED")


class SettingsStoreError(OpenScrcpyException):
    """用户设置存储（config/user.yaml）读写失败（损坏/不可写）。"""

    status_code = 500

    def __init__(self, message: str) -> None:
        super().__init__(message, code="SETTINGS_STORE_ERROR")


class RecordingDisabledError(OpenScrcpyException):
    """metrics.recording 总开关关闭时请求开启录制（方案 18 状态机，不静默）。"""

    def __init__(self) -> None:
        super().__init__(
            "Metrics recording is disabled by configuration",
            code="RECORDING_DISABLED",
        )


class ScanBusyError(OpenScrcpyException):
    """扫描互斥：已有网段扫描任务进行中，重复请求被拒绝（方案 36）。"""

    status_code = 409

    def __init__(self) -> None:
        super().__init__("已有扫描任务进行中，请等待其完成后再试", code="SCAN_BUSY")


class InvalidScanRangeError(OpenScrcpyException):
    """扫描网段非法或超出支持范围（仅 IPv4 /22-/32，方案 36）。"""

    status_code = 422

    def __init__(self, cidr: str) -> None:
        super().__init__(
            f"扫描网段 {cidr!r} 非法或超出支持范围（仅支持 IPv4，前缀 /22 至 /32）",
            code="INVALID_SCAN_RANGE",
        )


class ConflictError(OpenScrcpyException):
    """录屏互斥：同一设备已有进行态录屏会话（方案 37 B3）。"""

    status_code = 409

    def __init__(self) -> None:
        super().__init__(
            "该设备已有进行中的录屏会话，请先停止当前录制后再试",
            code="RECORDING_IN_PROGRESS",
        )


class RecordingNotFoundError(OpenScrcpyException):
    """当请求的设备没有录屏会话时抛出（方案 37 B4）。"""

    status_code = 404

    def __init__(self, device_id: str) -> None:
        super().__init__(f"设备 {device_id} 没有录屏会话", code="NO_RECORDING")


class RecordingDeleteError(OpenScrcpyException):
    """录屏产物删除失败（文件被占用等 OS 层错误，方案 46 T2）。"""

    status_code = 500

    def __init__(self, filename: str) -> None:
        super().__init__(f"录屏产物 {filename} 删除失败", code="RECORDING_DELETE_FAILED")


class InvalidPathError(OpenScrcpyException):
    """设备文件路径格式非法（方案 39）。

    消息不回显原值：避免攻击载荷反射面（仿 InvalidIdentifierError）。
    """

    def __init__(self, reason: str) -> None:
        super().__init__(f"Invalid path: {reason}", code="INVALID_PATH")


class PathNotFoundError(OpenScrcpyException):
    """设备端路径不存在/查询不到条目（方案 39）。"""

    status_code = 404

    def __init__(self, path: str) -> None:
        super().__init__(f"Path not found: {path}", code="PATH_NOT_FOUND")


class TransferBusyError(OpenScrcpyException):
    """传输互斥：同一设备已有进行中的上传/下载（方案 39，对齐方案 35 连接治理语义）。"""

    status_code = 409

    def __init__(self) -> None:
        super().__init__(
            "该设备已有进行中的文件传输，请等待其完成后再试",
            code="TRANSFER_BUSY",
        )


class TargetExistsError(OpenScrcpyException):
    """重命名目标已存在（方案 39）。"""

    status_code = 409

    def __init__(self, path: str) -> None:
        super().__init__(f"Target already exists: {path}", code="TARGET_EXISTS")


class FileTooLargeError(OpenScrcpyException):
    """上传文件超出单文件上限（方案 39）。"""

    status_code = 413

    def __init__(self, limit_bytes: int) -> None:
        super().__init__(
            f"File exceeds upload limit of {limit_bytes} bytes",
            code="FILE_TOO_LARGE",
        )


class JobNotFoundError(OpenScrcpyException):
    """传输任务 ID 无效/已过期/不属于该设备（方案 39；不区分原因以防探测）。"""

    status_code = 404

    def __init__(self, job_id: str) -> None:
        super().__init__(f"Transfer job not found: {job_id}", code="JOB_NOT_FOUND")


class NotAFileError(OpenScrcpyException):
    """下载目标是目录（首版不支持目录直下，方案 39）。"""

    def __init__(self, path: str) -> None:
        super().__init__(f"Not a file (download unsupported): {path}", code="NOT_A_FILE")


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
