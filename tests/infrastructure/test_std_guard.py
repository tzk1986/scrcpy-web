"""std_guard 自愈单测（方案 53 B 层，Windows-only）。"""
import subprocess
import sys
from pathlib import Path

import pytest

_BACKEND = str(Path(__file__).resolve().parents[2] / "backend")

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")

# 子进程脚本：悬垂化 STD_INPUT——把 PEB 标准输入项置为一个句柄表中不存在
# 的值（等价于投毒现场「PEB 值保留、表项已关」；比 CloseHandle 真实句柄更
# 确定，避免槽位复用让 DuplicateHandle 假成功）。
_CHILD = r"""
import ctypes, subprocess, sys
from ctypes import wintypes
sys.path.insert(0, r"__BACKEND__")
from app.infrastructure.adb.std_guard import repair_std_handles

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.GetStdHandle.restype = ctypes.c_void_p
k32.SetStdHandle.restype = wintypes.BOOL
k32.SetStdHandle(-10, ctypes.c_void_p(0x11111114))

try:
    # 与应用同款混合形态：stdin 继承 + stdout/stderr PIPE → 走继承分支
    subprocess.run([sys.executable, "-c", "print(1)"], capture_output=True,
                   timeout=30)
    print("PRE-REPAIR SPAWN OK (poison not effective)")
except OSError as e:
    print(f"PRE-REPAIR SPAWN FAIL {e!r}")

repaired = repair_std_handles()
print(f"REPAIRED {repaired}")

p = subprocess.run([sys.executable, "-c", "print(42)"], capture_output=True,
                   timeout=30)
print(f"POST-REPAIR rc={p.returncode} out={p.stdout.strip().decode().strip()}")
dup = wintypes.HANDLE()
ok = k32.DuplicateHandle(ctypes.c_void_p(-1), ctypes.c_void_p(k32.GetStdHandle(-10) or 0),
                         ctypes.c_void_p(-1), ctypes.byref(dup), 0, False, 2)
print(f"STDIN_NOW {'ALIVE' if ok else 'DEAD'}")
"""


def test_repair_recovers_dangling_stdin() -> None:
    proc = subprocess.run(
        [sys.executable, "-c", _CHILD.replace("__BACKEND__", _BACKEND)],
        stdin=subprocess.PIPE,
        capture_output=True,
        text=True,
        timeout=120,
        encoding="utf-8",
        errors="replace",
    )
    out = proc.stdout + proc.stderr
    assert "PRE-REPAIR SPAWN FAIL" in out, out
    assert "REPAIRED ['stdin']" in out, out
    assert "POST-REPAIR rc=0 out=42" in out, out
    assert "STDIN_NOW ALIVE" in out, out
