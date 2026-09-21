"""
交互式 Shell 会话（PTY）测试
==============================

测试 InteractiveShell（infrastructure/adb/shell.py）的会话逻辑。

测试内容：
    - 纯逻辑：prompt 检测正则（_is_prompt_line / _is_empty_prompt）
    - is_alive 属性
    - 生命周期：start（读取初始 prompt / 回调 / 错误处理）
    - _read_until_prompt（EOF / root prompt / 超时吞掉）
    - 后台读取器路由（exec 队列 vs output 队列 / EOF 哨兵 / 读错误）
    - execute（标记机制 / 未存活 / 中途退出 / 超时）
    - send_input / get_output / get_initial_output
    - stop（正常关闭 / 等待超时 kill / 无进程 / 已退出幂等）
    - _spawn_process（ConPTY 分支 / 不支持 / 禁用 / 失败降级管道）

不依赖真实 adb / 子进程 / 设备：用假进程对象（鸭子类型）驱动异步路径。
"""

import asyncio
import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.exceptions import AdbError
from app.infrastructure.adb.shell import InteractiveShell, ShellExitedError


# ---------------------------------------------------------------------------
# 假进程工具（鸭子类型，匹配 InteractiveShell 用到的最小接口）
# ---------------------------------------------------------------------------

class _FakeStdout:
    """最小 stdout 替身：read(n) 依次返回预置块，用尽后 EOF（b""）。

    若某块是异常实例，则 raise 它（用于模拟读错误 / 超时）。
    """

    def __init__(self, chunks):
        self._chunks = list(chunks)

    async def read(self, n: int = -1) -> bytes:
        if not self._chunks:
            return b""
        item = self._chunks.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class _FakeStdin:
    """最小 stdin 替身：记录写入；write 时可选地回调（用于注入命令响应）。"""

    def __init__(self, on_write=None):
        self.written = bytearray()
        self.closed = False
        self._on_write = on_write

    def write(self, data: bytes) -> None:
        self.written.extend(data)
        if self._on_write is not None:
            self._on_write(data)

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


def _make_proc(stdout_chunks=None, on_write=None, returncode=None):
    """构造假 adb shell 子进程。returncode=None 表示存活。"""
    proc = MagicMock()
    proc.pid = 4321
    proc.returncode = returncode
    proc.stdout = _FakeStdout(stdout_chunks or [])
    proc.stdin = _FakeStdin(on_write=on_write)
    # stderr 带 _transport=None，使 stop() 的 transport 清理走「跳过」分支
    proc.stderr = MagicMock()
    proc.stderr._transport = None
    proc.wait = AsyncMock(return_value=0)
    proc.kill = MagicMock()
    return proc


def _feed_marker_responses(shell: InteractiveShell, *chunks: str):
    """构造 on_write 回调：从写入行解析随机 marker，并把响应块压入 exec 队列。

    execute() 写入 `cmd 2>&1 ; echo "\\n<marker>"`，marker 为随机 uuid，
    因此只能在写入时回读。put_nowait 为同步，发生在 execute 清队列之后。
    """
    def on_write(data: bytes) -> None:
        line = data.decode()
        m = re.search(r"__CMD_DONE_[0-9a-f]+__", line)
        assert m is not None, f"写入行未包含 marker: {line!r}"
        marker = m.group(0)
        for chunk in chunks:
            shell._exec_queue.put_nowait(chunk.replace("{marker}", marker))
    return on_write


# ---------------------------------------------------------------------------
# 纯逻辑：prompt 检测正则
# ---------------------------------------------------------------------------

class TestPromptDetection:
    """_is_prompt_line / _is_empty_prompt 为纯正则函数。"""

    def test_is_prompt_line_matches_command_echo(self):
        shell = InteractiveShell()
        assert shell._is_prompt_line("rk3288:/ $ pwd") is True
        assert shell._is_prompt_line("shell@rk3288:~ $ ls -l") is True
        assert shell._is_prompt_line("rk3288:/sdcard $ cat x") is True

    def test_is_prompt_line_rejects_non_prompt(self):
        shell = InteractiveShell()
        assert shell._is_prompt_line("just some output") is False
        # 空命令（$ 后无内容）不匹配 `.+`
        assert shell._is_prompt_line("rk3288:/ $ ") is False

    def test_is_empty_prompt_matches(self):
        shell = InteractiveShell()
        assert shell._is_empty_prompt("rk3288:/ $ ") is True
        assert shell._is_empty_prompt("shell@rk3288:~ $ ") is True

    def test_is_empty_prompt_rejects_command(self):
        shell = InteractiveShell()
        assert shell._is_empty_prompt("rk3288:/ $ pwd") is False
        assert shell._is_empty_prompt("plain output") is False


# ---------------------------------------------------------------------------
# is_alive
# ---------------------------------------------------------------------------

class TestIsAlive:
    def test_false_when_no_proc(self):
        assert InteractiveShell().is_alive is False

    def test_true_when_running(self):
        shell = InteractiveShell()
        shell._proc = _make_proc(returncode=None)
        assert shell.is_alive is True

    def test_false_when_exited(self):
        shell = InteractiveShell()
        shell._proc = _make_proc(returncode=0)
        assert shell.is_alive is False


# ---------------------------------------------------------------------------
# start：读取初始 prompt + 启动后台读取器
# ---------------------------------------------------------------------------

class TestStart:
    async def test_reads_prompt_and_invokes_callback(self):
        shell = InteractiveShell()
        collected: list[str] = []
        proc = _make_proc(stdout_chunks=[b"welcome\r\nrk3288:/ $ ", b""])
        with patch.object(
            InteractiveShell, "_spawn_process", new=AsyncMock(return_value=proc)
        ):
            await shell.start("emulator-5554", initial_output_callback=collected.append)
        try:
            assert "welcome" in shell.get_initial_output()
            assert "".join(collected) == "welcome\r\nrk3288:/ $ "
            assert shell._reader_task is not None
        finally:
            await shell.stop()

    async def test_adb_not_found_raises_adberror(self):
        shell = InteractiveShell()
        with patch.object(
            InteractiveShell, "_spawn_process", new=AsyncMock(side_effect=FileNotFoundError())
        ):
            with pytest.raises(AdbError, match="ADB not found"):
                await shell.start("emulator-5554")

    async def test_generic_spawn_failure_raises_adberror(self):
        shell = InteractiveShell()
        with patch.object(
            InteractiveShell, "_spawn_process", new=AsyncMock(side_effect=RuntimeError("boom"))
        ):
            with pytest.raises(AdbError, match="Failed to start interactive shell"):
                await shell.start("emulator-5554")


# ---------------------------------------------------------------------------
# _read_until_prompt：EOF / root prompt / 超时
# ---------------------------------------------------------------------------

class TestReadUntilPrompt:
    async def test_eof_breaks_loop(self):
        shell = InteractiveShell()
        shell._proc = _make_proc(stdout_chunks=[b""])  # 立即 EOF
        shell._initial_output = ""  # start() 正常会先初始化；此处直调需手动设
        await shell._read_until_prompt()
        assert shell._initial_output == ""

    async def test_root_hash_prompt_detected(self):
        shell = InteractiveShell()
        shell._proc = _make_proc(stdout_chunks=[b"root@device:/ # "])
        shell._initial_output = ""
        cb: list[str] = []
        await shell._read_until_prompt(callback=cb.append)
        assert "# " in shell._initial_output
        assert cb == ["root@device:/ # "]

    async def test_timeout_is_swallowed(self):
        shell = InteractiveShell()
        # read 抛 TimeoutError → wait_for 透传 → except 吞掉，不向外抛
        shell._proc = _make_proc(stdout_chunks=[asyncio.TimeoutError()])
        shell._initial_output = ""
        await shell._read_until_prompt()
        assert shell._initial_output == ""


# ---------------------------------------------------------------------------
# _background_reader：输出路由
# ---------------------------------------------------------------------------

class TestBackgroundReader:
    async def test_routes_to_output_queue_when_idle(self):
        shell = InteractiveShell()
        shell._proc = _make_proc(stdout_chunks=[b"hello", b""])
        await shell._background_reader()
        assert await shell._output_queue.get() == "hello"
        # EOF → 两个队列各收到 None 哨兵
        assert await shell._output_queue.get() is None
        assert await shell._exec_queue.get() is None

    async def test_routes_to_exec_queue_when_executing(self):
        shell = InteractiveShell()
        shell._proc = _make_proc(stdout_chunks=[b"cmd-output", b""])
        shell._executing = True
        await shell._background_reader()
        assert await shell._exec_queue.get() == "cmd-output"

    async def test_read_error_breaks_and_signals(self):
        shell = InteractiveShell()
        shell._proc = _make_proc(stdout_chunks=[RuntimeError("read fail")])
        await shell._background_reader()
        assert await shell._output_queue.get() is None
        assert await shell._exec_queue.get() is None


# ---------------------------------------------------------------------------
# execute：标记机制 / 异常路径
# ---------------------------------------------------------------------------

class TestExecute:
    async def test_yields_output_until_marker(self):
        shell = InteractiveShell()
        on_write = _feed_marker_responses(shell, "first\n", "second\n{marker}\n")
        shell._proc = _make_proc(on_write=on_write, returncode=None)
        out = [chunk async for chunk in shell.execute("ls")]
        assert out == ["first\n", "second\n"]
        # finally 清除执行状态
        assert shell._executing is False

    async def test_marker_only_chunk_yields_nothing_extra(self):
        shell = InteractiveShell()
        # 块内 marker 之前为空 → before_marker 为假 → 不产出，直接 break
        on_write = _feed_marker_responses(shell, "{marker}")
        shell._proc = _make_proc(on_write=on_write, returncode=None)
        out = [chunk async for chunk in shell.execute("true")]
        assert out == []

    async def test_raises_when_not_alive(self):
        shell = InteractiveShell()
        shell._proc = _make_proc(returncode=0)  # 已退出
        with pytest.raises(ShellExitedError):
            _ = [c async for c in shell.execute("ls")]

    async def test_raises_when_shell_exits_midway(self):
        shell = InteractiveShell()
        shell._proc = _make_proc(
            on_write=lambda data: shell._exec_queue.put_nowait(None),
            returncode=None,
        )
        with pytest.raises(ShellExitedError):
            _ = [c async for c in shell.execute("ls")]

    async def test_timeout_yields_message(self):
        shell = InteractiveShell()
        shell.READ_TIMEOUT = 0.05  # 实例级覆盖，避免真等 30s
        # on_write 不注入任何响应 → exec 队列保持空 → get 超时
        shell._proc = _make_proc(on_write=lambda data: None, returncode=None)
        out = [c async for c in shell.execute("sleep 100")]
        assert any("timed out" in c for c in out)
        assert shell._executing is False

    async def test_clears_stale_exec_queue_before_run(self):
        shell = InteractiveShell()
        # 预置残留数据，execute 应在写入前清空
        shell._exec_queue.put_nowait("stale-leftover\n")
        on_write = _feed_marker_responses(shell, "fresh\n{marker}\n")
        shell._proc = _make_proc(on_write=on_write, returncode=None)
        out = [chunk async for chunk in shell.execute("ls")]
        assert out == ["fresh\n"]
        assert "stale-leftover" not in "".join(out)


# ---------------------------------------------------------------------------
# send_input
# ---------------------------------------------------------------------------

class TestSendInput:
    async def test_writes_to_stdin(self):
        shell = InteractiveShell()
        shell._proc = _make_proc(returncode=None)
        await shell.send_input(b"ls\r")
        assert bytes(shell._proc.stdin.written) == b"ls\r"

    async def test_raises_when_not_alive(self):
        shell = InteractiveShell()
        shell._proc = _make_proc(returncode=0)
        with pytest.raises(ShellExitedError):
            await shell.send_input(b"x")


# ---------------------------------------------------------------------------
# get_output / get_initial_output
# ---------------------------------------------------------------------------

class TestOutputAccessors:
    async def test_get_output_returns_queued(self):
        shell = InteractiveShell()
        await shell._output_queue.put("line")
        assert await shell.get_output() == "line"

    async def test_get_output_none_on_eof(self):
        shell = InteractiveShell()
        await shell._output_queue.put(None)
        assert await shell.get_output() is None

    def test_get_initial_output(self):
        shell = InteractiveShell()
        shell._initial_output = "boot prompt"
        assert shell.get_initial_output() == "boot prompt"


# ---------------------------------------------------------------------------
# stop：安全关闭
# ---------------------------------------------------------------------------

class TestStop:
    async def test_closes_stdin_and_clears_proc(self):
        shell = InteractiveShell()
        proc = _make_proc(returncode=None)
        shell._proc = proc
        await shell.stop()
        assert proc.stdin.closed is True
        proc.wait.assert_awaited()
        assert shell._proc is None

    async def test_kills_on_wait_timeout(self):
        shell = InteractiveShell()
        proc = _make_proc(returncode=None)
        # 第一次 wait 超时 → kill；第二次 wait（kill 后）正常返回
        proc.wait = AsyncMock(side_effect=[asyncio.TimeoutError(), 0])
        shell._proc = proc
        await shell.stop()
        proc.kill.assert_called_once()
        assert shell._proc is None

    async def test_noop_when_no_proc(self):
        shell = InteractiveShell()
        await shell.stop()  # 不应抛出
        assert shell._proc is None

    async def test_noop_when_already_exited(self):
        shell = InteractiveShell()
        proc = _make_proc(returncode=0)
        shell._proc = proc
        await shell.stop()
        # returncode 非 None → 不进入清理分支
        assert proc.stdin.closed is False
        assert shell._proc is proc

    async def test_cancels_pending_reader_task(self):
        shell = InteractiveShell()
        proc = _make_proc(returncode=None)
        shell._proc = proc

        async def _never_ending() -> None:
            await asyncio.sleep(3600)

        shell._reader_task = asyncio.create_task(_never_ending())
        await shell.stop()
        assert shell._reader_task.cancelled() or shell._reader_task.done()
        assert shell._proc is None

    async def test_closes_stream_transports(self):
        # Windows 资源泄漏修复：stop() 应关闭流上的 _transport
        shell = InteractiveShell()
        proc = _make_proc(returncode=None)
        transport = MagicMock()
        proc.stderr._transport = transport
        shell._proc = proc
        await shell.stop()
        transport.close.assert_called_once()
        assert shell._proc is None

    async def test_transport_close_error_is_swallowed(self):
        shell = InteractiveShell()
        proc = _make_proc(returncode=None)
        transport = MagicMock()
        transport.close.side_effect = RuntimeError("close fail")
        proc.stderr._transport = transport
        shell._proc = proc
        await shell.stop()  # 不应抛出
        assert shell._proc is None


# ---------------------------------------------------------------------------
# _spawn_process：ConPTY 分支 / 管道降级
# ---------------------------------------------------------------------------

class TestSpawnProcess:
    async def test_pipe_on_non_windows(self):
        shell = InteractiveShell()
        fake = _make_proc()
        with (
            patch("sys.platform", "linux"),
            patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=fake)) as exec_mock,
        ):
            proc = await shell._spawn_process("emulator-5554")
        assert proc is fake
        exec_mock.assert_awaited_once()

    async def test_conpty_used_on_windows(self):
        shell = InteractiveShell()
        fake = _make_proc()
        with (
            patch("sys.platform", "win32"),
            patch("app.infrastructure.adb.shell.settings") as settings_mock,
            patch("app.infrastructure.adb.winpty.conpty_supported", return_value=True),
            patch(
                "app.infrastructure.adb.winpty.spawn_conpty",
                new=AsyncMock(return_value=fake),
            ) as spawn_mock,
        ):
            settings_mock.return_value.adb.use_conpty = True
            proc = await shell._spawn_process("emulator-5554")
        assert proc is fake
        spawn_mock.assert_awaited_once()

    async def test_conpty_unsupported_falls_back_to_pipe(self):
        shell = InteractiveShell()
        fake = _make_proc()
        with (
            patch("sys.platform", "win32"),
            patch("app.infrastructure.adb.shell.settings") as settings_mock,
            patch("app.infrastructure.adb.winpty.conpty_supported", return_value=False),
            patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=fake)) as exec_mock,
        ):
            settings_mock.return_value.adb.use_conpty = True
            proc = await shell._spawn_process("emulator-5554")
        assert proc is fake
        exec_mock.assert_awaited_once()

    async def test_conpty_disabled_uses_pipe(self):
        shell = InteractiveShell()
        fake = _make_proc()
        with (
            patch("sys.platform", "win32"),
            patch("app.infrastructure.adb.shell.settings") as settings_mock,
            patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=fake)) as exec_mock,
        ):
            settings_mock.return_value.adb.use_conpty = False
            proc = await shell._spawn_process("emulator-5554")
        assert proc is fake
        exec_mock.assert_awaited_once()

    async def test_conpty_spawn_failure_falls_back_to_pipe(self):
        shell = InteractiveShell()
        fake = _make_proc()
        with (
            patch("sys.platform", "win32"),
            patch("app.infrastructure.adb.shell.settings") as settings_mock,
            patch("app.infrastructure.adb.winpty.conpty_supported", return_value=True),
            patch(
                "app.infrastructure.adb.winpty.spawn_conpty",
                new=AsyncMock(side_effect=RuntimeError("conpty fail")),
            ),
            patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=fake)) as exec_mock,
        ):
            settings_mock.return_value.adb.use_conpty = True
            proc = await shell._spawn_process("emulator-5554")
        assert proc is fake
        exec_mock.assert_awaited_once()
