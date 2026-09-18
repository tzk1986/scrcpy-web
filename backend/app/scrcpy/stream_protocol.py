"""
scrcpy-server v4.1 流协议解析（12 字节包头）
=============================================

按官方源码 fa57d7c6（server/src/.../device/Streamer.java、
app/src/demuxer.c）逐字段实现的流协议切包器。

协议事实：
    - send_stream_meta：每流起始 4B codec id（大端）；"h264" = 0x68323634，
      id 0 表示设备禁用该流、id 1 表示配置错误（需中止）
    - 视频流其后为 12B session 包：bit63 置位、byte3 最低位为
      client_resized、offset 4/8 为大端 uint32 宽高；编码器重启
      （resize / 码率切换）时会再次出现 session 包
    - 媒体/配置包 12B 头：8B PTS/flags（大端；bit62=config 包、
      bit61=关键帧、其余为 PTS）+ 4B 载荷长度（大端）+ 载荷
    - config 包载荷为编码器 CODEC_CONFIG 输出（Annex B SPS/PPS，
      带起始码），媒体包载荷为 Annex B 帧数据
    - tunnel_forward 模式建连后客户端先收到 1B dummy byte 与
      64B 设备名，之后才是 codec id

纯解析函数无 I/O；read_packets 为异步切包迭代器，
输入 asyncio.StreamReader（测试可直接 feed_data 离线喂字节）。
"""

import asyncio
import struct
from collections.abc import AsyncIterator
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# 协议常量
# ---------------------------------------------------------------------------

SC_PACKET_HEADER_SIZE = 12                    # 包头长度（字节）

SC_PACKET_FLAG_SESSION = 1 << 63              # bit63：session 包（宽高元数据）
SC_PACKET_FLAG_CONFIG = 1 << 62               # bit62：config 包（SPS/PPS）
SC_PACKET_FLAG_KEY_FRAME = 1 << 61            # bit61：关键帧
SC_PACKET_PTS_MASK = SC_PACKET_FLAG_KEY_FRAME - 1  # PTS 有效位

SC_CODEC_ID_H264 = 0x68323634                 # "h264"（大端）
SC_CODEC_ID_DISABLED = 0                      # 设备显式禁用该流
SC_CODEC_ID_ERROR = 1                         # 设备端配置错误，必须中止

SC_DEVICE_META_SIZE = 64                      # 设备名元数据长度（字节）

# 建连后先于 codec id 的握手数据长度（tunnel_forward + 默认 meta 开关）
SC_HANDSHAKE_PREFIX_SIZE = 1 + SC_DEVICE_META_SIZE


# ---------------------------------------------------------------------------
# 协议异常
# ---------------------------------------------------------------------------

class StreamDisabled(Exception):
    """设备显式禁用视频流（codec id = 0），镜像可继续但不推视频。"""


class StreamConfigError(Exception):
    """设备端配置错误（codec id = 1），必须中止。"""


class UnsupportedCodecError(Exception):
    """设备使用了不支持解码的 codec id。"""

    def __init__(self, codec_id: int) -> None:
        super().__init__(f"unsupported codec id: 0x{codec_id:08X}")
        self.codec_id = codec_id


# ---------------------------------------------------------------------------
# 事件类型
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SessionEvent:
    """session 包事件：流（重）起始元数据。"""
    width: int
    height: int
    client_resized: bool


@dataclass(frozen=True)
class MediaEvent:
    """媒体/配置包事件：完整包载荷。"""
    is_config: bool
    is_key: bool
    pts: int
    payload: bytes


@dataclass(frozen=True)
class FrameHeader:
    """媒体/配置包头解析结果。"""
    pts: int
    is_config: bool
    is_key: bool
    payload_size: int


# ---------------------------------------------------------------------------
# 纯解析函数
# ---------------------------------------------------------------------------

def is_session_packet(header: bytes) -> bool:
    """判定 12 字节头是否为 session 包（bit63）。"""
    return bool(header[0] & 0x80)


def parse_session(header: bytes) -> tuple[int, int, bool]:
    """
    解析 session 包头。

    返回：
        (width, height, client_resized)：宽高大端 uint32（offset 4/8），
        client_resized 为 byte3 最低位。
    """
    width = struct.unpack(">I", header[4:8])[0]
    height = struct.unpack(">I", header[8:12])[0]
    client_resized = bool(header[3] & 1)
    return width, height, client_resized


def parse_frame_header(header: bytes) -> FrameHeader:
    """
    解析媒体/配置包头。

    返回：
        FrameHeader：PTS 为 flags 掩掉 bit62/61 后的值；
        is_config / is_key 分别对应 bit62 / bit61；
        payload_size 为大端 uint32（offset 8）。
    """
    pts_flags = struct.unpack(">Q", header[:8])[0]
    return FrameHeader(
        pts=pts_flags & SC_PACKET_PTS_MASK,
        is_config=bool(pts_flags & SC_PACKET_FLAG_CONFIG),
        is_key=bool(pts_flags & SC_PACKET_FLAG_KEY_FRAME),
        payload_size=struct.unpack(">I", header[8:12])[0],
    )


# ---------------------------------------------------------------------------
# 异步切包器
# ---------------------------------------------------------------------------

async def read_packets(reader: asyncio.StreamReader) -> AsyncIterator[SessionEvent | MediaEvent]:
    """
    从视频 socket 流读取并切分完整协议包。

    流程：
        握手（1B dummy + 64B 设备名 + 4B codec id 校验）→ 循环读
        12B 头 → session 包产出 SessionEvent；其余读出整包载荷产出
        MediaEvent。EOF 中段（头读到一半）视为设备断开，正常结束。

    异常：
        StreamDisabled / StreamConfigError / UnsupportedCodecError
        分别对应 codec id 0 / 1 / 未知值。
    """
    # 握手：dummy byte + 设备名（仅 tunnel_forward 场景存在，
    # 由启动参数 send_dummy_byte/send_device_meta 默认开启决定）
    await reader.readexactly(1)                     # dummy byte（内容无意义）
    await reader.readexactly(SC_DEVICE_META_SIZE)   # 设备名（UTF-8，null 填充）

    codec_id = struct.unpack(">I", await reader.readexactly(4))[0]
    if codec_id == SC_CODEC_ID_DISABLED:
        raise StreamDisabled()
    if codec_id == SC_CODEC_ID_ERROR:
        raise StreamConfigError()
    if codec_id != SC_CODEC_ID_H264:
        raise UnsupportedCodecError(codec_id)

    while True:
        try:
            header = await reader.readexactly(SC_PACKET_HEADER_SIZE)
        except asyncio.IncompleteReadError:
            return  # 设备断开 / 流结束

        if is_session_packet(header):
            width, height, client_resized = parse_session(header)
            yield SessionEvent(width=width, height=height, client_resized=client_resized)
            continue

        fh = parse_frame_header(header)
        try:
            payload = await reader.readexactly(fh.payload_size)
        except asyncio.IncompleteReadError:
            return  # 载荷不完整（异常断开），丢弃半包
        yield MediaEvent(is_config=fh.is_config, is_key=fh.is_key, pts=fh.pts, payload=payload)