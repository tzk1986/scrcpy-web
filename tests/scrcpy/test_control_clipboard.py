"""
方案 54 T1：剪贴板控制消息与 DeviceMessage 反向解析
====================================================

覆盖：
- ControlSender.set_clipboard/get_clipboard 字节级封包（比对上游 v5.0.1 线格式）
- device_messages.read_device_message 帧解析（CLIPBOARD/ACK/UHID/EOF/未知类型/半包）
- send_input：key 扩展 down/repeat/meta 与旧格式向后兼容、scroll、clipboard_set
- get_device_clipboard：FIFO 关联 / 超时 / 无控制通道
- _read_control_messages 循环分发与 EOF 收口
- 兜底链：key UP 在 keyevent 兜底跳过、clipboard_set 降级 input text
"""

import asyncio
import struct
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.infrastructure.stream.scrcpy import ScrcpyEncoder
from app.scrcpy.control_sender import (
    ACTION_DOWN,
    ACTION_UP,
    ControlSender,
)
from app.scrcpy.device_messages import (
    TYPE_ACK_CLIPBOARD,
    TYPE_CLIPBOARD,
    TYPE_UHID_OUTPUT,
    DeviceMessage,
    read_device_message,
)

DEVICE_ID = "emulator-5554"


class FakeWriter:
    """StreamWriter 假体：write 累积到 written。"""

    def __init__(self) -> None:
        self.written = bytearray()

    def write(self, data: bytes) -> None:
        self.written.extend(data)

    async def drain(self) -> None:
        pass


def make_sender() -> tuple[ControlSender, FakeWriter]:
    writer = FakeWriter()
    return ControlSender(writer, (1080, 1920)), writer


# ---------------------------------------------------------------------------
# ControlSender 剪贴板封包（线格式实证：logs/upstream/ControlMessageReader.java）
# ---------------------------------------------------------------------------

async def test_set_clipboard_wire_format_paste_true():
    """SET_CLIPBOARD: type(9) + sequence(8) + paste(1) + len(4) + utf8。"""
    sender, writer = make_sender()
    text = "héllo中文"
    await sender.set_clipboard(text, paste=True)

    buf = text.encode("utf-8")
    expected = (
        struct.pack(">Bq", 9, 0) + b"\x01" + struct.pack(">i", len(buf)) + buf
    )
    assert bytes(writer.written) == expected


async def test_set_clipboard_wire_format_paste_false_default_sequence():
    sender, writer = make_sender()
    await sender.set_clipboard("abc")

    expected = struct.pack(">BqB", 9, 0, 0) + struct.pack(">i", 3) + b"abc"
    assert bytes(writer.written) == expected


async def test_get_clipboard_wire_format():
    """GET_CLIPBOARD: type(8) + copyKey(1)。"""
    sender, writer = make_sender()
    await sender.get_clipboard(copy_key=1)
    assert bytes(writer.written) == b"\x08\x01"

    sender2, writer2 = make_sender()
    await sender2.get_clipboard()
    assert bytes(writer2.written) == b"\x08\x00"


# ---------------------------------------------------------------------------
# DeviceMessage 帧解析
# ---------------------------------------------------------------------------

def make_reader(data: bytes, eof: bool = True) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    reader.feed_data(data)
    if eof:
        reader.feed_eof()
    return reader


async def test_read_device_message_clipboard():
    reader = make_reader(struct.pack(">BI", TYPE_CLIPBOARD, 4) + b"copy")
    msg = await read_device_message(reader)
    assert msg is not None
    assert msg.kind == TYPE_CLIPBOARD
    assert msg.text == "copy"


async def test_read_device_message_clipboard_empty_text():
    reader = make_reader(struct.pack(">BI", TYPE_CLIPBOARD, 0))
    msg = await read_device_message(reader)
    assert msg is not None and msg.text == ""


async def test_read_device_message_ack():
    reader = make_reader(struct.pack(">Bq", TYPE_ACK_CLIPBOARD, 42))
    msg = await read_device_message(reader)
    assert msg is not None
    assert msg.kind == TYPE_ACK_CLIPBOARD
    assert msg.sequence == 42


async def test_read_device_message_uhid_output_consumes_payload():
    payload = struct.pack(">HH", 1, 3) + b"\x01\x02\x03"
    reader = make_reader(bytes([TYPE_UHID_OUTPUT]) + payload)
    msg = await read_device_message(reader)
    assert msg is not None and msg.kind == TYPE_UHID_OUTPUT
    assert reader.at_eof()  # 整帧消耗，帧边界不破坏


async def test_read_device_message_eof_returns_none():
    reader = make_reader(b"")
    assert await read_device_message(reader) is None


async def test_read_device_message_partial_eof_returns_none():
    """半包后 EOF（连接断开）：返回 None 而非抛错。"""
    reader = make_reader(struct.pack(">BI", TYPE_CLIPBOARD, 10) + b"abc")
    assert await read_device_message(reader) is None


async def test_read_device_message_unknown_type_raises():
    reader = make_reader(b"\x07")
    with pytest.raises(ValueError):
        await read_device_message(reader)


async def test_read_device_message_half_pack_waits_for_rest():
    reader = make_reader(struct.pack(">BI", TYPE_CLIPBOARD, 2) + b"a", eof=False)
    task = asyncio.create_task(read_device_message(reader))
    await asyncio.sleep(0)
    assert not task.done()
    reader.feed_data(b"b")
    reader.feed_eof()
    msg = await task
    assert msg is not None and msg.text == "ab"


# ---------------------------------------------------------------------------
# send_input 扩展分发
# ---------------------------------------------------------------------------

def make_control_encoder() -> tuple[ScrcpyEncoder, MagicMock]:
    encoder = ScrcpyEncoder()
    encoder._device_id = DEVICE_ID
    encoder._resolution = (1080, 1920)
    sender = MagicMock()
    sender.touch = AsyncMock()
    sender.keycode = AsyncMock()
    sender.text = AsyncMock()
    sender.scroll = AsyncMock()
    sender.set_clipboard = AsyncMock()
    sender.get_clipboard = AsyncMock()
    encoder._control_sender = sender
    return encoder, sender


async def test_send_input_key_without_down_pairs_back_compat():
    """旧格式（无 down 字段）：维持 DOWN+UP 成对，repeat/meta 缺省 0。"""
    encoder, sender = make_control_encoder()
    await encoder.send_input({"action": "key", "keycode": 4})
    calls = [c.args for c in sender.keycode.await_args_list]
    assert calls == [(4, ACTION_DOWN, 0, 0), (4, ACTION_UP, 0, 0)]


async def test_send_input_key_down_true_sends_single_with_repeat_meta():
    encoder, sender = make_control_encoder()
    await encoder.send_input(
        {"action": "key", "keycode": 68, "down": True, "repeat": 2, "meta": 1}
    )
    assert [c.args for c in sender.keycode.await_args_list] == [(68, ACTION_DOWN, 2, 1)]


async def test_send_input_key_down_false_sends_up_only():
    encoder, sender = make_control_encoder()
    await encoder.send_input({"action": "key", "keycode": 68, "down": False, "meta": 16})
    assert [c.args for c in sender.keycode.await_args_list] == [(68, ACTION_UP, 0, 16)]


async def test_send_input_key_down_none_meta_passthrough_on_pair():
    """down 缺省 + meta：成对发送两帧同 meta。"""
    encoder, sender = make_control_encoder()
    await encoder.send_input({"action": "key", "keycode": 29, "meta": 1})
    assert [c.args for c in sender.keycode.await_args_list] == [
        (29, ACTION_DOWN, 0, 1),
        (29, ACTION_UP, 0, 1),
    ]


async def test_send_input_scroll_dispatches():
    encoder, sender = make_control_encoder()
    await encoder.send_input(
        {"action": "scroll", "x": 500, "y": 800, "h_scroll": 0, "v_scroll": -1}
    )
    sender.scroll.assert_awaited_once_with(500, 800, 0, -1)


async def test_send_input_clipboard_set_dispatches():
    encoder, sender = make_control_encoder()
    await encoder.send_input({"action": "clipboard_set", "text": "中文", "paste": True})
    sender.set_clipboard.assert_awaited_once_with("中文", paste=True)


async def test_send_input_clipboard_set_falls_back_without_control():
    """控制不可用：clipboard_set 走兜底链（不静默丢弃，硬约束）。"""
    encoder, _ = make_control_encoder()
    encoder._control_sender = None
    encoder._fallback_adb_input = AsyncMock()
    await encoder.send_input({"action": "clipboard_set", "text": "x"})
    encoder._fallback_adb_input.assert_awaited_once_with(
        {"action": "clipboard_set", "text": "x"}
    )


async def test_send_input_key_fallback_swallows_up_after_control_failure():
    """控制发送抛错 → 兜底；兜底侧 down=False 不再补敲（防双触发）。"""
    encoder, sender = make_control_encoder()
    sender.keycode = AsyncMock(side_effect=OSError("boom"))
    encoder._fallback_adb_input = AsyncMock()
    await encoder.send_input({"action": "key", "keycode": 4, "down": False})
    encoder._fallback_adb_input.assert_awaited_once()


# ---------------------------------------------------------------------------
# 兜底链细节（_fallback_adb_input）
# ---------------------------------------------------------------------------

async def run_fallback(encoder: ScrcpyEncoder, data: dict) -> list:
    with patch("asyncio.create_subprocess_exec", new=AsyncMock()) as exec_mock:
        await encoder._fallback_adb_input(data)
    return exec_mock.await_args_list


async def test_fallback_key_up_skipped():
    encoder = ScrcpyEncoder()
    encoder._device_id = DEVICE_ID
    calls = await run_fallback(encoder, {"action": "key", "keycode": 4, "down": False})
    assert calls == []


async def test_fallback_key_down_or_legacy_launches_keyevent():
    encoder = ScrcpyEncoder()
    encoder._device_id = DEVICE_ID
    for data in (
        {"action": "key", "keycode": 4},
        {"action": "key", "keycode": 4, "down": True},
    ):
        calls = await run_fallback(encoder, data)
        assert len(calls) == 1
        assert "keyevent" in calls[0].args
        assert "4" in calls[0].args


async def test_fallback_clipboard_set_uses_input_text():
    encoder = ScrcpyEncoder()
    encoder._device_id = DEVICE_ID
    calls = await run_fallback(
        encoder, {"action": "clipboard_set", "text": "abc", "paste": True}
    )
    assert len(calls) == 1
    args = [str(a) for a in calls[0].args]
    assert "input" in args and "text" in args and "abc" in args


# ---------------------------------------------------------------------------
# get_device_clipboard：FIFO 关联与超时
# ---------------------------------------------------------------------------

async def test_get_device_clipboard_without_sender_returns_none():
    encoder = ScrcpyEncoder()
    encoder._device_id = DEVICE_ID
    assert await encoder.get_device_clipboard() is None


async def test_get_device_clipboard_resolves_pending_fifo():
    encoder, sender = make_control_encoder()
    task = asyncio.create_task(encoder.get_device_clipboard(copy_key=1))
    await asyncio.sleep(0)
    assert len(encoder._clipboard_waiters) == 1
    sender.get_clipboard.assert_awaited_once_with(1)

    encoder._dispatch_device_message(DeviceMessage(TYPE_CLIPBOARD, text="first"))
    # 第二条 CLIPBOARD 无等待者：丢弃不崩
    encoder._dispatch_device_message(DeviceMessage(TYPE_CLIPBOARD, text="stray"))
    assert await task == "first"
    assert not encoder._clipboard_waiters


async def test_get_device_clipboard_timeout_clears_waiter():
    encoder, _ = make_control_encoder()
    result = await encoder.get_device_clipboard(timeout=0.05)
    assert result is None
    assert not encoder._clipboard_waiters


async def test_dispatch_ack_does_not_consume_clipboard_waiter():
    encoder, _ = make_control_encoder()
    loop = asyncio.get_running_loop()
    fut: asyncio.Future = loop.create_future()
    encoder._clipboard_waiters.append(fut)
    encoder._dispatch_device_message(DeviceMessage(TYPE_ACK_CLIPBOARD, sequence=7))
    assert not fut.done()
    assert len(encoder._clipboard_waiters) == 1


async def test_control_reader_loop_dispatches_frame_and_fails_pending_on_eof():
    """reader 循环：帧 → 等待者；EOF/异常 → 在途等待者以 None 收口。"""
    encoder, _ = make_control_encoder()
    frame = struct.pack(">BI", TYPE_CLIPBOARD, 2) + b"ok"
    encoder._control_reader = make_reader(frame)
    loop = asyncio.get_running_loop()
    matched: asyncio.Future = loop.create_future()
    encoder._clipboard_waiters.append(matched)

    await encoder._read_control_messages()

    assert matched.result() == "ok"

    encoder._control_reader = make_reader(b"")
    pending: asyncio.Future = loop.create_future()
    encoder._clipboard_waiters.append(pending)
    await encoder._read_control_messages()
    assert pending.result() is None


async def test_control_reader_loop_survives_dispatch_after_no_waiter():
    encoder, _ = make_control_encoder()
    frame = struct.pack(">BI", TYPE_CLIPBOARD, 2) + b"zz"
    encoder._control_reader = make_reader(frame)
    await encoder._read_control_messages()  # 不抛错即通过
