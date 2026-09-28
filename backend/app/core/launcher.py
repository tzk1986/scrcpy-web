"""
绿色版启动器辅助（方案 15 §8.5 任务 3 / §8.4-2/3/6、§9.3-5）
=============================================================

仅在 PyInstaller 冻结环境（sys.frozen）使用：文件日志重定向、
SetDllDirectoryW 修复、单实例互斥、端口探测回退、就绪轮询后开浏览器。
源码运行（dev）仅使用本模块的退出回调注册表（§23-T2），其余行为不经过
本模块、保持不变。
"""

from __future__ import annotations

import ctypes
import socket
import sys
import time
from collections.abc import Callable
from pathlib import Path

_SINGLE_INSTANCE_MUTEX = "OpenScrcpy-SingleInstance-Mutex"
_ERROR_ALREADY_EXISTS = 183
# SO_EXCLUSIVEADDRUSE 仅在 Windows typeshed 定义，直引会让 Linux CI 的 mypy
# 报 attr-defined。此选项运行时仅 Windows 有效，getattr 兜底仅为跨平台静态检查；
# 非 Windows 值为 0，setsockopt 抛 OSError 由下方 except 吞掉，语义不变。
_SO_EXCLUSIVEADDRUSE = getattr(socket, "SO_EXCLUSIVEADDRUSE", 0)


def fix_dll_search_path() -> None:
    """解除 PyInstaller bootloader 对子进程的 DLL 搜索路径限制（§8.4-2）。"""
    if sys.platform == "win32":
        ctypes.windll.kernel32.SetDllDirectoryW(None)


def acquire_single_instance_mutex(name: str = _SINGLE_INSTANCE_MUTEX) -> bool:
    """Windows CreateMutex 单实例锁：已存在（另一实例在跑）返回 False。

    句柄不释放——进程退出时由系统回收，持有期即实例存活期。
    非 Windows 恒返回 True（打包目标仅 Windows）。
    """
    if sys.platform != "win32":
        return True
    # use_last_error 必须走 WinDLL 构造参数：缓存的 _NamedFuncPointer 没有
    # use_last_error 实例属性（对函数指针直接赋值只静默写进 __dict__，调用
    # 机制不生效，get_last_error 恒为 0——修复轮实证）。构造参数会把
    # _FUNCFLAG_USELASTERROR 写入该 DLL 全部函数指针，调用后紧跟捕获
    # GetLastError，规避中间调用污染（任务 3 审查 M1 修正）
    create_mutex = ctypes.WinDLL("kernel32", use_last_error=True).CreateMutexW
    create_mutex.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    create_mutex.restype = ctypes.c_void_p
    create_mutex(None, False, name)
    return ctypes.get_last_error() != _ERROR_ALREADY_EXISTS


def _port_has_listener(port: int, timeout: float = 0.5) -> bool:
    """连接预检：目标端口存在任何监听者（含 0.0.0.0 通配绑定）即视为占用。

    Windows 允许 0.0.0.0:port 与具体地址:port 共存且连接路由具体地址优先，
    仅试绑具体地址会误判空闲（方案 23 缺陷 D1，§1.2/§3.3）——先连接预检
    再试绑：通配绑定同样接受回环连接，正确触发回退。
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(timeout)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def find_available_port(host: str, start_port: int, attempts: int = 5) -> int:
    """从 start_port 起逐个探测，返回第一个可用端口。

    探测 = 连接预检（感知任何监听者，含通配绑定，§23-D1）+ 试绑复核。
    Windows 下 SO_REUSEADDR 不阻止重复绑定，改用 SO_EXCLUSIVEADDRUSE
    （8.4-6 端口冲突回退的探测正确性依赖此语义）。
    """
    for offset in range(attempts):
        port = start_port + offset
        if _port_has_listener(port):
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.setsockopt(socket.SOL_SOCKET, _SO_EXCLUSIVEADDRUSE, 1)
            except (AttributeError, OSError):
                pass  # 非 Windows 无此选项，默认语义已足够
            try:
                sock.bind((host, port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"no available port from {start_port} to {start_port + attempts - 1}")


_shutdown_handler: Callable[[], None] | None = None


def register_shutdown_handler(handler: Callable[[], None] | None) -> None:
    """登记优雅停机回调（run_server 启动时注册置 uvicorn Server.should_exit 的闭包）。

    进程内单例注册表：重复注册以最后一次为准（dev/frozen 各注册一次，
    互不叠加）；传 None 反注册（测试与 docker 直跑场景的诚实语义依赖此）。
    """
    global _shutdown_handler
    _shutdown_handler = handler


def request_shutdown() -> bool:
    """触发优雅停机；无回调注册（如 docker 直跑 uvicorn）时返回 False。"""
    handler = _shutdown_handler
    if handler is None:
        return False
    handler()
    return True


def redirect_std_streams(log_file: Path) -> None:
    """--noconsole 下 stdout/stderr 为 None，重定向到日志文件（§8.4-3）。

    structlog 的 PrintLoggerFactory 与 uvicorn 默认日志都走这两条流，
    重定向后统一落文件；行缓冲便于 DebugView/tail 实时排错。
    """
    log_file.parent.mkdir(parents=True, exist_ok=True)
    stream = open(log_file, "a", encoding="utf-8", buffering=1)
    sys.stdout = stream
    sys.stderr = stream


def read_runtime_port(port_file: Path) -> int | None:
    try:
        return int(port_file.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def write_runtime_port(port_file: Path, port: int) -> None:
    port_file.parent.mkdir(parents=True, exist_ok=True)
    port_file.write_text(str(port), encoding="utf-8")


def wait_until_port_ready(host: str, port: int, timeout: float = 15.0) -> bool:
    """TCP 连通即返回 True，超时返回 False（慢机场景由调用方决定重试）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def resolve_bind_host(configured: str, *, frozen: bool, explicit_env: str | None) -> str:
    """冻结环境默认绑 127.0.0.1（防火墙弹窗 + WebCodecs 安全上下文双重理由，
    §9.3-5）；BACKEND_HOST 环境变量显式设置时尊重用户选择。"""
    if explicit_env:
        return explicit_env
    if frozen:
        return "127.0.0.1"
    return configured