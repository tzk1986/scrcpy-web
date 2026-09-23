"""
ScrcpyEncoder 单元测试（infrastructure/stream/scrcpy.py）
==========================================================

覆盖 ScrcpyEncoder 全生命周期，不依赖真实 adb / socket：

- start()：opts 规范化（max_size 强制 0）、bit_rate 字符串换算、
  push/forward 失败、server 早退、socket 连接失败各异常路径、
  12B 包头读循环 yield、session 包更新分辨率、raw_stream 兜底模式
- send_input() / _send_swipe() / _fallback_adb_input()：touch/swipe/long_press/key/text
  分发与 adb shell input 回退（含报文/命令构造断言）
- stop()：进程 terminate/kill、socket 关闭、forward --remove、状态复位

mock 策略（沿用 test_encoder.py / test_stream_adaptive.py 风格）：
    - asyncio.create_subprocess_exec → SubprocessHarness（按命令分派假进程）
    - asyncio.open_connection → ConnectionHarness（视频/控制两条连接）
    - ServerManager.push_server → AsyncMock
    - 视频流字节经 asyncio.StreamReader.feed_data 离线喂入
"""

import asyncio
import struct
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.config import settings
from app.domain.ports import EncoderOpts
from app.infrastructure.stream.scrcpy import ScrcpyEncoder
from app.scrcpy.constants import SCRCPY_SERVER_CLASS
from app.scrcpy.control_sender import ACTION_DOWN, ACTION_MOVE, ACTION_UP, ControlSender
from app.scrcpy.stream_protocol import (
    SC_CODEC_ID_DISABLED,
    SC_CODEC_ID_ERROR,
    SC_CODEC_ID_H264,
    SC_PACKET_FLAG_CONFIG,
    SC_PACKET_FLAG_KEY_FRAME,
    SessionEvent,
)

DEVICE_ID = "emulator-5554"


# ---------------------------------------------------------------------------
# 协议字节构造（与 tests/scrcpy/test_stream_protocol.py 同构，避免跨文件依赖）
# ---------------------------------------------------------------------------

def make_session(width: int, height: int, client_resized: bool = False) -> bytes:
    """12B session 包头：bit63 置位 + 大端宽高。"""
    flags = (1 << 31) | (1 if client_resized else 0)
    return struct.pack(">III", flags, width, height)


def make_frame_header(pts_flags: int, size: int) -> bytes:
    """12B 媒体包头：8B PTS/flags + 4B 载荷长度（大端）。"""
    return struct.pack(">QI", pts_flags, size)


def make_handshake(codec_id: int = SC_CODEC_ID_H264) -> bytes:
    """建连握手：1B dummy + 64B 设备名 + 4B codec id。"""
    return b"\x00" + b"test-device".ljust(64, b"\x00") + struct.pack(">I", codec_id)


def make_reader(data: bytes = b"", eof: bool = True) -> asyncio.StreamReader:
    """构造离线 StreamReader；eof=False 时保持阻塞（模拟流未结束）。"""
    reader = asyncio.StreamReader()
    if data:
        reader.feed_data(data)
    if eof:
        reader.feed_eof()
    return reader


# ---------------------------------------------------------------------------
# 手写 Fake（项目风格：test_stream_adaptive.py）
# ---------------------------------------------------------------------------

class FakeStderr:
    """
    scrcpy-server stderr 假读端。

    - lines：bytes 依序返回；Exception 实例直接 raise（模拟读错误）
    - 耗尽后：raises 非 None → 抛该异常（raises_limit 限定次数，之后转 block，
      避免后台日志任务在“即时抛 TimeoutError”下热循环并吞掉取消）；
      block=True → 挂起（模拟无新输出）；否则立即返回 b""（EOF）
    - on_line：每返回一行 bytes 后回调（用于中途翻转进程 returncode）
    """

    def __init__(self, lines=None, *, raises=None, raises_limit=None, block=False, on_line=None):
        self._items = list(lines or [])
        self._raises = raises
        self._raises_limit = raises_limit
        self._raise_count = 0
        self._block = block
        self.on_line = on_line

    async def readline(self) -> bytes:
        if self._items:
            item = self._items.pop(0)
            if isinstance(item, BaseException):
                raise item
            if self.on_line is not None:
                self.on_line(item)
            return item
        if self._raises is not None and (
            self._raises_limit is None or self._raise_count < self._raises_limit
        ):
            self._raise_count += 1
            raise self._raises
        if self._block or self._raises is not None:
            await asyncio.sleep(3600)  # 挂起，等待被取消（模拟无新输出）
        return b""

    async def read(self) -> bytes:
        """exited-early 分支：一次读出剩余全部文本行。"""
        out = bytearray()
        while self._items and isinstance(self._items[0], bytes):
            out += self._items.pop(0)
        return bytes(out)


class FakeServerProcess:
    """scrcpy-server 子进程假体（terminate/kill/wait 可观测）。"""

    def __init__(self, stderr: FakeStderr, returncode: int | None = None) -> None:
        self.stderr = stderr
        self.stdout = None
        self.returncode = returncode
        self.terminate_called = False
        self.kill_called = False

    def terminate(self) -> None:
        self.terminate_called = True
        if self.returncode is None:
            self.returncode = -15

    def kill(self) -> None:
        self.kill_called = True
        self.returncode = -9

    async def wait(self) -> int | None:
        return self.returncode


class FakeWriter:
    """StreamWriter 假体（close/wait_closed/write 可观测）。"""

    def __init__(self) -> None:
        self.closed = False
        self.wait_closed_called = False
        self.written = bytearray()

    def write(self, data: bytes) -> None:
        self.written.extend(data)

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        self.wait_closed_called = True


def make_adb_proc(stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0) -> MagicMock:
    """普通 adb 子进程假体（communicate 可用）。"""
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.wait = AsyncMock(return_value=returncode)
    return proc


class SubprocessHarness:
    """asyncio.create_subprocess_exec 假实现：按命令分派假进程。"""

    def __init__(
        self,
        *,
        server_stderr: FakeStderr | None = None,
        server_process: FakeServerProcess | None = None,
        forward_ok: bool = True,
        cleanup_error: bool = False,
        wm_output: bytes = b"Physical size: 1080x1920\n",
        wm_error: bool = False,
    ) -> None:
        self.calls: list[tuple] = []
        self.forward_ok = forward_ok
        self.cleanup_error = cleanup_error
        self.wm_output = wm_output
        self.wm_error = wm_error
        self.server_stderr = server_stderr if server_stderr is not None else FakeStderr(block=True)
        self.server_process = server_process
        self.cleanup_failed = False

    async def __call__(self, *args, **kwargs):
        self.calls.append(args)
        cmd = [str(a) for a in args]

        if "forward" in cmd:
            if "--remove" in cmd and self.cleanup_error and not self.cleanup_failed:
                self.cleanup_failed = True
                raise OSError("no such port forwarding")  # 起始清理失败（实现应忽略）
            if "--remove" not in cmd and not self.forward_ok:
                return make_adb_proc(stderr=b"cannot bind listener tcp:27183", returncode=1)
            return make_adb_proc()

        if SCRCPY_SERVER_CLASS in cmd:
            if self.server_process is None:
                self.server_process = FakeServerProcess(self.server_stderr)
            return self.server_process

        if "wm" in cmd:
            if self.wm_error:
                raise OSError("wm size failed")
            return make_adb_proc(stdout=self.wm_output)

        if "input" in cmd:
            return make_adb_proc()

        raise AssertionError(f"unexpected command: {cmd}")

    def server_cmd(self) -> list[str]:
        """scrcpy-server 启动命令（tokens）。"""
        for call in self.calls:
            tokens = [str(a) for a in call]
            if SCRCPY_SERVER_CLASS in tokens:
                return tokens
        raise AssertionError("scrcpy-server command not captured")

    def commands_with(self, *needles: str) -> list[list[str]]:
        """所有同时包含全部给定 token 的命令。"""
        found = []
        for call in self.calls:
            tokens = [str(a) for a in call]
            if all(n in tokens for n in needles):
                found.append(tokens)
        return found


class ConnectionHarness:
    """asyncio.open_connection 假实现：第 1 次视频连接，第 2 次控制连接。"""

    def __init__(
        self,
        video_reader: asyncio.StreamReader,
        *,
        control_ok: bool = True,
        video_error: Exception | None = None,
    ) -> None:
        self.video_reader = video_reader
        self.control_ok = control_ok
        self.video_error = video_error
        self.calls: list[tuple[str, int]] = []
        self.video_writer = FakeWriter()
        self.control_writer = FakeWriter()

    async def __call__(self, host: str, port: int):
        self.calls.append((host, port))
        if len(self.calls) == 1:
            if self.video_error is not None:
                raise self.video_error
            return self.video_reader, self.video_writer
        if not self.control_ok:
            raise OSError("connection refused")
        return asyncio.StreamReader(), self.control_writer


async def drive_start(
    encoder: ScrcpyEncoder,
    *,
    harness: SubprocessHarness,
    video_data: bytes = b"",
    eof: bool = True,
    conn: ConnectionHarness | None = None,
    opts: EncoderOpts | None = None,
    push_ok: bool = True,
    probe=None,
) -> list[bytes]:
    """驱动 start() 至自然结束（含 stop 清理），返回 yield 出的全部载荷。

    probe：每次 yield 后回调一次（stop 会复位分辨率等状态，需在流运行中取样）。
    """
    encoder._server_manager.push_server = AsyncMock(return_value=push_ok)
    if conn is None:
        conn = ConnectionHarness(make_reader(video_data, eof=eof))
    chunks: list[bytes] = []
    with patch("asyncio.create_subprocess_exec", new=harness), \
            patch("asyncio.open_connection", new=conn):
        async for chunk in encoder.start(DEVICE_ID, opts or EncoderOpts()):
            chunks.append(chunk)
            if probe is not None:
                probe(encoder)
    return chunks


async def let_background_tasks_settle() -> None:
    """给未被 finally 覆盖的收尾任务一个执行机会，避免 pending task 告警。"""
    await asyncio.sleep(0.01)


# ---------------------------------------------------------------------------
# start()：异常路径
# ---------------------------------------------------------------------------

async def test_start_raises_when_push_server_fails():
    """push_server 失败 → start 立即抛 RuntimeError。"""
    encoder = ScrcpyEncoder()
    with pytest.raises(RuntimeError, match="Failed to push scrcpy-server.jar"):
        await drive_start(encoder, harness=SubprocessHarness(), push_ok=False)


async def test_start_raises_when_forward_setup_fails():
    """adb forward 建立失败（返回码非 0）→ RuntimeError 且携带 stderr 内容。"""
    encoder = ScrcpyEncoder()
    harness = SubprocessHarness(forward_ok=False)
    with pytest.raises(RuntimeError, match="adb forward failed: cannot bind listener"):
        await drive_start(encoder, harness=harness)


async def test_start_raises_when_server_exits_before_ready():
    """server 进程在就绪前退出 → RuntimeError 携带 stderr。"""
    encoder = ScrcpyEncoder()
    stderr = FakeStderr([b"Error: bad arguments\n"])
    proc = FakeServerProcess(stderr, returncode=1)
    harness = SubprocessHarness(server_process=proc)
    with pytest.raises(RuntimeError, match="scrcpy-server failed: Error: bad arguments"):
        await drive_start(encoder, harness=harness)


async def test_start_raises_when_server_exits_after_ready():
    """server 读完 Device: 行后立刻退出 → RuntimeError（exited early 分支）。"""
    encoder = ScrcpyEncoder()
    stderr = FakeStderr([b"Device: test-device\n"], block=True)
    proc = FakeServerProcess(stderr)

    def _die_after_device(line: bytes) -> None:
        if b"Device:" in line:
            proc.returncode = 1

    stderr.on_line = _die_after_device
    harness = SubprocessHarness(server_process=proc)
    with pytest.raises(RuntimeError, match="scrcpy-server exited early"):
        await drive_start(encoder, harness=harness)
    await let_background_tasks_settle()


async def test_start_raises_when_video_socket_connect_fails():
    """视频 socket 连接失败 → RuntimeError（控制/读循环不再启动）。"""
    encoder = ScrcpyEncoder()
    stderr = FakeStderr([b"Device: test-device\n"], raises=RuntimeError("stderr closed"))
    conn = ConnectionHarness(make_reader(), video_error=OSError("connection refused"))
    with pytest.raises(RuntimeError, match="Failed to connect to scrcpy-server"):
        await drive_start(encoder, harness=SubprocessHarness(server_stderr=stderr), conn=conn)
    await let_background_tasks_settle()


# ---------------------------------------------------------------------------
# start()：stderr 就绪探测与容错
# ---------------------------------------------------------------------------

async def test_server_ready_timeout_warns_and_continues():
    """stderr 持续超时（未出现 Device:）→ 记 warning 但不中断启动。"""
    encoder = ScrcpyEncoder()
    # 就绪探测最多 30 轮：前 30 次读超时，之后转入挂起（模拟真实 fd 无数据）
    stderr = FakeStderr(raises=asyncio.TimeoutError, raises_limit=30)
    chunks = await drive_start(encoder, harness=SubprocessHarness(server_stderr=stderr))
    assert chunks == []
    assert encoder.process is None  # 已走完 stop 清理


async def test_stderr_read_error_is_swallowed():
    """stderr 读异常 → warning 吞掉，流程继续（不抛错）。"""
    encoder = ScrcpyEncoder()
    stderr = FakeStderr(raises=RuntimeError("stderr broken"))
    chunks = await drive_start(encoder, harness=SubprocessHarness(server_stderr=stderr))
    assert chunks == []


async def test_forward_cleanup_failure_is_ignored():
    """起始 forward --remove 清理失败被忽略，转发照常建立。"""
    encoder = ScrcpyEncoder()
    stderr = FakeStderr([b"Device: test-device\n"], block=True)
    harness = SubprocessHarness(server_stderr=stderr, cleanup_error=True)

    chunks = await drive_start(encoder, harness=harness)

    assert chunks == []
    assert harness.cleanup_failed is True
    assert harness.commands_with("forward", "localabstract:scrcpy")  # 转发仍建立成功
    # 起点清理（抛错）+ stop 清理各一次
    assert len(harness.commands_with("forward", "--remove")) == 2


async def test_control_connection_failure_is_non_fatal():
    """控制 socket 连接失败不致命 → 流程继续，_control_writer 保持 None。"""
    encoder = ScrcpyEncoder()
    payload = b"\x00\x00\x00\x01a" + b"\xee" * 4
    stream = (
        make_handshake()
        + make_session(1080, 1920)
        + make_frame_header(0, len(payload)) + payload
    )
    conn = ConnectionHarness(make_reader(stream), control_ok=False)
    seen: list[tuple[int, int]] = []

    chunks = await drive_start(
        encoder,
        harness=SubprocessHarness(server_stderr=FakeStderr([b"Device: test-device\n"], block=True)),
        conn=conn,
        probe=lambda enc: seen.append(enc.resolution),
    )

    assert chunks == [payload]
    assert seen == [(1080, 1920)]  # 分辨率仍随 session 更新（与有无控制通道无关）
    assert encoder._control_writer is None
    assert encoder._control_sender is None  # 无控制通道 → session 后也建不了发送器


# ---------------------------------------------------------------------------
# start()：opts 规范化与 bit_rate 换算
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "bit_rate, expected",
    [
        ("4M", "4000000"),
        ("2K", "2000"),
        ("1500000", "1500000"),
        (4000000, "4000000"),
        ("2.5M", "2500000"),
    ],
)
async def test_server_command_normalizes_opts(bit_rate, expected):
    """max_size 强制为 0；bit_rate 字符串换算为整数后写入启动命令。"""
    encoder = ScrcpyEncoder()
    stderr = FakeStderr([b"Device: test-device\n"], block=True)
    harness = SubprocessHarness(server_stderr=stderr)
    opts = EncoderOpts(max_size=720, bit_rate=bit_rate, codec="h264", fps=30)

    await drive_start(encoder, harness=harness, opts=opts)

    cmd = harness.server_cmd()
    assert "max_size=0" in cmd          # 强制不缩放（坐标对齐）
    assert f"video_bit_rate={expected}" in cmd
    assert "max_fps=30" in cmd
    assert "control=true" in cmd
    assert "raw_stream=true" not in cmd  # 默认走 12B 包头协议


# ---------------------------------------------------------------------------
# start()：12B 包头读循环
# ---------------------------------------------------------------------------

async def test_full_flow_yields_packets_and_updates_resolution():
    """完整流：session 更新分辨率 → config/媒体包按序 yield → 收尾清理。"""
    encoder = ScrcpyEncoder()
    sps = b"\x00\x00\x00\x01g" + b"\x00" * 8
    key = b"\x00\x00\x00\x01e" + b"\xaa" * 16
    delta = b"\x00\x00\x00\x01a" + b"\xbb" * 8
    stream = (
        make_handshake()
        + make_session(1080, 1920)
        + make_frame_header(SC_PACKET_FLAG_CONFIG, len(sps)) + sps
        + make_frame_header(SC_PACKET_FLAG_KEY_FRAME, len(key)) + key
        + make_frame_header(0, len(delta)) + delta
    )
    # 前两行供启动探测消费（第二行命中 Device: 即 break），第三行留给后台日志任务
    stderr = FakeStderr(
        [b"INFO: scrcpy 4.1 started\n", b"Device: test-device\n", b"INFO: streaming\n"],
        block=True,
    )
    harness = SubprocessHarness(server_stderr=stderr)
    conn = ConnectionHarness(make_reader(stream))
    seen: list[tuple[tuple[int, int], object]] = []

    chunks = await drive_start(
        encoder,
        harness=harness,
        conn=conn,
        probe=lambda enc: seen.append((enc.resolution, enc._control_sender)),
    )

    assert chunks == [sps, key, delta]          # 一包一 yield，顺序保持
    assert seen[0][0] == (1080, 1920)           # session 包先于首个媒体包处理
    assert isinstance(seen[0][1], ControlSender)
    assert seen[0][1].resolution == (1080, 1920)
    assert conn.video_writer.wait_closed_called  # 视频/控制 socket 已关闭
    assert conn.control_writer.wait_closed_called
    assert harness.server_process.terminate_called
    assert encoder.process is None
    assert encoder._device_id == ""
    assert encoder._resolution == (0, 0)        # stop 复位内部状态
    # stop 时清理端口转发（起点清理 + stop 清理）
    assert len(harness.commands_with("forward", "--remove")) == 2


async def test_early_exit_cancels_blocked_read_task():
    """外部置 _running=False 提前退出 → 阻塞中的读任务被取消并走 stop。"""
    encoder = ScrcpyEncoder()
    payload = b"\x00\x00\x00\x01a" + b"\xcc" * 8
    stream = (
        make_handshake()
        + make_session(720, 1280)
        + make_frame_header(0, len(payload)) + payload
    )
    stderr = FakeStderr([b"Device: test-device\n"], block=True)
    harness = SubprocessHarness(server_stderr=stderr)
    conn = ConnectionHarness(make_reader(stream, eof=False))  # 不 EOF：读任务保持阻塞

    encoder._server_manager.push_server = AsyncMock(return_value=True)
    with patch("asyncio.create_subprocess_exec", new=harness), \
            patch("asyncio.open_connection", new=conn):
        agen = encoder.start(DEVICE_ID, EncoderOpts())
        first = await agen.__anext__()
        assert first == payload
        encoder._running = False  # 模拟停止：读任务在 finally 中被取消
        with pytest.raises(StopAsyncIteration):
            await agen.__anext__()

    assert harness.server_process.terminate_called


@pytest.mark.parametrize(
    "codec_id",
    [SC_CODEC_ID_DISABLED, SC_CODEC_ID_ERROR, 0xDEADBEEF],
    ids=["disabled", "config-error", "unsupported"],
)
async def test_codec_failure_ends_stream_quietly(codec_id):
    """codec id 0/1/未知 → 读循环按协议异常结束，不向调用方抛错。"""
    encoder = ScrcpyEncoder()
    stderr = FakeStderr([b"Device: test-device\n"], block=True)

    chunks = await drive_start(
        encoder,
        harness=SubprocessHarness(server_stderr=stderr),
        video_data=make_handshake(codec_id),
    )

    assert chunks == []


# ---------------------------------------------------------------------------
# start()：raw_stream 兜底模式
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "wm_output, wm_error, expected_res",
    [
        (b"Physical size: 1080x1920\n", False, (1080, 1920)),
        (b"Physical size: unknown\n", False, (0, 0)),
        (b"", True, (0, 0)),
    ],
    ids=["parsed", "unparsable", "wm-error"],
)
async def test_raw_fallback_mode(monkeypatch, wm_output, wm_error, expected_res):
    """raw_stream_fallback=True：裸流块读 + adb wm size 取分辨率 + 带分辨率建发送器。"""
    monkeypatch.setattr(settings().stream, "raw_stream_fallback", True)
    encoder = ScrcpyEncoder()
    raw_chunk = b"\x00\x00\x00\x01\x65" + b"\xdd" * 32
    harness = SubprocessHarness(
        server_stderr=FakeStderr([b"Device: test-device\n"], block=True),
        wm_output=wm_output,
        wm_error=wm_error,
    )
    seen: list[tuple[tuple[int, int], object]] = []

    chunks = await drive_start(
        encoder,
        harness=harness,
        video_data=raw_chunk,
        probe=lambda enc: seen.append((enc.resolution, enc._control_sender)),
    )

    cmd = harness.server_cmd()
    assert "raw_stream=true" in cmd
    assert "send_frame_meta=false" in cmd
    assert chunks == [raw_chunk]  # 裸流按块 yield，不做切包
    assert seen[0][0] == expected_res
    if expected_res != (0, 0):
        assert seen[0][1] is not None
        assert seen[0][1].resolution == expected_res
    else:
        assert seen[0][1] is None  # 分辨率未知 → 不建发送器（走 adb 回退）


# ---------------------------------------------------------------------------
# session 事件处理
# ---------------------------------------------------------------------------

def test_handle_session_event_updates_existing_sender():
    """已有 ControlSender → 仅更新分辨率。"""
    encoder = ScrcpyEncoder()
    sender = MagicMock()
    encoder._control_sender = sender
    encoder._control_writer = FakeWriter()

    encoder._handle_session_event(DEVICE_ID, SessionEvent(1080, 2160, True))

    assert encoder.resolution == (1080, 2160)
    sender.update_resolution.assert_called_once_with((1080, 2160))


def test_handle_session_event_creates_sender_from_writer():
    """无发送器但有控制 writer → 用新分辨率创建 ControlSender。"""
    encoder = ScrcpyEncoder()
    encoder._control_writer = FakeWriter()

    encoder._handle_session_event(DEVICE_ID, SessionEvent(720, 1280, False))

    assert isinstance(encoder._control_sender, ControlSender)
    assert encoder._control_sender.resolution == (720, 1280)


def test_handle_session_event_without_control_writer():
    """既无发送器也无 writer → 仅记录分辨率，不抛错。"""
    encoder = ScrcpyEncoder()

    encoder._handle_session_event(DEVICE_ID, SessionEvent(1080, 1920, False))

    assert encoder.resolution == (1080, 1920)
    assert encoder._control_sender is None


def test_resolution_property():
    """resolution 属性反映内部状态。"""
    encoder = ScrcpyEncoder()
    assert encoder.resolution == (0, 0)
    encoder._resolution = (1080, 1920)
    assert encoder.resolution == (1080, 1920)


# ---------------------------------------------------------------------------
# send_input 分发
# ---------------------------------------------------------------------------

def make_control_encoder() -> tuple[ScrcpyEncoder, MagicMock]:
    """带假控制发送器的编码器（分辨率已就绪）。"""
    encoder = ScrcpyEncoder()
    encoder._device_id = DEVICE_ID
    encoder._resolution = (1080, 1920)
    sender = MagicMock()
    sender.touch = AsyncMock()
    sender.keycode = AsyncMock()
    sender.text = AsyncMock()
    encoder._control_sender = sender
    return encoder, sender


async def test_send_input_touch_sends_down_then_up():
    """touch → DOWN/UP 两次，坐标一致。"""
    encoder, sender = make_control_encoder()

    await encoder.send_input({"action": "touch", "x": 100, "y": 200})

    calls = [(c.args[0], c.args[1], c.args[2]) for c in sender.touch.await_args_list]
    assert calls == [(100, 200, ACTION_DOWN), (100, 200, ACTION_UP)]


@pytest.mark.parametrize(
    "data, expected_touches",
    [
        # 距离 < 10：只发 DOWN + UP（起点/终点各一次）
        ({"action": "swipe", "x1": 0, "y1": 0, "x2": 5, "y2": 0, "duration": 300},
         [(0, 0, ACTION_DOWN), (5, 0, ACTION_UP)]),
        # 时长 < 100ms：同样只发 DOWN + UP
        ({"action": "swipe", "x1": 0, "y1": 0, "x2": 200, "y2": 0, "duration": 50},
         [(0, 0, ACTION_DOWN), (200, 0, ACTION_UP)]),
    ],
)
async def test_send_input_swipe_short_paths(data, expected_touches):
    """短距离/短时长 swipe 不插入 MOVE 事件。"""
    encoder, sender = make_control_encoder()

    with patch("asyncio.sleep", new=AsyncMock()):
        await encoder.send_input(data)

    calls = [(c.args[0], c.args[1], c.args[2]) for c in sender.touch.await_args_list]
    assert calls == expected_touches


async def test_send_input_swipe_builds_move_sequence():
    """长距离 swipe：DOWN → 按 5px 步长插值 MOVE → 终点 UP。"""
    encoder, sender = make_control_encoder()

    with patch("asyncio.sleep", new=AsyncMock()) as sleeper:
        await encoder.send_input(
            {"action": "swipe", "x1": 0, "y1": 0, "x2": 50, "y2": 0, "duration": 200}
        )

    calls = [(c.args[0], c.args[1], c.args[2]) for c in sender.touch.await_args_list]
    assert calls[0] == (0, 0, ACTION_DOWN)
    assert calls[-1] == (50, 0, ACTION_UP)
    moves = calls[1:-1]
    assert len(moves) == 10                      # 50px / 5px 步长
    assert [x for x, _, _ in moves] == [5 * i for i in range(1, 11)]
    assert all(action == ACTION_MOVE for _, _, action in moves)
    assert sleeper.await_count == 10             # 每个 MOVE 后 sleep（已 mock，无真实等待）


async def test_send_input_key_sends_down_and_up():
    """key → keycode DOWN/UP。"""
    encoder, sender = make_control_encoder()

    await encoder.send_input({"action": "key", "keycode": 4})

    calls = [(c.args[0], c.args[1]) for c in sender.keycode.await_args_list]
    assert calls == [(4, ACTION_DOWN), (4, ACTION_UP)]


async def test_send_input_text_dispatches_to_control_sender():
    """text → ControlSender.text 透传。"""
    encoder, sender = make_control_encoder()

    await encoder.send_input({"action": "text", "text": "hello"})

    sender.text.assert_awaited_once_with("hello")


async def test_send_input_long_press_holds_between_down_and_up():
    """long_press → DOWN → 保持 duration/1000 秒 → UP，坐标一致。"""
    encoder, sender = make_control_encoder()

    with patch("asyncio.sleep", new=AsyncMock()) as sleeper:
        await encoder.send_input({"action": "long_press", "x": 100, "y": 200, "duration": 1500})

    calls = [(c.args[0], c.args[1], c.args[2]) for c in sender.touch.await_args_list]
    assert calls == [(100, 200, ACTION_DOWN), (100, 200, ACTION_UP)]
    sleeper.assert_awaited_once_with(1.5)


async def test_send_input_long_press_default_duration():
    """long_press 未传 duration → 默认保持 1000ms。"""
    encoder, sender = make_control_encoder()

    with patch("asyncio.sleep", new=AsyncMock()) as sleeper:
        await encoder.send_input({"action": "long_press", "x": 1, "y": 2})

    calls = [(c.args[0], c.args[1], c.args[2]) for c in sender.touch.await_args_list]
    assert calls == [(1, 2, ACTION_DOWN), (1, 2, ACTION_UP)]
    sleeper.assert_awaited_once_with(1.0)


async def test_send_input_unknown_action_falls_back_without_failure():
    """未知 action：记 warning，不抛错（也不发控制消息）。"""
    encoder, sender = make_control_encoder()

    with patch("asyncio.create_subprocess_exec", new=AsyncMock()) as exec_mock:
        await encoder.send_input({"action": "bogus"})

    sender.touch.assert_not_awaited()
    exec_mock.assert_not_awaited()


# ---------------------------------------------------------------------------
# adb shell input 回退
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "data, expected",
    [
        ({"action": "touch", "x": 100, "y": 200}, ["input", "tap", "100", "200"]),
        (
            {"action": "swipe", "x1": 1, "y1": 2, "x2": 3, "y2": 4, "duration": 250},
            ["input", "swipe", "1", "2", "3", "4", "250"],
        ),
        ({"action": "swipe", "x1": 1, "y1": 2, "x2": 3, "y2": 4}, ["input", "swipe",
                                                                  "1", "2", "3", "4", "300"]),
        ({"action": "long_press", "x": 1, "y": 2}, ["input", "swipe",
                                                     "1", "2", "1", "2", "1000"]),
        ({"action": "long_press", "x": 1, "y": 2, "duration": 800},
         ["input", "swipe", "1", "2", "1", "2", "800"]),
        ({"action": "key", "keycode": 4}, ["input", "keyevent", "4"]),
        ({"action": "text", "text": "hi"}, ["input", "text", "hi"]),
    ],
    ids=["touch", "swipe", "swipe-default-duration",
         "long-press", "long-press-duration", "key", "text"],
)
async def test_fallback_adb_input_builds_expected_commands(data, expected):
    """控制通道不可用 → adb shell input 命令构造（含 duration 缺省值 300）。"""
    encoder = ScrcpyEncoder()
    encoder._device_id = DEVICE_ID
    encoder._control_sender = None

    with patch("asyncio.create_subprocess_exec", new=AsyncMock()) as exec_mock:
        await encoder.send_input(data)

    args = [str(a) for a in exec_mock.call_args[0]]
    assert args[:4] == ["adb", "-s", DEVICE_ID, "shell"]
    assert args[4:] == expected


async def test_send_input_falls_back_when_resolution_unknown():
    """有发送器但分辨率未知（0,0）→ 同样回退 adb（坐标不可信）。"""
    encoder, _ = make_control_encoder()
    encoder._resolution = (0, 0)

    with patch("asyncio.create_subprocess_exec", new=AsyncMock()) as exec_mock:
        await encoder.send_input({"action": "touch", "x": 1, "y": 2})

    args = [str(a) for a in exec_mock.call_args[0]]
    assert args[4:] == ["input", "tap", "1", "2"]


async def test_send_input_falls_back_when_control_send_fails():
    """控制发送抛异常 → 同一输入回退到 adb。"""
    encoder, sender = make_control_encoder()
    sender.touch = AsyncMock(side_effect=RuntimeError("broken pipe"))

    with patch("asyncio.create_subprocess_exec", new=AsyncMock()) as exec_mock:
        await encoder.send_input({"action": "touch", "x": 10, "y": 20})

    args = [str(a) for a in exec_mock.call_args[0]]
    assert args[4:] == ["input", "tap", "10", "20"]


async def test_fallback_adb_input_swallows_exec_errors():
    """adb 子进程创建失败被吞掉（输入丢失但不影响会话）。"""
    encoder = ScrcpyEncoder()
    encoder._device_id = DEVICE_ID

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(side_effect=OSError("adb missing"))):
        await encoder.send_input({"action": "touch", "x": 1, "y": 2})  # 不抛异常


async def test_fallback_unknown_action_is_noop():
    """回退路径遇到未知 action：仅 warning，不创建子进程。"""
    encoder = ScrcpyEncoder()
    encoder._device_id = DEVICE_ID

    with patch("asyncio.create_subprocess_exec", new=AsyncMock()) as exec_mock:
        await encoder.send_input({"action": "bogus", "x": 1, "y": 2})

    exec_mock.assert_not_awaited()


# ---------------------------------------------------------------------------
# stop()
# ---------------------------------------------------------------------------

async def test_stop_terminates_process_closes_sockets_and_resets_state():
    """stop：terminate 进程 → 关闭双 socket → forward --remove → 清队列/复位状态。"""
    encoder = ScrcpyEncoder()
    proc = FakeServerProcess(FakeStderr(block=True))
    video_writer = FakeWriter()
    control_writer = FakeWriter()
    encoder.process = proc
    encoder._writer = video_writer
    encoder._reader = asyncio.StreamReader()
    encoder._control_writer = control_writer
    encoder._control_sender = MagicMock()
    encoder._device_id = DEVICE_ID
    encoder._local_port = 27183
    encoder._resolution = (1080, 1920)
    encoder._running = True
    encoder._data_queue.put_nowait(b"stale")  # 队列残留（清空分支）

    harness = SubprocessHarness()
    with patch("asyncio.create_subprocess_exec", new=harness):
        await encoder.stop()

    assert proc.terminate_called is True
    assert proc.kill_called is False
    assert video_writer.closed and video_writer.wait_closed_called
    assert control_writer.closed and control_writer.wait_closed_called
    assert harness.commands_with("forward", "--remove", "tcp:27183")
    assert encoder._data_queue.empty()
    assert encoder.process is None
    assert encoder._writer is None and encoder._reader is None
    assert encoder._control_writer is None and encoder._control_sender is None
    assert encoder._device_id == ""
    assert encoder._resolution == (0, 0)
    assert encoder._socket_name == "scrcpy"
    assert encoder._running is False


async def test_stop_kills_process_when_wait_times_out():
    """wait 超时 → kill 兜底；无设备号时跳过 forward 清理。"""
    encoder = ScrcpyEncoder()
    proc = MagicMock()
    proc.returncode = None
    proc.terminate = MagicMock()
    proc.kill = MagicMock()
    proc.wait = AsyncMock(side_effect=[asyncio.TimeoutError, None])
    encoder.process = proc
    encoder._device_id = ""  # 无设备 → 不发起清理子进程

    harness = SubprocessHarness()
    with patch("asyncio.create_subprocess_exec", new=harness):
        await encoder.stop()

    proc.kill.assert_called_once()
    assert proc.wait.await_count == 2
    assert encoder.process is None
    assert harness.commands_with("forward", "--remove") == []


async def test_stop_swallows_socket_close_and_forward_cleanup_errors():
    """socket close 抛错、forward --remove 子进程抛错均被吞掉，状态仍复位。"""
    encoder = ScrcpyEncoder()
    encoder._device_id = DEVICE_ID
    encoder._writer = MagicMock()
    encoder._writer.close = MagicMock(side_effect=RuntimeError("already closed"))
    encoder._control_writer = MagicMock()
    encoder._control_writer.close = MagicMock(side_effect=RuntimeError("already closed"))

    harness = SubprocessHarness(cleanup_error=True)
    with patch("asyncio.create_subprocess_exec", new=harness):
        await encoder.stop()  # 不抛异常

    assert harness.cleanup_failed is True  # stop 清理确实尝试过且失败
    assert encoder._writer is None and encoder._control_writer is None
    assert encoder._device_id == ""


async def test_stop_swallows_terminate_errors():
    """terminate 抛错（进程已消失）→ 吞掉并复位 process。"""
    encoder = ScrcpyEncoder()
    proc = MagicMock()
    proc.returncode = None
    proc.terminate = MagicMock(side_effect=ProcessLookupError("no such process"))
    proc.wait = AsyncMock()
    encoder.process = proc

    harness = SubprocessHarness()
    with patch("asyncio.create_subprocess_exec", new=harness):
        await encoder.stop()  # 不抛异常

    assert encoder.process is None


# ---------------------------------------------------------------------------
# RESET_VIDEO 与空闲保活（方案 19 实施项 1a）
# ---------------------------------------------------------------------------

async def test_reset_video_sends_type17():
    from app.scrcpy.control_sender import ControlSender
    writer = FakeWriter()
    sender = ControlSender(writer, (1360, 768))
    await sender.reset_video()
    assert bytes(writer.written) == bytes([17])


async def wait_until(pred, timeout: float = 1.5) -> bool:
    """轮询等待 pred() 为真（默认 1.5s 超时；pred 为同步谓词）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        await asyncio.sleep(0.02)
    return bool(pred())


async def test_idle_keepalive_sends_reset_video(monkeypatch):
    """静止超过 idle_reset_seconds 后经控制 socket 发出 type=17，收到帧后计时复位。"""
    import types

    fake = types.SimpleNamespace(stream=types.SimpleNamespace(
        idle_reset_seconds=0.2, raw_stream_fallback=False))
    monkeypatch.setattr("app.infrastructure.stream.scrcpy.settings", lambda: fake)

    encoder = ScrcpyEncoder()
    payload = b"\x00\x00\x00\x01a" + b"\xee" * 4
    stream = (
        make_handshake()
        + make_session(1080, 1920)
        + make_frame_header(0, len(payload)) + payload
    )
    stderr = FakeStderr([b"Device: test-device\n"], block=True)
    harness = SubprocessHarness(server_stderr=stderr)
    conn = ConnectionHarness(make_reader(stream, eof=False))  # 1 帧后视频流永久静默

    encoder._server_manager.push_server = AsyncMock(return_value=True)
    received: list[bytes] = []

    async def consume() -> None:
        # 消费端始终挂起在 __anext__ 上（真实调用方 StreamService 即如此），
        # yield 循环只有在等待下一帧时才空闲并检查静止时长
        async for chunk in encoder.start(DEVICE_ID, EncoderOpts()):
            received.append(chunk)

    with patch("asyncio.create_subprocess_exec", new=harness), \
            patch("asyncio.open_connection", new=conn):
        task = asyncio.create_task(consume())
        try:
            # ① 启动并消费首帧（session 包建立 _control_sender）
            assert await wait_until(lambda: len(received) == 1)
            # ② 静默 ≥ 0.2s → ③ 控制 writer 恰为一个 0x11：
            #    只有「首帧后无数据」才触发（0.2s 阈值下 1.5s 内必达）
            assert await wait_until(
                lambda: bytes(conn.control_writer.written) == b"\x11")
            # 收到新帧 → 计时复位：短暂静默不补发，再次静默超阈值才发第二个 0x11
            conn.video_reader.feed_data(make_frame_header(0, len(payload)) + payload)
            assert await wait_until(lambda: len(received) == 2)
            await asyncio.sleep(0.15)
            assert bytes(conn.control_writer.written) == b"\x11"
            assert await wait_until(
                lambda: bytes(conn.control_writer.written) == b"\x11\x11")
        finally:
            encoder._running = False  # 下一个 tick 退出取帧循环 → 消费任务自然结束
            await task
    assert harness.server_process.terminate_called


async def test_idle_keepalive_retries_after_send_failure(monkeypatch):
    """reset_video 发送抛错 → 清除锁存，下个 tick 重试（不再静默锁死）。

    覆盖评审修复：失败后若保留 reset_sent_at 锁存，则需等新数据到达才会重试
    （静止场景下永不到达 → 保活失效）。修复后 except 分支置 None，按 idle/2
    tick 粒度重试。
    """
    import types

    fake = types.SimpleNamespace(stream=types.SimpleNamespace(
        idle_reset_seconds=0.2, raw_stream_fallback=False))
    monkeypatch.setattr("app.infrastructure.stream.scrcpy.settings", lambda: fake)

    encoder = ScrcpyEncoder()
    payload = b"\x00\x00\x00\x01a" + b"\xee" * 4
    stream = (
        make_handshake()
        + make_session(1080, 1920)
        + make_frame_header(0, len(payload)) + payload
    )
    stderr = FakeStderr([b"Device: test-device\n"], block=True)
    harness = SubprocessHarness(server_stderr=stderr)
    conn = ConnectionHarness(make_reader(stream, eof=False))  # 首帧后永久静默

    # 控制 socket 首次写抛错（模拟瞬时断管），之后恢复正常
    write_calls = {"n": 0}

    def flaky_write(data: bytes) -> None:
        write_calls["n"] += 1
        if write_calls["n"] == 1:
            raise RuntimeError("transient broken pipe")
        conn.control_writer.written.extend(data)

    encoder._server_manager.push_server = AsyncMock(return_value=True)
    received: list[bytes] = []

    async def consume() -> None:
        async for chunk in encoder.start(DEVICE_ID, EncoderOpts()):
            received.append(chunk)

    with patch("asyncio.create_subprocess_exec", new=harness), \
            patch("asyncio.open_connection", new=conn):
        conn.control_writer.write = flaky_write
        task = asyncio.create_task(consume())
        try:
            # ① 首帧消费（session 建立 _control_sender）
            assert await wait_until(lambda: len(received) == 1)
            # ② 首次保活发送失败一次，但锁存被清除 → tick 重试成功，最终恰为一个 0x11
            assert await wait_until(
                lambda: bytes(conn.control_writer.written) == b"\x11")
            assert write_calls["n"] >= 2  # 失败 + 重试均发生
        finally:
            encoder._running = False  # 下一个 tick 退出取帧循环 → 消费任务自然结束
            await task
    assert harness.server_process.terminate_called