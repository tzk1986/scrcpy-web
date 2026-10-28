"""标准句柄悬垂自愈（方案 53 B 层）。

frozen exe 内 native 路径（疑 pywinpty ConPTY teardown 的 FreeConsole）会
CloseHandle 掉进程 stdin/stdout 句柄表项而 PEB 值保留（悬垂），此后任何
继承分支的 spawn 永久 WinError 6。本模块仅把「已悬垂」的标准句柄替换为
NUL 设备句柄，活句柄绝不触碰。
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

_STD_INDICES = (-10, -11, -12)  # STD_INPUT/OUTPUT/ERROR_HANDLE
_STD_NAMES = ("stdin", "stdout", "stderr")
_ERROR_INVALID_HANDLE = 6


def repair_std_handles() -> list[str]:
    """检测并修复悬垂标准句柄，返回被修复项的名字列表。

    非 Windows 平台 no-op。判定：GetStdHandle 非 0/非 -1 且 DuplicateHandle
    失败(ERROR_INVALID_HANDLE) → 悬垂 → CreateFileW("NUL") 后 SetStdHandle 替换。
    """
    if sys.platform != "win32":
        return []
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    k32.GetStdHandle.restype = ctypes.c_void_p
    k32.SetStdHandle.restype = wintypes.BOOL
    k32.CreateFileW.restype = ctypes.c_void_p
    k32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    repaired: list[str] = []
    cur = ctypes.c_void_p(-1)  # GetCurrentProcess 伪句柄须按 64 位传
    for idx, name in zip(_STD_INDICES, _STD_NAMES):
        hv = k32.GetStdHandle(idx) or 0
        if hv in (0, ctypes.c_void_p(-1).value):
            continue
        dup = wintypes.HANDLE()
        ctypes.set_last_error(0)
        ok = k32.DuplicateHandle(ctypes.c_void_p(-1), ctypes.c_void_p(hv),
                                 cur, ctypes.byref(dup), 0, False, 0x2)
        if ok:
            k32.CloseHandle(dup)
            continue
        if ctypes.get_last_error() != _ERROR_INVALID_HANDLE:
            continue
        # GENERIC_READ|GENERIC_WRITE, FILE_SHARE_*, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL
        nul = k32.CreateFileW("NUL", 0xC0000000, 0x3, None, 3, 0x80, None)
        if not nul or nul == ctypes.c_void_p(-1).value:
            continue
        if k32.SetStdHandle(idx, ctypes.c_void_p(nul)):
            repaired.append(name)
    return repaired
