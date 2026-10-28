"""v0.7.15 投毒机制 spike C：真句柄继承 + ConPTY teardown → FreeConsole 悬垂实锤。

机制假设（winpty-rs/src/pty/conpty/pty_impl.rs 已读）：
  ConPTY 后端构造时对无控制台进程 AllocConsole()（成功则 console_allocated=True），
  teardown（Drop 或子进程退出触发的 cleanup 线程）调用 FreeConsole()。
  FreeConsole 关闭进程 STD_INPUT/STD_OUTPUT 的句柄表项，但 PEB 里的旧值保留
  → 悬垂。此后一切 stdin=None 继承分支的 subprocess spawn
  （_get_handles→GetStdHandle 返回旧值→DuplicateHandle）永久 WinError 6。

为何 spike A/B 未复现：pythonw-from-bash 继承控制台伪句柄，AllocConsole
分支未生效/伪句柄关不掉；本 spike 复刻 tessoa→OpenScrcpy.exe 拓扑——
父进程 Popen(stdin=PIPE, stdout=PIPE, stderr=PIPE) 拉起 build/venv pythonw
子进程（三个真 kernel 句柄），子进程走与 InteractiveShell.stop() 同序列。

用法（3.10 build venv，父进程）：
    build/venv/Scripts/python.exe tests/manual/spike_53c_freeconsole_poison.py
可选 --device=IP:PORT。结果写 logs/spike_53c_out.txt（毒发后 stdout 管道本身
可能已失效，故用文件回传）。
"""
import os
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
OUT = _ROOT / "logs" / "spike_53c_out.txt"


def parent() -> int:
    pyw = Path(sys.executable).with_name("pythonw.exe")
    env = dict(os.environ)
    env.setdefault("ANDROID_ADB_SERVER_PORT", "5039")
    args = [str(pyw), str(__file__), "--child"]
    args += [a for a in sys.argv[1:] if a.startswith(("--device=", "--mode="))]
    proc = subprocess.Popen(
        args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, cwd=str(_ROOT), env=env,
    )
    try:
        pipe_out, _ = proc.communicate(timeout=120)
    except subprocess.TimeoutExpired:
        proc.kill()
        pipe_out, _ = proc.communicate()
    if OUT.exists():
        text = OUT.read_text(encoding="utf-8", errors="replace")
    else:
        text = f"(no OUT file) pipe rc={proc.returncode}\n{pipe_out!r}"
    print(text)
    return 0


def child() -> None:
    import asyncio
    import ctypes
    import gc
    from ctypes import wintypes

    sys.path.insert(0, str(_ROOT / "backend"))
    sys.path.insert(0, str(_ROOT))
    lines: list[str] = []

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    k32.GetStdHandle.restype = ctypes.c_void_p
    k32.GetConsoleWindow.restype = ctypes.c_void_p
    k32.AllocConsole.restype = wintypes.BOOL
    k32.FreeConsole.restype = wintypes.BOOL
    STD = ((-10, "STDIN"), (-11, "STDOUT"), (-12, "STDERR"))
    mode = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--mode=")),
                "conpty")

    def console(tag: str) -> None:
        lines.append(f"{tag} GetConsoleWindow=0x{k32.GetConsoleWindow() or 0:x}")

    def snap(tag: str) -> dict[int, int]:
        vals = {}
        for idx, name in STD:
            hv = k32.GetStdHandle(idx) or 0
            ctypes.set_last_error(0)
            dup = wintypes.HANDLE()
            ok = k32.DuplicateHandle(
                ctypes.c_void_p(-1), ctypes.c_void_p(hv),
                ctypes.c_void_p(-1), ctypes.byref(dup),
                0, False, 0x2)
            err = ctypes.get_last_error()
            if ok:
                k32.CloseHandle(dup)
            vals[idx] = hv
            lines.append(f"{tag} {name}: handle=0x{hv:x} local_dup={'ALIVE' if ok else f'DEAD(err={err})'}")
        return vals

    async def probe(tag: str) -> bool:
        try:
            p = await asyncio.create_subprocess_exec(
                sys.executable, "-c", "print(1)",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(p.communicate(), timeout=10)
            lines.append(f"{tag} spawn: OK rc={p.returncode}")
            return True
        except OSError as e:
            lines.append(f"{tag} spawn: OSError {e!r}")
            return False

    async def teardown(proc, tag: str) -> None:
        reader = asyncio.ensure_future(proc.stdout.read(4096))
        reader.cancel()
        try:
            await reader
        except asyncio.CancelledError:
            pass
        proc.stdin.close()
        try:
            await asyncio.wait_for(proc.wait(), timeout=3.0)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
        lines.append(f"{tag}: waited/killed, dropping refs + gc")
        del proc
        gc.collect()
        await asyncio.sleep(0.5)
        gc.collect()
        await asyncio.sleep(0.5)

    async def main() -> None:
        lines.append(f"python {sys.version.split()[0]} executable={sys.executable} "
                     f"mode={mode}")
        before = snap("BEFORE")
        console("BEFORE")
        await probe("BEFORE")

        if mode == "raw":
            ok = k32.AllocConsole()
            lines.append(f"AllocConsole -> {bool(ok)} err={ctypes.get_last_error()}")
            console("AFTER-ALLOC")
            snap("AFTER-ALLOC")
            await asyncio.sleep(0.5)
            ok = k32.FreeConsole()
            lines.append(f"FreeConsole -> {bool(ok)} err={ctypes.get_last_error()}")
            console("AFTER-FREE")
        else:
            from app.infrastructure.adb.winpty import spawn_conpty
            adb = str(_ROOT / "dist" / "OpenScrcpy" / "_internal" / "tools" / "adb.exe")
            device = next((a.split("=", 1)[1] for a in sys.argv
                           if a.startswith("--device=")), "192.168.8.18:5555")
            proc = await spawn_conpty([adb, "-s", device, "shell"])
            lines.append(f"conpty spawned pid={proc.pid}")
            await asyncio.sleep(1.0)
            proc.pty.set_size(60, 39)
            console("DURING-PTY")
            await teardown(proc, "TEARDOWN")

        after = snap("AFTER")
        console("AFTER")
        lines.append(f"handle values unchanged: {before == after}")
        ok_all = True
        for i in range(5):
            ok = await probe(f"AFTER-{i}")
            ok_all = ok_all and ok
            await asyncio.sleep(0.3)
        lines.append("VERDICT: " + (
            "REPRODUCED (std poisoned after teardown w/ real inherited handles)"
            if not ok_all else "NOT REPRODUCED in this run"))
        OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")

    try:
        asyncio.run(main())
    except BaseException as e:  # pythonw 无 stderr，异常也必须落文件
        lines.append(f"FATAL: {e!r}")
        import traceback
        lines.append(traceback.format_exc())
        OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
        raise


if __name__ == "__main__":
    if "--child" in sys.argv:
        child()
    else:
        raise SystemExit(parent())
