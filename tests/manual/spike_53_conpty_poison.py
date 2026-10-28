"""v0.7.15 ConPTY 关闭投毒复现 spike（一次性，pythonw 无控制台运行）。

背景：0.7.15 exe 打开调试终端（ConPTY shell）后关闭会话，进程内所有
asyncio.create_subprocess_exec 永久报 OSError [WinError 6] 句柄无效
（subprocess._get_handles → _make_inheritable，即继承用的 STD_INPUT_HANDLE
被关）。本 spike 在 pythonw（同为无控制台宿主、同 3.15.2 + pywinpty）下
走与 InteractiveShell.stop() 相同的 teardown 序列，前后对比：
  A) create_subprocess_exec(sys.executable) 是否开始 WinError 6；
  B) ctypes GetStdHandle(-10/-11/-12) 句柄值与其有效性（GetFileType）。
判读：若 teardown 后 B 中 STD_INPUT_HANDLE 值不变但 CloseHandle/GetFileType
判定失效、且 A 开始 WinError 6 → 复现成立，投毒点=ConPTY teardown。

运行：
    .venv/Scripts/pythonw.exe tests/manual/spike_53_conpty_poison.py
结果写 logs/spike_53_out.txt。
"""
import asyncio
import ctypes
import gc
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "backend"))
sys.path.insert(0, str(_ROOT))

OUT = Path(__file__).resolve().parents[2] / "logs" / "spike_53_out.txt"
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
CloseHandle = _kernel32.CloseHandle
GetFileType = _kernel32.GetFileType
GetStdHandle = _kernel32.GetStdHandle
GetLastError = ctypes.get_last_error


def std_snapshot(tag: str, lines: list[str]) -> dict[int, int]:
    handles = {}
    for idx, name in ((-10, "STD_INPUT"), (-11, "STD_OUTPUT"), (-12, "STD_ERROR")):
        h = GetStdHandle(idx)
        hval = h if isinstance(h, int) else ctypes.cast(h, ctypes.c_void_p).value or 0
        # 有效性探测：FILE_TYPE_CHAR=2/PIPE=1/DISK=0；无效句柄返回 0 且 err=ERROR_INVALID_HANDLE(6)
        _kernel32.SetLastError(0)
        ftype = GetFileType(hval)
        err = GetLastError()
        handles[idx] = hval
        lines.append(f"{tag} {name}: handle=0x{hval:x} filetype={ftype} err={err}")
    return handles


async def probe_spawn(tag: str, lines: list[str]) -> bool:
    """spawn 一次无害子进程；返回是否成功。"""
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", "print(1)",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(proc.communicate(), timeout=10)
        lines.append(f"{tag} spawn: OK rc={proc.returncode}")
        return True
    except OSError as e:
        lines.append(f"{tag} spawn: OSError {e!r}")
        return False


async def teardown_like_shell_stop(proc, lines: list[str]) -> None:
    """复刻 InteractiveShell.stop() 对 ConPtyProcess 的清理序列。"""
    reader = asyncio.ensure_future(proc.stdout.read(4096))
    reader.cancel()
    try:
        await reader
    except asyncio.CancelledError:
        pass
    proc.stdin.close()  # ConPTY: no-op
    try:
        await asyncio.wait_for(proc.wait(), timeout=3.0)
    except asyncio.TimeoutError:
        proc.kill()  # taskkill /T /F
        await proc.wait()
    lines.append("teardown: waited/killed, dropping refs + gc")
    proc.pty.close() if hasattr(proc.pty, "close") else None
    del proc
    gc.collect()
    await asyncio.sleep(0.5)
    gc.collect()


async def main() -> None:
    lines: list[str] = []
    from app.infrastructure.adb.winpty import spawn_conpty

    lines.append(f"python {sys.version.split()[0]} executable={sys.executable} "
                 f"stdout={sys.stdout!r}")
    before = std_snapshot("BEFORE", lines)
    await probe_spawn("BEFORE", lines)

    # 与用户场景完全对齐：真机 adb shell（有持续 prompt 输出流，reader 会卡在
    # executor 里真读），再走同样的超时→kill teardown
    adb = str(_ROOT / "dist" / "OpenScrcpy" / "_internal" / "tools" / "adb.exe")
    device = next((a.split("=")[1] for a in sys.argv if a.startswith("--device=")),
                  "192.168.8.18:5555")
    proc = await spawn_conpty([adb, "-s", device, "shell"])
    lines.append(f"conpty spawned pid={proc.pid}")
    await asyncio.sleep(1.0)
    await teardown_like_shell_stop(proc, lines)

    after = std_snapshot("AFTER", lines)
    if before == after:
        lines.append("std handle VALUES unchanged after teardown "
                     "(若 spawn 失败→句柄值未换但目标被关=PEB 悬垂)")
    else:
        lines.append(f"std handle VALUES CHANGED: {before} -> {after}")

    ok_all = True
    for i in range(5):
        ok = await probe_spawn(f"AFTER-{i}", lines)
        ok_all = ok_all and ok
        await asyncio.sleep(0.3)

    lines.append("VERDICT: " + ("REPRODUCED (WinError 6 after conpty teardown)"
                 if not ok_all else "NOT REPRODUCED in this run"))
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
