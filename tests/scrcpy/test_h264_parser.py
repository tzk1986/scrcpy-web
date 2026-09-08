"""
H264 帧解析器测试
==================

测试 H264 NALU 解析器的功能。

测试内容：
    - 单个 NALU 解析
    - 多个 NALU 连续解析
    - 跨块 NALU 解析
    - NALU 类型识别
    - 起始码检测（3字节和4字节）
    - 缓冲区和重置功能
"""

import pytest

from app.scrcpy.h264_parser import H264Parser
from app.scrcpy.constants import (
    NALU_TYPE_IDR,
    NALU_TYPE_SPS,
    NALU_TYPE_PPS,
    NALU_TYPE_SLICE,
)


# ---------------------------------------------------------------------------
# 基础解析测试
# ---------------------------------------------------------------------------

class TestH264ParserBasic:
    """H264 解析器基础功能测试"""

    def test_parse_single_sps(self, sample_h264_sps):
        """测试解析单个 SPS NALU"""
        parser = H264Parser()
        nalus = parser.feed(sample_h264_sps)

        # SPS 不完整（没有下一个起始码），应返回空列表
        assert len(nalus) == 0

    def test_parse_two_nalus(self, sample_h264_sps, sample_h264_pps):
        """测试解析两个连续的 NALU"""
        parser = H264Parser()
        data = sample_h264_sps + sample_h264_pps
        nalus = parser.feed(data)

        # 应该解析出第一个 NALU（SPS）
        assert len(nalus) == 1
        assert parser.is_sps(nalus[0])

    def test_parse_three_nalus(self, sample_h264_stream):
        """测试解析三个连续的 NALU"""
        parser = H264Parser()
        nalus = []

        # 分块喂入数据
        chunks = [
            sample_h264_stream[:20],
            sample_h264_stream[20:40],
            sample_h264_stream[40:],
        ]

        for chunk in chunks:
            result = parser.feed(chunk)
            nalus.extend(result)

        # 应该解析出前两个 NALU（SPS 和 PPS）
        assert len(nalus) == 2
        assert parser.is_sps(nalus[0])
        assert parser.is_pps(nalus[1])

    def test_parse_complete_stream(self, sample_h264_sps, sample_h264_pps, sample_h264_idr):
        """测试解析完整的 H264 流"""
        parser = H264Parser()

        # 喂入完整的数据，然后在末尾添加一个假的起始码来触发解析
        data = sample_h264_sps + sample_h264_pps + sample_h264_idr + b'\x00\x00\x00\x01'
        nalus = parser.feed(data)

        # 应该解析出三个 NALU
        assert len(nalus) == 3
        assert parser.is_sps(nalus[0])
        assert parser.is_pps(nalus[1])
        assert parser.is_key_frame(nalus[2])


# ---------------------------------------------------------------------------
# 起始码测试
# ---------------------------------------------------------------------------

class TestStartCodeDetection:
    """起始码检测测试"""

    def test_3_byte_start_code(self):
        """测试 3 字节起始码（0x00 0x00 0x01）"""
        parser = H264Parser()

        # 使用 3 字节起始码
        data = b'\x00\x00\x01\x67\x42\x00\x1e'  # SPS with 3-byte start code
        data += b'\x00\x00\x01\x68\xce\x38\x80'  # PPS with 3-byte start code

        nalus = parser.feed(data)

        # 应该解析出第一个 NALU
        assert len(nalus) == 1
        assert nalus[0].startswith(b'\x00\x00\x01')

    def test_4_byte_start_code(self, sample_h264_sps, sample_h264_pps):
        """测试 4 字节起始码（0x00 0x00 0x00 0x01）"""
        parser = H264Parser()
        nalus = parser.feed(sample_h264_sps + sample_h264_pps)

        # 应该解析出第一个 NALU
        assert len(nalus) == 1
        assert nalus[0].startswith(b'\x00\x00\x00\x01')

    def test_mixed_start_codes(self):
        """测试混合起始码"""
        parser = H264Parser()

        # 第一个 NALU 使用 4 字节起始码，第二个使用 3 字节起始码
        data = b'\x00\x00\x00\x01\x67\x42\x00\x1e'  # SPS (4-byte)
        data += b'\x00\x00\x01\x68\xce\x38\x80'      # PPS (3-byte)

        nalus = parser.feed(data)

        assert len(nalus) == 1
        assert parser.is_sps(nalus[0])


# ---------------------------------------------------------------------------
# NALU 类型识别测试
# ---------------------------------------------------------------------------

class TestNaluTypeIdentification:
    """NALU 类型识别测试"""

    def test_identify_sps(self, sample_h264_sps):
        """测试识别 SPS"""
        parser = H264Parser()

        # 添加一个假的下一起始码
        data = sample_h264_sps + b'\x00\x00\x00\x01'
        nalus = parser.feed(data)

        assert len(nalus) == 1
        assert parser.is_sps(nalus[0])
        assert parser.get_nalu_type(nalus[0]) == NALU_TYPE_SPS

    def test_identify_pps(self, sample_h264_pps):
        """测试识别 PPS"""
        parser = H264Parser()

        data = sample_h264_pps + b'\x00\x00\x00\x01'
        nalus = parser.feed(data)

        assert len(nalus) == 1
        assert parser.is_pps(nalus[0])
        assert parser.get_nalu_type(nalus[0]) == NALU_TYPE_PPS

    def test_identify_idr(self, sample_h264_idr):
        """测试识别 IDR 关键帧"""
        parser = H264Parser()

        data = sample_h264_idr + b'\x00\x00\x00\x01'
        nalus = parser.feed(data)

        assert len(nalus) == 1
        assert parser.is_key_frame(nalus[0])
        assert parser.get_nalu_type(nalus[0]) == NALU_TYPE_IDR

    def test_identify_non_idr(self):
        """测试识别非 IDR 帧"""
        parser = H264Parser()

        # 非 IDR 帧（NALU 类型 1）
        data = b'\x00\x00\x00\x01\x41\x9a\x24\x6c\xb0'
        data += b'\x00\x00\x00\x01'

        nalus = parser.feed(data)

        assert len(nalus) == 1
        assert not parser.is_key_frame(nalus[0])
        assert parser.get_nalu_type(nalus[0]) == NALU_TYPE_SLICE


# ---------------------------------------------------------------------------
# 跨块解析测试
# ---------------------------------------------------------------------------

class TestCrossChunkParsing:
    """跨块解析测试"""

    def test_nalu_split_across_chunks(self):
        """测试 NALU 跨越多个数据块"""
        parser = H264Parser()

        # 完整的 SPS
        sps = b'\x00\x00\x00\x01\x67\x42\x00\x1e\xab\x40\xa0\xfd\x00\xf0'
        pps = b'\x00\x00\x00\x01\x68\xce\x38\x80'

        # 将 SPS 分成两部分
        chunk1 = sps[:10]
        chunk2 = sps[10:] + pps

        # 第一块不应返回任何 NALU（不完整）
        nalus1 = parser.feed(chunk1)
        assert len(nalus1) == 0

        # 第二块应该能解析出 SPS
        nalus2 = parser.feed(chunk2)
        assert len(nalus2) == 1
        assert parser.is_sps(nalus2[0])

    def test_start_code_split_across_chunks(self):
        """测试起始码跨越数据块边界"""
        parser = H264Parser()

        # 第一个 NALU 完整
        sps = b'\x00\x00\x00\x01\x67\x42\x00\x1e'

        # 将第二个 NALU 的起始码分成两部分
        chunk1 = sps + b'\x00\x00'  # SPS + 起始码的前两字节
        chunk2 = b'\x00\x01\x68\xce\x38\x80'  # 起始码的后两字节 + PPS

        nalus1 = parser.feed(chunk1)
        assert len(nalus1) == 0  # 起始码不完整

        nalus2 = parser.feed(chunk2)
        assert len(nalus2) == 1  # 现在可以解析出 SPS
        assert parser.is_sps(nalus2[0])


# ---------------------------------------------------------------------------
# 缓冲区和重置测试
# ---------------------------------------------------------------------------

class TestBufferAndReset:
    """缓冲区和重置功能测试"""

    def test_buffer_accumulation(self, sample_h264_sps):
        """测试缓冲区累积"""
        parser = H264Parser()

        # 喂入不完整的数据
        partial = sample_h264_sps[:10]
        nalus = parser.feed(partial)

        # 应该没有返回 NALU，但数据应该在缓冲区中
        assert len(nalus) == 0
        assert len(parser._buffer) > 0

    def test_reset_clears_buffer(self, sample_h264_sps):
        """测试重置清空缓冲区"""
        parser = H264Parser()

        # 喂入一些数据
        parser.feed(sample_h264_sps[:10])
        assert len(parser._buffer) > 0

        # 重置
        parser.reset()

        # 缓冲区应该被清空
        assert len(parser._buffer) == 0

    def test_reset_allows_fresh_parsing(self, sample_h264_sps, sample_h264_pps):
        """测试重置后可以重新解析"""
        parser = H264Parser()

        # 第一次解析
        data1 = sample_h264_sps + sample_h264_pps
        nalus1 = parser.feed(data1)
        assert len(nalus1) == 1

        # 重置
        parser.reset()

        # 第二次解析（使用相同数据）
        nalus2 = parser.feed(data1)
        assert len(nalus2) == 1

        # 两次解析结果应该相同
        assert nalus1[0] == nalus2[0]


# ---------------------------------------------------------------------------
# 边界情况测试
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """边界情况测试"""

    def test_empty_data(self):
        """测试空数据"""
        parser = H264Parser()
        nalus = parser.feed(b'')
        assert len(nalus) == 0

    def test_no_start_code(self):
        """测试没有起始码的数据"""
        parser = H264Parser()
        nalus = parser.feed(b'\x67\x42\x00\x1e\xab\x40')
        assert len(nalus) == 0

    def test_only_start_code(self):
        """测试只有起始码没有数据"""
        parser = H264Parser()
        nalus = parser.feed(b'\x00\x00\x00\x01')
        assert len(nalus) == 0

    def test_invalid_nalu_type(self):
        """测试无效 NALU 类型"""
        parser = H264Parser()

        # 创建一个 NALU，但头部数据为空
        nalu = b'\x00\x00\x00\x01'
        nalu_type = parser.get_nalu_type(nalu)

        # 应该返回 -1 或抛出异常
        assert nalu_type == -1 or nalu_type is None

    def test_very_large_nalu(self):
        """测试非常大的 NALU"""
        parser = H264Parser()

        # 创建一个 1MB 的 NALU
        large_data = b'\x00\x00\x00\x01\x67' + b'\x00' * 1000000
        large_data += b'\x00\x00\x00\x01'

        nalus = parser.feed(large_data)

        assert len(nalus) == 1
        assert len(nalus[0]) > 1000000
