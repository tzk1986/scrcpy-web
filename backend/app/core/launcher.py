"""
绿色版启动器辅助（方案 15 §8.5 任务 3 / §8.4-2/3/6、§9.3-5）
=============================================================

仅在 PyInstaller 冻结环境（sys.frozen）使用：文件日志重定向、
SetDllDirectoryW 修复、单实例互斥、端口探测回退、就绪轮询后开浏览器。
源码运行（dev）不经过本模块，行为保持不变。
"""

from __future__ import annotations

import ctypes
import socket
import sys
import time
from pathlib import Path

_SINGLE_INSTANCE_MUTEX = "OpenScrcpy-SingleInstance-Mutex"
_ERROR_ALREADY_EXISTS = 183


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
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW(None, False, name)
    return bool(kernel32.GetLastError() != _ERROR_ALREADY_EXISTS)


def find_available_port(host: str, start_port: int, attempts: int = 5) -> int:
    """从 start_port 起逐个试绑（探测后立即释放），返回第一个可用端口。

    Windows 下 SO_REUSEADDR 不阻止重复绑定，改用 SO_EXCLUSIVEADDRUSE
    （8.4-6 端口冲突回退的探测正确性依赖此语义）。
    """
    for offset in range(attempts):
        port = start_port + offset
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            except (AttributeError, OSError):
                pass  # 非 Windows 无此选项，默认语义已足够
            try:
                sock.bind((host, port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"no available port from {start_port} to {start_port + attempts - 1}")


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