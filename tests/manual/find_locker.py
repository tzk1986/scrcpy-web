"""用 Restart Manager API 列出锁定指定路径的进程（排障工具，不入 CI）。

用法: python tests/manual/find_locker.py <绝对路径>
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

kernel32 = ctypes.windll.kernel32
rstr = ctypes.WinDLL("rstrtmgr")

CCH_RM_SESSIONGuid = 36


class RM_UNIQUE_PROCESS(ctypes.Structure):
    _fields_ = [("dwProcessId", wintypes.DWORD), ("rftStartTime", ctypes.c_longlong)]


class RM_PROCESS_INFO(ctypes.Structure):
    _fields_ = [
        ("Process", RM_UNIQUE_PROCESS),
        ("strAppName", ctypes.c_wchar * 256),
        ("strServiceShortName", ctypes.c_wchar * 64),
        ("ApplicationType", ctypes.c_int),
        ("AppStatus", ctypes.c_uint),
        ("TSSessionId", wintypes.DWORD),
        ("bRestartable", wintypes.BOOL),
    ]


def main(path: str) -> int:
    session = wintypes.DWORD()
    key = ctypes.create_unicode_buffer(CCH_RM_SESSIONGuid + 1)
    if rstr.RmStartSession(ctypes.byref(session), 0, key) != 0:
        print("RmStartSession failed")
        return 1
    try:
        paths = (ctypes.c_wchar_p * 1)(path)
        rc = rstr.RmRegisterResources(session, 1, paths, 0, None, 0, None)
        if rc != 0:
            print(f"RmRegisterResources failed rc={rc}")
            return 1
        needed = wintypes.UINT()
        fetched = wintypes.UINT()
        blocked = wintypes.BOOL()
        rc = rstr.RmGetList(session, ctypes.byref(needed), ctypes.byref(fetched), None, ctypes.byref(blocked))
        if rc == 0 and fetched.value == 0:
            print(f"{path}: no locking processes found")
            return 0
        arr = (RM_PROCESS_INFO * max(needed.value, 1))()
        rc = rstr.RmGetList(session, ctypes.byref(needed), ctypes.byref(fetched), arr, ctypes.byref(blocked))
        if rc != 0:
            print(f"RmGetList failed rc={rc}")
            return 1
        print(f"{path}: {fetched.value} locking process(es), blocked={blocked.value}")
        for i in range(fetched.value):
            pi = arr[i]
            print(f"  PID {pi.Process.dwProcessId}: {pi.strAppName} (service={pi.strServiceShortName}, type={pi.ApplicationType})")
    finally:
        rstr.RmEndSession(session)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
