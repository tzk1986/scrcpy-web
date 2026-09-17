"""
ConPTY 伪终端进程适配器（Windows）
==================================

层：基础设施 → ADB。

把 pywinpty 的 PTY 适配为 InteractiveShell 所需的最小
asyncio.subprocess.Process 接口（stdout.read / stdin.write /
returncode / pid / wait / kill）。

为什么需要 ConPTY：
  - adb.exe 是 Windows 控制台程序。当宿主进程没有真实控制台
    （如 PyInstaller windowed 打包、服务化运行）时，其 stdin 以
    普通管道方式行为异常（终端控制序列丢失、Ctrl+C 语义缺失）。
  - ConPTY 给子进程一个伪控制台，stdin/stdout 走真实的控制台
    I/O 路径，输出统一为 Unicode（规避控制台代码页转码问题）。

注意：
  - 直接使用底层 PTY 类而非 PtyProcess：PtyProcess 的 socket
    读线程在进程退出后 EOF 传播延迟可达数秒，且非阻塞模式下有
    '0011Ignore' 哨兵包粘连风险。PTY.read(blocking=False) 一次
    返回全部可用输出（str），无数据立即返回空串，这里用线程池
    轮询包装成 awaitable read()。
  - ConPTY 没有 stdin EOF 语义：stdin.close() 为 no-op，进程退出
    由 wait 超时后的 kill 兜底（或显式 terminate）。
"""
import asyncio
import os
import signal
import subprocess
import sys
from shutil import which
from typing import Sequence

from app.core.logging import get_logger

logger = get_logger(__name__)

_READ_POLL_INTERVAL = 0.02  # ConPTY 非阻塞 read 的轮询间隔（秒）


class ConPtyUnavailableError(Exception):
    """当前环境无法使用 ConPTY（非 Windows 或未安装 pywinpty）。"""


def conpty_supported() -> bool:
    """平台与依赖检查：仅 Windows + 已安装 pywinpty 时可用。"""
    if sys.platform != "win32":
        return False
    try:
        import winpty  # noqa: F401
        return True
    except ImportError:
        return False


class _ConPtyStdout:
    """asyncio 风格 stdout：read(n) 返回 bytes，进程退出且无数据时返回 b''（EOF）。"""

    def __init__(self, proc: "ConPtyProcess"):
        self._proc = proc

    def _read_nb(self) -> str:
        # PTY.read 第一个位置参数是 blocking
        return self._proc.pty.read(False)

    async def read(self, n: int = 4096) -> bytes:
        # ConPTY 非阻塞读一次返回全部可用数据，忽略 n 上限
        loop = asyncio.get_event_loop()
        while True:
            text = await loop.run_in_executor(None, self._read_nb)
            if text:
                # ConPTY 内部是 Unicode；还原为 bytes 供上层按 UTF-8 解码
                return text.encode("utf-8", errors="replace")
            if not self._proc.pty.isalive():
                # 退出后排空残余输出
                drained = ""
                while True:
                    tail = await loop.run_in_executor(None, self._read_nb)
                    if not tail:
                        break
                    drained += tail
                return drained.encode("utf-8", errors="replace") if drained else b""
            await asyncio.sleep(_READ_POLL_INTERVAL)


class _ConPtyStdin:
    """asyncio 风格 stdin：write(bytes)。ConPTY 无 EOF，close 为 no-op。"""

    def __init__(self, proc: "ConPtyProcess"):
        self._proc = proc

    def write(self, data: bytes):
        self._proc.pty.write(data.decode("utf-8", errors="replace"))

    async def drain(self):
        pass  # pywinpty.write 为同步写入

    def close(self):
        pass  # ConPTY 无 stdin EOF 语义；终止由 kill/terminate 负责


class ConPtyProcess:
    """伪控制台子进程，最小实现 asyncio.subprocess.Process 所需接口。"""

    def __init__(self, pty):
        self.pty = pty
        self.stdin = _ConPtyStdin(self)
        self.stdout = _ConPtyStdout(self)

    @property
    def pid(self) -> int:
        return self.pty.pid

    @property
    def returncode(self) -> int | None:
        if self.pty.isalive():
            return None
        es = self.pty.get_exitstatus()
        return es if es is not None else -1

    async def wait(self) -> int:
        while self.pty.isalive():
            await asyncio.sleep(0.05)
        return self.returncode

    def kill(self):
        try:
            # Windows 上 os.kill(pid, 非 CTRL_* 信号) 即 TerminateProcess（强杀）
            os.kill(self.pty.pid, signal.SIGTERM)
        except Exception as e:
            logger.warning("conpty_kill_failed", error=str(e))

    def terminate(self):
        # Windows 无优雅终止语义，与 kill 等价（TerminateProcess）
        self.kill()


async def spawn_conpty(argv: Sequence[str],
                       dimensions: tuple[int, int] = (24, 80)) -> ConPtyProcess:
    """
    在 ConPTY 伪控制台中启动子进程。

    参数：
        argv: 命令与参数。
        dimensions: (rows, cols)，与设备 PTY 的 80x24 对齐。

    异常：
        ConPtyUnavailableError: 非 Windows 或缺 pywinpty。
        FileNotFoundError: 命令不存在（与 create_subprocess_exec 语义一致）。
    """
    if not conpty_supported():
        raise ConPtyUnavailableError("ConPTY requires Windows with pywinpty installed")
    from winpty import PTY

    argv = list(argv)
    command = which(argv[0]) or argv[0]
    if not os.path.exists(command):
        raise FileNotFoundError(f"command not found: {argv[0]}")
    cmdline = " " + subprocess.list2cmdline(argv[1:]) if len(argv) > 1 else None
    rows, cols = dimensions

    def _spawn() -> ConPtyProcess:
        pty = PTY(cols, rows)
        ok = pty.spawn(command, cwd=None, cmdline=cmdline) if cmdline \
            else pty.spawn(command, cwd=None)
        if not ok:
            raise OSError(f"conpty spawn failed: {argv[0]}")
        return ConPtyProcess(pty)

    loop = asyncio.get_event_loop()
    proc = await loop.run_in_executor(None, _spawn)
    logger.info("conpty_process_spawned", pid=proc.pid, argv=argv[:4])
    return proc
