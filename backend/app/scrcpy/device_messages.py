"""
scrcpy device→host DeviceMessage 帧解析
========================================

控制 socket 反向通道（tunnel_forward 全双工，方案 54 G3-B）。
帧格式与上游 v5.0.1 DeviceMessage.java 一致（留档 logs/upstream/）：

    CLIPBOARD:      type(1)=0 + len(4) + utf8
    ACK_CLIPBOARD:  type(1)=1 + sequence(8)
    UHID_OUTPUT:    type(1)=2 + id(2) + len(2) + data（本项目不用，消费保帧边界）
"""

import asyncio
import struct
from dataclasses import dataclass

TYPE_CLIPBOARD = 0
TYPE_ACK_CLIPBOARD = 1
TYPE_UHID_OUTPUT = 2

# 与上游 ControlMessageReader.MESSAGE_MAX_SIZE 同量级，防畸形长度撑爆内存
_TEXT_MAX_LENGTH = (1 << 18) - 14


@dataclass
class DeviceMessage:
    """一条已解析的 device→host 消息。"""

    kind: int
    text: str | None = None
    sequence: int | None = None


async def read_device_message(reader: asyncio.StreamReader) -> DeviceMessage | None:
    """读取一整个 DeviceMessage 帧；EOF（含半包断连）返回 None。

    未知 type 抛 ValueError：帧体长度无从得知，继续读必然破坏帧边界，
    由调用侧结束循环。
    """
    head = await _read_exact(reader, 1)
    if head is None:
        return None
    kind = head[0]

    if kind == TYPE_CLIPBOARD:
        length_bytes = await _read_exact(reader, 4)
        if length_bytes is None:
            return None
        (length,) = struct.unpack(">I", length_bytes)
        if length > _TEXT_MAX_LENGTH:
            raise ValueError(f"clipboard frame too long: {length}")
        body = await _read_exact(reader, length) if length else b""
        if body is None:
            return None
        return DeviceMessage(kind, text=body.decode("utf-8", errors="replace"))

    if kind == TYPE_ACK_CLIPBOARD:
        seq_bytes = await _read_exact(reader, 8)
        if seq_bytes is None:
            return None
        (sequence,) = struct.unpack(">q", seq_bytes)
        return DeviceMessage(kind, sequence=sequence)

    if kind == TYPE_UHID_OUTPUT:
        prefix = await _read_exact(reader, 4)
        if prefix is None:
            return None
        _, length = struct.unpack(">HH", prefix)
        if length > _TEXT_MAX_LENGTH:
            raise ValueError(f"uhid frame too long: {length}")
        data = await _read_exact(reader, length) if length else b""
        if data is None:
            return None
        return DeviceMessage(kind)

    raise ValueError(f"unknown device message type: {kind}")


async def _read_exact(reader: asyncio.StreamReader, n: int) -> bytes | None:
    """readexactly 包装：EOF（含零字节与半包）统一返回 None。

    readexactly(0) 在未 EOF 时会立即抛 IncompleteReadError（partial=b""），
    零长度须在调用侧短路，不能走到这里。
    """
    try:
        return await reader.readexactly(n)
    except asyncio.IncompleteReadError:
        return None
