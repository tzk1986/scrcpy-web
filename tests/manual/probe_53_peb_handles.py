"""远程探针（一次性）：读指定 PID 的 PEB 标准句柄并验证有效性。

判读：
- StandardInput 值非 0 且 DuplicateHandle 失败(ERROR_INVALID_HANDLE=6)
  → PEB 悬垂：进程内有人 CloseHandle 了该句柄，之后所有
    subprocess(stdin=None 继承) 的 SetHandleInformation 必报 WinError 6。
- DuplicateHandle 成功 → 句柄仍在表内，投毒另有位置（需再查）。

用法：build/venv/Scripts/python.exe tests/manual/probe_53_peb_handles.py <pid>
"""
import ctypes
import struct
import sys
from ctypes import wintypes

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
nt = ctypes.WinDLL("ntdll", use_last_error=True)
k32.OpenProcess.restype = wintypes.HANDLE
k32.GetCurrentProcess.restype = wintypes.HANDLE

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
PROCESS_DUP_HANDLE = 0x0040
DUPLICATE_SAME_ACCESS = 0x2
ERROR_INVALID_HANDLE = 6
ERROR_ALREADY_EXISTS = 183
STATUS_INFO_LENGTH_MISMATCH = 0xC0000004


class PROCESS_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("Reserved1", ctypes.c_void_p),
        ("PebBaseAddress", ctypes.c_void_p),
        ("Reserved2", ctypes.c_void_p * 2),
        ("UniqueProcessId", ctypes.c_void_p),
        ("Reserved3", ctypes.c_void_p),
    ]


def read_mem(h, addr, size):
    buf = (ctypes.c_char * size)()
    done = ctypes.c_size_t()
    if not k32.ReadProcessMemory(h, ctypes.c_void_p(addr), buf, size,
                                 ctypes.byref(done)) or done.value != size:
        raise OSError(f"ReadProcessMemory@{addr:#x} failed: {ctypes.get_last_error()}")
    return bytes(buf)


TH32CS_SNAPMODULE = 0x8
TH32CS_SNAPMODULE32 = 0x10


class MODULEENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("th32ModuleID", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("GlCntUsage", wintypes.DWORD),
        ("th32Heap", wintypes.HANDLE),
        ("modBaseAddr", ctypes.c_void_p),
        ("modBaseSize", wintypes.DWORD),
        ("hModule", wintypes.HMODULE),
        ("szModule", wintypes.WCHAR * 256),
        ("szExePath", wintypes.WCHAR * 260),
    ]


def first_module_base(pid: int):
    snap = k32.CreateToolhelp32Snapshot(
        TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32, pid)
    if snap == -1 or not snap:
        return None, f"snapshot err={ctypes.get_last_error()}"
    me = MODULEENTRY32W()
    me.dwSize = ctypes.sizeof(me)
    if not k32.Module32FirstW(snap, ctypes.byref(me)):
        k32.CloseHandle(snap)
        return None, f"Module32FirstW err={ctypes.get_last_error()}"
    base = me.modBaseAddr
    name = me.szModule
    k32.CloseHandle(snap)
    return (base, name), None


def shared_user_std_handles():
    """本进程读 KUSER_SHARED_DATA 的 ProcessStandardHandle(+0x58, x64)。"""
    ua = ctypes.c_void_p.from_address(0x7FFE0000 + 0x58)
    va = ctypes.c_void_p.from_address(0x7FFE0000 + 0x60)
    ve = ctypes.c_void_p.from_address(0x7FFE0000 + 0x68)
    return ua.value or 0, va.value or 0, ve.value or 0


def main() -> int:
    pid = int(sys.argv[1])
    h = k32.OpenProcess(
        PROCESS_QUERY_INFORMATION | PROCESS_VM_READ | PROCESS_DUP_HANDLE, False, pid)
    if not h:
        print(f"OpenProcess({pid}) failed err={ctypes.get_last_error()}")
        return 2

    pbi = PROCESS_BASIC_INFORMATION()
    ret_len = ctypes.c_ulong()
    st = nt.NtQueryInformationProcess(h, 0, ctypes.byref(pbi), ctypes.sizeof(pbi),
                                      ctypes.byref(ret_len))
    if st != 0:
        print(f"NtQueryInformationProcess status={st:#x}")
        return 2
    peb = pbi.PebBaseAddress
    # PEB+0x20 (x64): ProcessParameters；PEB+0x10: ImageBaseAddress（自校验用）
    params = struct.unpack("<Q", read_mem(h, peb + 0x20, 8))[0]
    peb_imgbase = struct.unpack("<Q", read_mem(h, peb + 0x10, 8))[0]
    # x64 RTL_USER_PROCESS_PARAMETERS: +0x28/0x30/0x38 StandardInput/Output/Error
    in_h, out_h, err_h = struct.unpack("<QQQ", read_mem(h, params + 0x28, 24))
    print(f"pid={pid} PEB={peb:#x} params={params:#x}")
    print(f"self-check: PEB.ImageBase=0x{peb_imgbase:x}")
    raw = read_mem(h, params, 0x80)
    for off in range(0, 0x80, 0x10):
        qw = struct.unpack_from("<QQ", raw, off)
        print(f"params+0x{off:02x}: {qw[0]:#018x} {qw[1]:#018x}")
    mod, err = first_module_base(pid)
    if mod:
        base, name = mod
        print(f"self-check: toolhelp first module {name} base=0x{(base or 0):x} "
              f"match={bool(base) and base == peb_imgbase}")
    else:
        print(f"self-check: toolhelp failed {err}")
    su = shared_user_std_handles()
    print(f"SharedUserData(in-proc,非目标) stdin=0x{su[0]:x} "
          f"stdout=0x{su[1]:x} stderr=0x{su[2]:x}")
    print(f"PEB handles: stdin=0x{in_h:x} stdout=0x{out_h:x} stderr=0x{err_h:x}")

    cur = ctypes.c_void_p(-1)  # GetCurrentProcess 伪句柄，必须按 64 位传
    dup = wintypes.HANDLE()
    for name, hv in (("stdin", in_h), ("stdout", out_h), ("stderr", err_h)):
        ctypes.set_last_error(0)
        ok = k32.DuplicateHandle(h, ctypes.c_void_p(hv), cur, ctypes.byref(dup),
                                 0, False, DUPLICATE_SAME_ACCESS)
        err = ctypes.get_last_error()
        verdict = ("ALIVE" if ok else
                   "DEAD(INVALID_HANDLE)->PEB悬垂实锤" if err == ERROR_INVALID_HANDLE
                   else f"DEAD(err={err})")
        print(f"{name}: 0x{hv:x} DuplicateHandle -> {verdict}")
        if ok:
            k32.CloseHandle(dup)
    k32.CloseHandle(h)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
