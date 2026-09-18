"""
scrcpy-server v4.1 流协议解析测试（12 字节包头）
================================================

覆盖 stream_protocol 模块的纯函数与 read_packets 异步切包器：

协议事实（官方源码 fa57d7c6：server Streamer.java / app demuxer.c）：
    - 每流起始 4B codec id（"h264" = 0x68323634 大端）
    - 视频流其后 12B session 包：bit63 标志 + 宽高（offset 4/8，大端 uint32）
    - 媒体/配置包：12B 头 = 8B PTS/flags（bit62=config、bit61=keyframe）
      + 4B 载荷长度（大端）+ 载荷
    - 握手（tunnel_forward 模式）：1B dummy + 64B 设备名先于 codec id

测试不依赖真实 adb/socket：使用 asyncio.StreamReader.feed_data 离线喂字节。
"""

import asyncio
import struct

import pytest

from app.scrcpy.stream_protocol import (
    SC_PACKET_HEADER_SIZE,
    SC_PACKET_FLAG_CONFIG,
    SC_PACKET_FLAG_KEY_FRAME,
    SC_PACKET_FLAG_SESSION,
    SC_CODEC_ID_H264,
    SC_CODEC_ID_DISABLED,
    SC_CODEC_ID_ERROR,
    SessionEvent,
    MediaEvent,
    StreamDisabled,
    StreamConfigError,
    UnsupportedCodecError,
    is_session_packet,
    parse_session,
    parse_frame_header,
    read_packets,
)


def make_header(pts_flags: int, size: int) -> bytes:
    """构造 12 字节媒体包头（8B PTS/flags + 4B 载荷长度，大端）。"""
    return struct.pack(">QI", pts_flags, size)


def make_session(width: int, height: int, client_resized: bool = False) -> bytes:
    """构造 12 字节 session 包头（bit63 置位 + 4B width + 4B height）。"""
    flags = (1 << 31) | (1 if client_resized else 0)
    return struct.pack(">III", flags, width, height)


def make_codec_id(codec_id: int) -> bytes:
    return struct.pack(">I", codec_id)


def make_reader(data: bytes) -> asyncio.StreamReader:
    """构造喂入完整字节序的 StreamReader（含 EOF）。"""
    reader = asyncio.StreamReader()
    reader.feed_data(data)
    reader.feed_eof()
    return reader


class TestSessionParsing:
    """session 包识别与解析"""

    def test_session_flag_detected(self):
        """bit63 置位判定为 session 包"""
        assert is_session_packet(make_session(1080, 1920))
        assert not is_session_packet(make_header(0, 100))

    def test_session_parses_width_height(self):
        """宽高为大端 uint32，位于 offset 4/8"""
        w, h, resized = parse_session(make_session(1080, 1920))
        assert (w, h, resized) == (1080, 1920, False)

    def test_session_client_resized_flag(self):
        """byte3 最低位为 client_resized"""
        w, h, resized = parse_session(make_session(720, 1280, client_resized=True))
        assert (w, h) == (720, 1280)
        assert resized is True


class TestFrameHeaderParsing:
    """媒体/配置包头解析"""

    def test_config_flag(self):
        """bit62 置位为 config 包，size 从 offset 8 读取"""
        fh = parse_frame_header(make_header(SC_PACKET_FLAG_CONFIG, 256))
        assert fh.is_config is True
        assert fh.is_key is False
        assert fh.payload_size == 256

    def test_keyframe_flag(self):
        """bit61 置位为关键帧，PTS 为 flags 掩掉高两位后的值"""
        pts = 123456789
        fh = parse_frame_header(make_header(pts | SC_PACKET_FLAG_KEY_FRAME, 512))
        assert fh.is_config is False
        assert fh.is_key is True
        assert fh.pts == pts

    def test_pts_mask_keeps_lower_bits(self):
        """PTS 只保留 bit61 以下（SC_PACKET_PTS_MASK）"""
        fh = parse_frame_header(make_header((1 << 61) - 1, 64))
        assert fh.pts == (1 << 61) - 1
        assert fh.is_config is False
        assert fh.is_key is False

    def test_header_size_constant(self):
        """包头恒定 12 字节"""
        assert SC_PACKET_HEADER_SIZE == 12
        assert SC_PACKET_FLAG_SESSION == 1 << 63
        assert SC_PACKET_FLAG_CONFIG == 1 << 62
        assert SC_PACKET_FLAG_KEY_FRAME == 1 << 61


class TestReadPackets:
    """read_packets 切包器：握手 + 按 size 切包"""

    async def _collect(self, data: bytes) -> list[SessionEvent | MediaEvent]:
        return [ev async for ev in read_packets(make_reader(data))]

    async def test_handshake_then_session_then_packets(self):
        """完整流：dummy + 64B 设备名 + codec + session + 2 个媒体包"""
        payload_a = b"\x00\x00\x00\x01" + bytes([0x65]) + b"\xaa" * 100
        payload_b = b"\x00\x00\x00\x01" + bytes([0x61]) + b"\xbb" * 50
        stream = (
            b"\x00"                                   # dummy byte
            + b"test-device".ljust(64, b"\x00")       # 64B 设备名
            + make_codec_id(SC_CODEC_ID_H264)         # codec id
            + make_session(1080, 1920)                # 首 session
            + make_header(SC_PACKET_FLAG_KEY_FRAME, len(payload_a)) + payload_a  # 关键帧
            + make_header(0, len(payload_b)) + payload_b                        # delta 帧
        )
        events = await self._collect(stream)

        assert len(events) == 3
        s, a, b = events
        assert isinstance(s, SessionEvent) and (s.width, s.height) == (1080, 1920)
        assert isinstance(a, MediaEvent) and a.payload == payload_a
        assert a.is_key and not a.is_config
        assert isinstance(b, MediaEvent) and b.payload == payload_b
        assert not b.is_key and not b.is_config

    async def test_config_packet_marked(self):
        """config 包（bit62）与媒体包分离标记"""
        sps = b"\x00\x00\x00\x01g" + b"\x00" * 20
        stream = (
            b"\x00"
            + b"x".ljust(64, b"\x00")
            + make_codec_id(SC_CODEC_ID_H264)
            + make_session(720, 1280)
            + make_header(SC_PACKET_FLAG_CONFIG, len(sps)) + sps
        )
        events = await self._collect(stream)
        assert len(events) == 2
        cfg = events[1]
        assert isinstance(cfg, MediaEvent)
        assert cfg.is_config and not cfg.is_key
        assert cfg.payload == sps

    async def test_chunked_feed_never_loses_packets(self):
        """逐字节喂入（跨 chunk 头/载荷）仍完整切包"""
        header = make_header(0, 30)
        payload = b"\x00\x00\x00\x01" + bytes([0x41]) + b"\xcc" * 25
        stream = (
            b"\x00" + b"y".ljust(64, b"\x00") + make_codec_id(SC_CODEC_ID_H264)
            + make_session(800, 600) + header + payload
        )
        reader = asyncio.StreamReader()
        for i in range(len(stream)):
            reader.feed_data(stream[i:i + 1])
        reader.feed_eof()

        events = [ev async for ev in read_packets(reader)]
        media = events[-1]
        assert isinstance(media, MediaEvent)
        assert media.payload == payload

    async def test_session_repeat_after_streaming(self):
        """流中重复 session 包（resize/重启）作为独立事件产出"""
        stream = (
            b"\x00" + b"z".ljust(64, b"\x00") + make_codec_id(SC_CODEC_ID_H264)
            + make_session(1080, 1920)
            + make_header(0, 5) + b"\x00" * 5
            + make_session(1080, 2160, client_resized=True)
        )
        events = await self._collect(stream)
        sessions = [e for e in events if isinstance(e, SessionEvent)]
        assert len(sessions) == 2
        assert sessions[1].height == 2160
        assert sessions[1].client_resized is True

    async def test_empty_payload_packet(self):
        """0 长度载荷合法产出（服务端不会发，但切包器不应崩溃）"""
        stream = (
            b"\x00" + b"e".ljust(64, b"\x00") + make_codec_id(SC_CODEC_ID_H264)
            + make_session(10, 10)
            + make_header(0, 0)
        )
        events = await self._collect(stream)
        assert events[-1].payload == b""

    async def test_disabled_stream_raises(self):
        """codec id=0（设备禁用视频流）抛 StreamDisabled"""
        stream = b"\x00" + b"d".ljust(64, b"\x00") + make_codec_id(SC_CODEC_ID_DISABLED)
        with pytest.raises(StreamDisabled):
            async for _ in read_packets(make_reader(stream)):
                pass

    async def test_config_error_raises(self):
        """codec id=1（配置错误）抛 StreamConfigError"""
        stream = b"\x00" + b"c".ljust(64, b"\x00") + make_codec_id(SC_CODEC_ID_ERROR)
        with pytest.raises(StreamConfigError):
            async for _ in read_packets(make_reader(stream)):
                pass

    async def test_unsupported_codec_raises(self):
        """未知 codec id 抛 UnsupportedCodecError"""
        stream = b"\x00" + b"u".ljust(64, b"\x00") + make_codec_id(0xDEADBEEF)
        with pytest.raises(UnsupportedCodecError):
            async for _ in read_packets(make_reader(stream)):
                pass

    async def test_eof_mid_header_ends_quietly(self):
        """头部读到一半 EOF：正常结束（设备断开）"""
        stream = b"\x00" + b"t".ljust(64, b"\x00") + make_codec_id(SC_CODEC_ID_H264)
        reader = make_reader(stream)  # 无 session 无包，直接 EOF
        events = [ev async for ev in read_packets(reader)]
        assert events == []