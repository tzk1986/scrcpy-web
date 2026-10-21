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
    ├── DeviceNotReadyError      — adb 回 already connected 但 transport 非 device 态（400，方案 51 D11 补树）
    ├── ScrcpyOccupiedError      — 设备端有在跑 scrcpy 会话，起流前拦截（400，方案 48 D1，D11 补树）
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
    ├── NotAFileError            — 下载目标是目录（400，方案 39）
    ├── SseClientLimitError      — SSE 订阅连接数达上限，新连接被拒（503，A1 L1）
    └── SessionLimitError        — 活跃会话数达上限，新建被拒（503，A1 L2）

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


def _clip(text: str, limit: int = 120) -> str:
    """回显用户输入的错误消息净化（A1 L4）：截断 + 剔除不可打印字符。

    错误消息会进 HTTP 响应与日志：超长载荷刷屏、控制字符（换行/ANSI
    转义/NUL）构成日志注入与终端转义面。正常短输入原样通过。
    """
    cleaned = "".join(ch for ch in text if ch.isprintable())
    return cleaned if len(cleaned) <= limit else cleaned[:limit] + "…"


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
        super().__init__(f"Device not found: {_clip(device_id)}", code="DEVICE_NOT_FOUND")


class SessionNotFoundError(OpenScrcpyException):
    """当调试会话 ID 不存在或已过期时抛出。"""

    status_code = 404

    def __init__(self, session_id: str):
        super().__init__(f"Session not found: {_clip(session_id)}", code="SESSION_NOT_FOUND")


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
    """当 ADB 子进程以非零退出码退出时抛出。

    消息不净化：A1 L4 针对「回显用户输入」的消息，AdbError 属 adb 诊断
    输出（不含用户输入），且 err-38 重试合并等链路的完整文本有既有断言
    与 200 字符截断专项口径，_clip 会破坏二者。
    """

    def __init__(self, message: str):
        super().__init__(message, code="ADB_ERROR")


class DeviceUnreachableError(OpenScrcpyException):
    """TCP 可达性预检失败：目标 host:port 无法建立连接。"""

    def __init__(self, host: str, port: int, reason: str):
        super().__init__(
            f"设备 {_clip(host)}:{port} 不可达：{_clip(reason)}",
            code="DEVICE_UNREACHABLE",
        )


class DeviceNotReadyError(OpenScrcpyException):
    """adb 声称已连接但 transport 实际不可用（路线图 15 假成功守卫）。

    `adb connect` 对已登记地址回 already connected（不尝试重连），旧判定
    字符串命中即成功；而 offline/unauthorized 行会被设备列表过滤——用户
    见「连接成功」却无设备。connect_tcp 对 already connected 分支补设备表
    状态复核，非 device 态经此异常回明确中文指引。
    """

    OFFLINE_HINT = (
        "设备 {device} 状态为 offline：TCP 已建立但设备端 adbd 握手异常，"
        "不会出现在设备列表。请在设备端关闭再打开网络 ADB 调试后重试；"
        "仍不行请重启设备或在主机执行 adb kill-server 后重连"
    )
    UNAUTHORIZED_HINT = (
        "设备 {device} 等待授权：请在设备屏幕上确认允许调试的弹窗"
        "（首次连接或未信任本机时会停留在该状态）后重试"
    )
    ABSENT_HINT = (
        "设备 {device} 显示已连接但未出现在设备表中：请先在设备列表"
        "执行「断开」后重新连接"
    )

    def __init__(self, message: str) -> None:
        super().__init__(message, code="DEVICE_NOT_READY")

    @classmethod
    def classify(cls, device_id: str, state: str | None) -> "DeviceNotReadyError":
        """设备表状态（None=不在表）→ 分类 DeviceNotReadyError。"""
        device = _clip(device_id)
        if state == "offline":
            return cls(cls.OFFLINE_HINT.format(device=device))
        if state == "unauthorized":
            return cls(cls.UNAUTHORIZED_HINT.format(device=device))
        return cls(cls.ABSENT_HINT.format(device=device))


class ScrcpyOccupiedError(OpenScrcpyException):
    """设备端存在在跑的 scrcpy 会话，起流前被拦截（方案 48 D1=A 仅提示阻断）。

    残留两类来源：本软件上次会话异常退出（stop 仅杀本地 adb 子进程，设备端
    不保证随退）或其他工具/客户端的在跑会话。设备侧无法确证连接来源，
    以启动参数指纹比对做概率性归因（D3）：命中本软件特征串 → 残留文案；
    否则 → 其他工具文案（亦覆盖归因失败的兜底场景）。
    """

    OWN_RESIDUAL_HINT = (
        "设备 {device} 上检测到已在运行的 scrcpy 会话（启动参数与本软件一致），"
        "判定为上次会话异常退出的残留进程：请重启设备清除残留后重试；"
        "使用 scrcpy 桌面版等其他工具关闭该会话后重试亦可"
    )
    OTHER_SESSION_HINT = (
        "设备 {device} 上检测到已在运行的 scrcpy 会话：可能是其他工具"
        "（如 scrcpy 桌面版或其他电脑）正在使用，也可能是本软件上次会话"
        "异常退出残留。请关闭占用程序后重试；无法关闭时重启设备可清除"
    )

    def __init__(self, message: str) -> None:
        super().__init__(message, code="SCRCPY_SESSION_OCCUPIED")

    @classmethod
    def classify(cls, device_id: str, cmdline: str | None) -> "ScrcpyOccupiedError":
        """残留进程 cmdline（读失败为 None）→ 参数指纹归因分类。"""
        markers = ("max_size=0", "tunnel_forward=true", "audio=false", "clipboard_autosync=false")
        device = _clip(device_id)
        if cmdline and all(m in cmdline for m in markers):
            return cls(cls.OWN_RESIDUAL_HINT.format(device=device))
        return cls(cls.OTHER_SESSION_HINT.format(device=device))


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
        detail = _clip(detail)
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
            f"扫描网段 {_clip(cidr)!r} 非法或超出支持范围（仅支持 IPv4，前缀 /22 至 /32）",
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
        super().__init__(f"设备 {_clip(device_id)} 没有录屏会话", code="NO_RECORDING")


class RecordingDeleteError(OpenScrcpyException):
    """录屏产物删除失败（文件被占用等 OS 层错误，方案 46 T2）。"""

    status_code = 500

    def __init__(self, filename: str) -> None:
        # 方案 51 D07：filename 来自用户输入，回显须走 _clip（与其它回显类统一）
        super().__init__(f"录屏产物 {_clip(filename)} 删除失败", code="RECORDING_DELETE_FAILED")


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
        super().__init__(f"Path not found: {_clip(path)}", code="PATH_NOT_FOUND")


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
        super().__init__(f"Target already exists: {_clip(path)}", code="TARGET_EXISTS")


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
        super().__init__(f"Transfer job not found: {_clip(job_id)}", code="JOB_NOT_FOUND")


class NotAFileError(OpenScrcpyException):
    """下载目标是目录（首版不支持目录直下，方案 39）。"""

    def __init__(self, path: str) -> None:
        super().__init__(f"Not a file (download unsupported): {_clip(path)}", code="NOT_A_FILE")


class SseClientLimitError(OpenScrcpyException):
    """SSE 订阅连接数达上限，新连接被拒（A1 L1 资源耗尽防护）。"""

    status_code = 503

    def __init__(self, limit: int) -> None:
        super().__init__(
            f"SSE 事件流连接数已达上限（{limit}），请关闭部分页面后重试",
            code="SSE_CLIENT_LIMIT",
        )


class SessionLimitError(OpenScrcpyException):
    """活跃会话数达上限，新建被拒（A1 L2：无鉴权下防会话无界堆积）。

    kind 为会话类别中文名（如「调试」「协作」），消息不回显任何用户输入。
    """

    status_code = 503

    def __init__(self, kind: str, limit: int) -> None:
        super().__init__(
            f"{kind}活跃会话数已达上限（{limit}），请先关闭部分会话后重试",
            code="SESSION_LIMIT",
        )


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
