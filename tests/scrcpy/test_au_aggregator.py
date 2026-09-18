"""
AU 聚合器测试
================

测试启发式 Access Unit 聚合（方案 17 实施项 1a 尾步骤）：
    - SPS/PPS/SEI 前缀挂靠后继 VCL
    - 连续 slice 各成新 AU
    - AUD 显式分界
    - 悬空前缀跨批挂靠（pending 返回值）
    - 其他类型并入当前 AU
"""

from app.scrcpy.au_aggregator import aggregate_aus
from app.scrcpy.constants import (
    NALU_TYPE_SLICE,
    NALU_TYPE_IDR,
    NALU_TYPE_SEI,
    NALU_TYPE_SPS,
    NALU_TYPE_PPS,
    NALU_TYPE_AUD,
)


def nalu(nalu_type: int, body: bytes = b'\xab' * 8) -> bytes:
    """构造 4 字节起始码 + NAL 头 + 载荷的 NALU"""
    return b'\x00\x00\x00\x01' + bytes([nalu_type]) + body


def nalu_body(nalu_type: int) -> bytes:
    return bytes([nalu_type]) + b'\xab' * 8


class TestPrefixAttachment:
    """前缀 NALU 挂靠"""

    def test_sps_pps_idr_single_au(self):
        """SPS+PPS+IDR 聚合为一个 AU"""
        aus, pending = aggregate_aus([
            nalu(NALU_TYPE_SPS),
            nalu(NALU_TYPE_PPS),
            nalu(NALU_TYPE_IDR),
        ])
        assert pending == []
        assert len(aus) == 1
        assert aus[0] == (nalu(NALU_TYPE_SPS) + nalu(NALU_TYPE_PPS) + nalu(NALU_TYPE_IDR))

    def test_sei_attaches_to_slice(self):
        """SEI 挂靠后继非 IDR slice"""
        sei, slic = nalu(NALU_TYPE_SEI), nalu(NALU_TYPE_SLICE)
        aus, pending = aggregate_aus([sei, slic])
        assert pending == []
        assert aus == [sei + slic]

    def test_sps_pps_then_multiple_aus(self):
        """前缀只挂第一个 VCL，后续 slice 独立成 AU"""
        n = {t: nalu(t) for t in (NALU_TYPE_SPS, NALU_TYPE_PPS, NALU_TYPE_IDR, NALU_TYPE_SLICE)}
        aus, pending = aggregate_aus([
            n[NALU_TYPE_SPS], n[NALU_TYPE_PPS], n[NALU_TYPE_IDR], n[NALU_TYPE_SLICE],
        ])
        assert pending == []
        assert len(aus) == 2
        assert aus[0] == n[NALU_TYPE_SPS] + n[NALU_TYPE_PPS] + n[NALU_TYPE_IDR]
        assert aus[1] == n[NALU_TYPE_SLICE]


class TestAuBoundaries:
    """AU 边界"""

    def test_consecutive_slices_separate_aus(self):
        """连续两个非 IDR slice 各成新 AU（单 slice/帧假设）"""
        a, b = nalu(NALU_TYPE_SLICE), nalu(NALU_TYPE_SLICE)
        aus, pending = aggregate_aus([a, b])
        assert pending == []
        assert aus == [a, b]

    def test_idr_starts_new_au(self):
        """IDR 是新帧起点"""
        a, b = nalu(NALU_TYPE_SLICE), nalu(NALU_TYPE_IDR)
        aus, pending = aggregate_aus([a, b])
        assert pending == []
        assert aus == [a, b]

    def test_aud_explicit_boundary(self):
        """AUD 显式分界：闭合前一 AU 并作为新 AU 首 NALU"""
        aud, s1, s2 = nalu(NALU_TYPE_AUD), nalu(NALU_TYPE_SLICE), nalu(NALU_TYPE_SLICE)
        aus, pending = aggregate_aus([aud, s1, aud, s2])
        assert pending == []
        assert len(aus) == 2
        assert aus[0] == aud + s1
        assert aus[1] == aud + s2

    def test_trailing_vcl_au_flushed(self):
        """输入末尾的 VCL 视为完整帧输出"""
        a = nalu(NALU_TYPE_SLICE)
        aus, pending = aggregate_aus([a])
        assert pending == []
        assert aus == [a]


class TestPendingCarry:
    """悬空前缀跨批挂靠（起始码跨 WS chunk 场景）"""

    def test_dangling_prefix_returned_as_pending(self):
        """尾部纯前缀（无 VCL）不输出，进入 pending"""
        sps, pps = nalu(NALU_TYPE_SPS), nalu(NALU_TYPE_PPS)
        aus, pending = aggregate_aus([sps, pps])
        assert aus == []
        assert pending == [sps, pps]

    def test_pending_attaches_to_next_batch_vcl(self):
        """上一批 pending 前缀挂靠下一批首个 VCL"""
        sps, pps, idr = (nalu(t) for t in (NALU_TYPE_SPS, NALU_TYPE_PPS, NALU_TYPE_IDR))
        aus1, pending = aggregate_aus([sps, pps])
        aus2, pending2 = aggregate_aus([idr], pending)
        assert pending2 == []
        assert aus1 == []
        assert aus2 == [sps + pps + idr]

    def test_pending_chained_across_three_batches(self):
        """多批悬空链式累积"""
        sps, pps, sei, idr = (
            nalu(t) for t in (NALU_TYPE_SPS, NALU_TYPE_PPS, NALU_TYPE_SEI, NALU_TYPE_IDR))
        aus1, p1 = aggregate_aus([sps])
        aus2, p2 = aggregate_aus([pps], p1)
        aus3, p3 = aggregate_aus([sei], p2)
        aus4, p4 = aggregate_aus([idr], p3)
        assert aus1 == aus2 == aus3 == []
        assert p4 == []
        assert aus4 == [sps + pps + sei + idr]


class TestMisc:
    """其他类型与边界输入"""

    def test_other_types_merge_into_current_au(self):
        """数据分区（type 3）等并入当前 AU"""
        a = nalu(NALU_TYPE_SLICE)
        other = nalu(3)
        aus, pending = aggregate_aus([a, other])
        assert pending == []
        assert aus == [a + other]

    def test_three_byte_start_code(self):
        """3 字节起始码类型识别"""
        sps = b'\x00\x00\x01' + nalu_body(NALU_TYPE_SPS)
        idr = b'\x00\x00\x01' + nalu_body(NALU_TYPE_IDR)
        aus, pending = aggregate_aus([sps, idr])
        assert pending == []
        assert aus == [sps + idr]

    def test_empty_input(self):
        """空输入"""
        aus, pending = aggregate_aus([])
        assert aus == []
        assert pending == []

    def test_prefix_after_vcl_merges_in_band(self):
        """VCL 之后的前缀（异常流形）并入当前 AU，不产生悬空"""
        a = nalu(NALU_TYPE_SLICE)
        sps = nalu(NALU_TYPE_SPS)
        aus, pending = aggregate_aus([a, sps])
        assert pending == []
        assert aus == [a + sps]