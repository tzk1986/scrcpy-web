"""
交互式 Shell 会话（PTY 模式）
==============================

层：基础设施 → ADB。

通过 `adb shell -tt` 创建持久化交互式 shell。

数据流：
  用户按键 → WebSocket → backend → adb stdin → 设备 PTY
  用户看到 ← WebSocket ← backend ← adb stdout ← 设备 PTY

架构：
  - 后台读取器（_background_reader）：持续读取 stdout，路由到队列
  - execute() 期间：输出 → _exec_queue（命令专用）
  - 其他时间：输出 → _output_queue（PTY 转发）
  - asyncio.Lock 序列化 stdin 写入

功能：
  - 持久化 shell 会话（cd/su 状态保持）
  - 完整终端功能（Ctrl+C、Tab、↑↓ 历史、readline）
  - 交互式命令支持（top、vi、less）
  - 窗口大小固定 80x24（与设备 PTY 对齐）

技术细节：
  - 使用 `adb shell -tt` 强制分配 PTY（即使 stdin 不是 TTY）
  - 命令后追加标记（marker）检测命令完成
  - 安全清理：close stdin → wait → kill（避免 Windows 资源泄漏）

限制：
  - 仅支持 PTY 模式（无 pipe 模式降级）
  - 命令必须串行执行（asyncio.Lock）
  - 读取超时 30s（支持长时间运行的命令）
"""

import asyncio
import re
import uuid
from typing import AsyncIterator

from app.core.config import settings
from app.core.exceptions import AdbError
from app.core.logging import get_logger
from app.domain.ports import ShellSession

logger = get_logger(__name__)


class ShellExitedError(Exception):
    """Shell 进程意外退出"""

    pass


class InteractiveShell:
    """
    持久化交互式 shell 会话（PTY 模式）。

    实现 domain.ports.ShellSession 协议。

    输出路由：
      stdout → _background_reader → _exec_queue（execute 期间）
                                  → _output_queue（其他时间）
    """

    READ_TIMEOUT = 30.0  # 读取超时（支持长时间运行的命令）

    def __init__(self):
        """从配置初始化 ADB 二进制路径。"""
        self._adb_path = settings().adb.path
        self._proc: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._device_id: str | None = None
        # 输出路由
        self._output_queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._exec_queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._executing = False
        self._reader_task: asyncio.Task | None = None

    @property
    def is_alive(self) -> bool:
        """检查 shell 进程是否存活"""
        return self._proc is not None and self._proc.returncode is None

    async def start(self, device_id: str, initial_output_callback=None):
        """
        启动 adb shell -tt（强制 PTY）。

        流程：
          1. 创建子进程
          2. 读取初始欢迎消息（直到看到 prompt）
          3. 启动后台读取器（持续读取 stdout）

        参数：
            device_id: ADB 序列号。
            initial_output_callback: 可选回调，接收初始输出（用于显示 prompt）。

        异常：
            AdbError: 启动失败时。
        """
        self._device_id = device_id
        self._initial_output = ""  # 存储初始输出（包括 prompt）

        try:
            self._proc = await asyncio.create_subprocess_exec(
                self._adb_path,
                "-s", device_id,
                "shell", "-tt",  # 强制 PTY 分配
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            logger.error("adb_not_found", path=self._adb_path)
            raise AdbError(
                f"ADB not found at '{self._adb_path}'. "
                "Please install Android SDK Platform Tools and ensure it's in PATH."
            )
        except Exception as e:
            logger.error("shell_start_failed", device=device_id, error=str(e))
            raise AdbError(f"Failed to start interactive shell: {e}")

        logger.info("interactive_shell_started", device=device_id, pid=self._proc.pid)

        # 读取初始欢迎消息（直到看到第一个 prompt）
        # 必须在后台读取器启动前完成，避免竞争初始输出
        await self._read_until_prompt(initial_output_callback)

        # 启动后台读取器（持续读取 stdout 并路由到队列）
        self._reader_task = asyncio.create_task(self._background_reader())

    async def _read_until_prompt(self, callback=None):
        """读取初始输出，直到看到 shell prompt，并转发给回调。

        超时策略：每次 read 最多等待 5s（网络设备延迟较高）。
        循环读取直到检测到 prompt 或超时。
        """
        try:
            while True:
                chunk = await asyncio.wait_for(
                    self._proc.stdout.read(4096),
                    timeout=5.0
                )
                if not chunk:
                    break
                text = chunk.decode("utf-8", errors="replace")
                self._initial_output += text
                logger.info("initial_output_chunk", text=repr(text), total_len=len(self._initial_output))
                # 转发给回调（用于显示初始 prompt）
                if callback:
                    callback(text)
                # 检测到 prompt（如 `rk3288:/ $ ` 或 `shell@rk3288:~ $ `
                # 也支持 root 的 `# ` prompt）
                if "$ " in text or "# " in text:
                    logger.info("shell_prompt_detected", text=text.strip())
                    break
        except asyncio.TimeoutError:
            logger.info("initial_output_timeout", total_len=len(self._initial_output))

    async def _background_reader(self):
        """
        后台任务：持续读取 stdout 并路由到适当的队列。

        使用 read(4096) 而非 readline()，因为 PTY 输出可能没有
        干净的换行符（使用 \\r 进行光标定位），readline() 会阻塞
        直到收到 \\n。

        路由规则：
          - execute() 期间（_executing=True）→ _exec_queue
          - 其他时间 → _output_queue

        当 stdout 返回空数据（EOF）时退出。
        """
        while True:
            try:
                raw = await self._proc.stdout.read(4096)
            except Exception as e:
                logger.warning("stdout_read_error", error=str(e))
                break

            if not raw:
                # EOF — shell 进程已退出
                logger.info("shell_eof", device=self._device_id)
                break

            text = raw.decode("utf-8", errors="replace")

            if self._executing:
                # execute() 正在等待命令输出 → 发送到 exec 队列
                await self._exec_queue.put(text)
            else:
                # 正常 PTY 模式 → 发送到输出队列（供 WebSocket 转发）
                await self._output_queue.put(text)

        # Shell 退出时，通知等待者
        await self._exec_queue.put(None)
        await self._output_queue.put(None)
        logger.info("background_reader_exited", device=self._device_id)

    async def execute(self, cmd: str) -> AsyncIterator[str]:
        """
        执行命令并流式返回输出。

        使用标记机制检测命令完成。命令执行期间，后台读取器将输出
        路由到 _exec_queue，避免与 PTY 输出队列冲突。

        注意：由于使用 read(4096) 读取（非 readline），输出是数据块
        而非行。调用方需要自行处理行分割。

        参数：
            cmd: 要执行的 shell 命令。

        产出：
            输出数据块（UTF-8 字符串）。

        异常：
            ShellExitedError: 进程意外退出时。
        """
        async with self._lock:
            if not self.is_alive:
                raise ShellExitedError("Shell process not running")

            marker = f"__CMD_DONE_{uuid.uuid4().hex[:8]}__"
            # 清空 exec 队列（避免上一次命令的残留数据）
            while not self._exec_queue.empty():
                try:
                    self._exec_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

            # 设置执行状态（后台读取器会将输出路由到 _exec_queue）
            self._executing = True

            # 写入命令 + 标记（合并 stderr）
            line = f'{cmd} 2>&1 ; echo "\\n{marker}"\n'
            self._proc.stdin.write(line.encode())
            await self._proc.stdin.drain()

            try:
                while True:
                    try:
                        chunk = await asyncio.wait_for(
                            self._exec_queue.get(),
                            timeout=self.READ_TIMEOUT
                        )
                    except asyncio.TimeoutError:
                        logger.warning("command_timeout", cmd=cmd, timeout=self.READ_TIMEOUT)
                        yield f"[Command timed out after {self.READ_TIMEOUT}s]\n"
                        break

                    if chunk is None:
                        raise ShellExitedError("Shell process exited")

                    # 检测标记（可能在数据块中间）
                    if marker in chunk:
                        # 输出标记之前的部分（如果有）
                        before_marker = chunk.split(marker)[0]
                        if before_marker:
                            yield before_marker
                        break

                    yield chunk
            finally:
                # 清除执行状态（后续输出回到 _output_queue）
                self._executing = False

    async def send_input(self, data: bytes):
        """
        发送原始输入（按键）到 shell。

        参数：
            data: 原始字节数据（如按键序列）。

        异常：
            ShellExitedError: 进程已退出时。
        """
        async with self._lock:
            if not self.is_alive:
                raise ShellExitedError("Shell process not running")
            self._proc.stdin.write(data)
            await self._proc.stdin.drain()

    async def get_output(self) -> str | None:
        """
        从输出队列获取一行输出（阻塞）。

        用于 PTY 模式的输出转发。当 shell 退出时返回 None。

        返回：
            一行输出（UTF-8 字符串），或 None（EOF）。
        """
        return await self._output_queue.get()

    def get_initial_output(self) -> str:
        """
        获取初始输出（包括设备 prompt）。

        返回：
            初始输出字符串。
        """
        return self._initial_output

    async def stop(self):
        """
        安全关闭 shell 进程。

        先关闭 stdin，等待进程自然退出，超时则强制 kill。
        清理 stdout/stderr transport（避免 Windows 资源泄漏）。
        """
        if self._proc and self._proc.returncode is None:
            logger.info("stopping_shell", device=self._device_id)

            # 1. 取消后台读取器
            if self._reader_task and not self._reader_task.done():
                self._reader_task.cancel()
                try:
                    await self._reader_task
                except asyncio.CancelledError:
                    pass

            # 2. 关闭 stdin
            self._proc.stdin.close()
            try:
                # 3. 等待 3 秒让 shell 自然退出
                await asyncio.wait_for(self._proc.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                # 4. 超时则强制 kill
                self._proc.kill()
                await self._proc.wait()

            # 5. 清理 transport（避免 Windows 资源泄漏）
            try:
                self._proc.stdout._transport.close()
            except Exception:
                pass
            try:
                self._proc.stderr._transport.close()
            except Exception:
                pass
            self._proc = None
            logger.info("shell_stopped", device=self._device_id)

    def _is_prompt_line(self, text: str) -> bool:
        """
        检测是否为 shell prompt 行（命令回显）。

        示例：
          - `rk3288:/ $ pwd`
          - `shell@rk3288:~ $ pwd`
          - `rk3288:/sdcard $ ls`
        """
        # Prompt 格式：<hostname>:<path> $ <command>
        return bool(re.match(r'^[^\$]+\$ .+$', text))

    def _is_empty_prompt(self, text: str) -> bool:
        """
        检测是否为空 prompt 行。

        示例：
          - `rk3288:/ $ `
          - `shell@rk3288:~ $ `
        """
        return bool(re.match(r'^[^\$]+\$ $', text))
