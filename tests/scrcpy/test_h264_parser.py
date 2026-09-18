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
    - 起始码回看修正（4 字节码吸收，方案 17 实施项 1a）
    - 与现状算法的随机流对拍（语义回归守护）
"""

import random

import pytest

from app.scrcpy.h264_parser import H264Parser
from app.scrcpy.constants import (
    NALU_TYPE_IDR,
    NALU_TYPE_SPS,
    NALU_TYPE_PPS,
    NALU_TYPE_SLICE,
)


class ReferenceParser:
    """现状逐字节算法的独立备份（2026-09-18 重写前基线）。

    仅用于对拍测试：随机流下断言重写后的 H264Parser 与本类产出
    逐字节一致。重写后本类不再变化，作为语义回归的守护存在。
    """

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, data: bytes) -> list[bytes]:
        self._buffer.extend(data)
        nalus = []
        while True:
            start = self._find_start_code()
            if start == -1:
                break
            next_start = self._find_start_code(start + 3)
            if next_start == -1:
                break
            nalus.append(bytes(self._buffer[start:next_start]))
            self._buffer = self._buffer[next_start:]
        return nalus

    def _find_start_code(self, start: int = 0) -> int:
        buf = self._buffer
        for i in range(start, len(buf) - 2):
            if buf[i] == 0 and buf[i + 1] == 0 and buf[i + 2] == 1:
                return i
            if (i < len(buf) - 3 and buf[i] == 0 and buf[i + 1] == 0
                    and buf[i + 2] == 0 and buf[i + 3] == 1):
                return i
        return -1


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


# ---------------------------------------------------------------------------
# 起始码回看修正测试（方案 17 实施项 1a）
# ---------------------------------------------------------------------------

class TestLookbackCorrection:
    """起始码回看修正测试：4 字节/3 字节码混用时，产出边界字节零漂移"""

    def _feed_all(self, parser: H264Parser, data: bytes) -> list[bytes]:
        """末尾补假起始码触发最后一个 NALU 输出，单块喂入。

        假 NALU 无后继起始码不会被输出，因此无需剔除。
        """
        nalus = parser.feed(data)
        nalus.extend(parser.feed(b'\x00\x00\x00\x01NEXT'))
        return nalus

    def test_consecutive_4_byte_codes(self):
        """两个 4 字节起始码：前一 NALU 尾不残留多余零字节"""
        data = (b'\x00\x00\x00\x01\x67\x01'      # SPS
                + b'\x00\x00\x00\x01\x68\x02')   # PPS
        parser = H264Parser()
        nalus = self._feed_all(parser, data)

        assert len(nalus) == 2
        assert nalus[0] == b'\x00\x00\x00\x01\x67\x01'
        assert nalus[1] == b'\x00\x00\x00\x01\x68\x02'

    def test_3_byte_after_4_byte(self):
        """4 字节码后接 3 字节码：第二 NALU 仍是完整 3 字节起始码"""
        data = (b'\x00\x00\x00\x01\x67\x01'   # 4 字节码 SPS
                + b'\x00\x00\x01\x68\x02')    # 3 字节码 PPS
        parser = H264Parser()
        nalus = self._feed_all(parser, data)

        assert len(nalus) == 2
        assert nalus[0] == b'\x00\x00\x00\x01\x67\x01'
        assert nalus[1] == b'\x00\x00\x01\x68\x02'

    def test_4_byte_after_3_byte(self):
        """3 字节码后接 4 字节码：第二 NALU 起始码完整不残缺"""
        data = (b'\x00\x00\x01\x67\x01'       # 3 字节码 SPS
                + b'\x00\x00\x00\x01\x68\x02')  # 4 字节码 PPS
        parser = H264Parser()
        nalus = self._feed_all(parser, data)

        assert len(nalus) == 2
        assert nalus[0] == b'\x00\x00\x01\x67\x01'
        assert nalus[1] == b'\x00\x00\x00\x01\x68\x02'

    def test_data_body_trailing_zero_before_4_byte_code(self):
        """NALU 数据尾部以 0x00 结尾时，起始码的 4 字节归属判定正确"""
        # 前一 NALU 末尾字节 0x00，后接 4 字节起始码（共 5 个连续 0）
        data = (b'\x00\x00\x01\x67\x00'       # 数据尾 0x00
                + b'\x00\x00\x00\x01\x68\x02')
        parser = H264Parser()
        nalus = self._feed_all(parser, data)

        assert len(nalus) == 2
        # ref 算法：数据尾 1 个 0 保留在 NALU1 内，
        # 4 字节码从其后开始，起始码完整
        assert nalus[0] == b'\x00\x00\x01\x67\x00'
        assert nalus[1] == b'\x00\x00\x00\x01\x68\x02'

    def test_data_body_trailing_two_zeros_before_4_byte_code(self):
        """NALU 数据尾部 2 个 0x00 + 4 字节起始码（6 个连续 0）"""
        data = (b'\x00\x00\x01\x67\x00\x00'   # 数据尾 2 个 0
                + b'\x00\x00\x00\x01\x68\x02')
        parser = H264Parser()
        nalus = self._feed_all(parser, data)

        assert len(nalus) == 2
        # ref 算法：数据尾 2 个 0 保留在 NALU1 内，
        # 4 字节码从其后开始，起始码完整
        assert nalus[0] == b'\x00\x00\x01\x67\x00\x00'
        assert nalus[1] == b'\x00\x00\x00\x01\x68\x02'


# ---------------------------------------------------------------------------
# 与现状算法的随机流对拍（语义回归守护）
# ---------------------------------------------------------------------------

class TestDifferentialParity:
    """随机流下 H264Parser 与现状逐字节算法 ReferenceParser 逐字节一致"""

    LEGAL_TYPES = [0x67, 0x68, 0x65, 0x41, 0x06, 0x09]  # SPS/PPS/IDR/SLICE/SEI/AUD

    @staticmethod
    def _make_random_stream(rng: random.Random) -> bytes:
        """构造随机 NALU 流：随机 3/4 字节起始码、随机长度、合法 NALU 类型"""
        parts = []
        for _ in range(rng.randint(3, 12)):
            start = (b'\x00\x00\x01' if rng.random() < 0.5
                     else b'\x00\x00\x00\x01')
            nalu_type = rng.choice(TestDifferentialParity.LEGAL_TYPES)
            body = bytes(rng.randrange(256) for _ in range(rng.randint(0, 60)))
            parts.append(start + bytes([nalu_type]) + body)
        return b''.join(parts)

    @staticmethod
    def _feed_chunks(parser: H264Parser, data: bytes, rng: random.Random) -> list[bytes]:
        """随机大小分块喂入，收集全部产出"""
        nalus: list[bytes] = []
        pos = 0
        while pos < len(data):
            size = min(rng.randint(1, 37), len(data) - pos)
            nalus.extend(parser.feed(data[pos:pos + size]))
            pos += size
        return nalus

    def test_whole_feed_parity(self):
        """整块喂入：200 组随机流对拍"""
        rng = random.Random(20260918)
        for _ in range(200):
            data = self._make_random_stream(rng)
            new_parser = H264Parser()
            ref = ReferenceParser()
            new_out = new_parser.feed(data)
            ref_out = ref.feed(data)
            assert new_out == ref_out

    def test_chunked_feed_parity(self):
        """随机分块喂入：200 组随机流对拍（含起始码跨块切分）"""
        rng = random.Random(20260919)
        for _ in range(200):
            data = self._make_random_stream(rng)
            new_parser = H264Parser()
            ref = ReferenceParser()
            new_out = self._feed_chunks(new_parser, data, rng)
            ref_out = self._feed_chunks(ref, data, rng)
            assert new_out == ref_out


# ---------------------------------------------------------------------------
# 大帧跨块测试
# ---------------------------------------------------------------------------

class TestLargeFrameAcrossChunks:
    """大 NALU 跨多块解析（补充既有 1MB 单块测试的跨块场景）"""

    def test_64kb_nalu_across_many_chunks(self):
        """64KB NALU 按 1KB 分块喂入"""
        parser = H264Parser()
        sps = b'\x00\x00\x00\x01\x67' + b'\xab' * (64 * 1024)
        pps = b'\x00\x00\x00\x01\x68\xce'

        nalus: list[bytes] = []
        data = sps + pps
        for i in range(0, len(data), 1024):
            nalus.extend(parser.feed(data[i:i + 1024]))

        assert len(nalus) == 1
        assert nalus[0] == sps

    def test_large_nalu_parity_with_reference(self):
        """128KB 大 NALU + 后续小 NALU，分块喂入对拍"""
        rng = random.Random(20260920)
        nalu1 = b'\x00\x00\x00\x01\x67' + bytes(rng.randrange(256) for _ in range(128 * 1024))
        nalu2 = b'\x00\x00\x01\x68\xce\x38'
        data = nalu1 + nalu2

        new_parser = H264Parser()
        ref = ReferenceParser()
        new_out = self._feed_fixed_chunks(new_parser, data, 4096)
        ref_out = self._feed_fixed_chunks(ref, data, 4096)
        assert new_out == ref_out

    @staticmethod
    def _feed_fixed_chunks(parser: H264Parser, data: bytes, size: int) -> list[bytes]:
        nalus: list[bytes] = []
        for i in range(0, len(data), size):
            nalus.extend(parser.feed(data[i:i + size]))
        return nalus
