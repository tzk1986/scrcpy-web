"""
AU（Access Unit）聚合器
==========================

把 H264Parser 产出的 NALU 列表按启发式聚合为 Access Unit（一帧），
供 WebSocket 按帧发送（方案 17 实施项 1a 尾步骤）。

启发式规则（基于「设备编码器每 AU 单 slice」假设，rk3568 MediaCodec
常态；真机验证失败需回退逐 NALU 发送）：

    - SEI(6)/SPS(7)/PPS(8) 为前缀 NALU，挂靠到后继第一个 VCL；
    - IDR(5) 与非 IDR slice(1) 为新 AU 起点；
    - AUD(9) 显式分界：闭合既有 AU，AUD 作为新 AU 首 NALU；
    - 其余类型（数据分区、序列结束等）并入当前 AU；
    - 输入尾部未闭合的 VCL AU 直接输出（单 slice 假设下即完整帧）；
      无 VCL 可挂靠的悬空前缀通过返回值携带给调用方，与下一批 NALU
      合并（起始码跨 WS 接收 chunk 时 SEI 不与帧切碎）。

纯函数：输入仅依赖参数，产出不修改入参。
"""

from collections.abc import Sequence

from app.scrcpy.constants import (
    NALU_TYPE_SLICE,
    NALU_TYPE_IDR,
    NALU_TYPE_SEI,
    NALU_TYPE_SPS,
    NALU_TYPE_PPS,
    NALU_TYPE_AUD,
)

_VCL_TYPES = frozenset({NALU_TYPE_SLICE, NALU_TYPE_IDR})
_PREFIX_TYPES = frozenset({NALU_TYPE_SEI, NALU_TYPE_SPS, NALU_TYPE_PPS})


def _nalu_type(nalu: bytes) -> int:
    """提取 NALU 类型（NAL 头低 5 位）；无起始码时按裸 NALU 头处理。"""
    if nalu[0:4] == b'\x00\x00\x00\x01':
        pos = 4
    elif nalu[0:3] == b'\x00\x00\x01':
        pos = 3
    else:
        pos = 0
    if pos >= len(nalu):
        return -1
    return nalu[pos] & 0x1F


def aggregate_aus(
    nalus: Sequence[bytes],
    pending: Sequence[bytes] = (),
) -> tuple[list[bytes], list[bytes]]:
    """
    把 NALU 序列聚合为 AU 列表。

    参数：
        nalus: 本轮 H264Parser.feed 产出的 NALU（含起始码）。
        pending: 上一轮返回的悬空前缀 NALU，先于 nalus 参与挂靠。

    返回：
        (aus, new_pending)：
            aus — 完整 AU 列表（每个元素为 Annex B 字节串，可直接 send_bytes）；
            new_pending — 本轮末尾无 VCL 可挂靠的前缀 NALU，交还调用方、
                与下一批 nalus 合并后再进入本函数。
    """
    aus: list[bytes] = []
    prefix: list[bytes] = []
    current: list[bytes] = []
    current_vcl = False  # current 是否已含 VCL（只有已含 VCL 的 AU 才可闭合）

    for nalu in list(pending) + list(nalus):
        nalu_type = _nalu_type(nalu)

        if nalu_type == NALU_TYPE_AUD:
            # 显式分界：闭合既有内容，AUD 成为新 AU 的首 NALU
            if prefix or current:
                aus.append(b''.join(prefix + current))
            prefix = []
            current = [nalu]
            current_vcl = False
        elif nalu_type in _PREFIX_TYPES:
            if current:
                current.append(nalu)
            else:
                prefix.append(nalu)
        elif nalu_type in _VCL_TYPES:
            # 新帧起点：仅当 current 已含 VCL 才闭合（AUD 后首个
            # VCL 属同一 AU，不闭合）
            if current_vcl:
                aus.append(b''.join(prefix + current))
                prefix = []
                current = []
            current.extend(prefix)
            prefix = []
            current.append(nalu)
            current_vcl = True
        else:
            if current:
                current.append(nalu)
            else:
                prefix.append(nalu)

    # 收尾：含 VCL 的未闭合 AU 视为完整帧输出；
    # 无 VCL 的悬空内容（前缀/AUD）交还调用方，与下一批合并
    if current_vcl:
        aus.append(b''.join(prefix + current))
        return aus, []

    return aus, prefix + current