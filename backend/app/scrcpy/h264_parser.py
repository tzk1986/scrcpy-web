"""
H264 帧解析器
==============

解析 scrcpy-server 输出的原始 H264 流，提取完整的 NALU（网络抽象层单元）。

H264 流格式：
    scrcpy-server 输出的是连续的字节流，需要解析为独立的 NALU。
    每个 NALU 以起始码开头：
        - 3 字节起始码：0x00 0x00 0x01
        - 4 字节起始码：0x00 0x00 0x00 0x01

NALU 类型（NALU 头部第 1 字节的低 5 位）：
    5 (0x65): IDR 关键帧（I 帧）
    7 (0x67): SPS（序列参数集）
    8 (0x68): PPS（图像参数集）
    1 (0x41): 非 IDR 帧（P/B 帧）

使用示例：
    ```python
    parser = H264Parser()

    # 从编码器读取数据块
    async for chunk in encoder.start(device_id, opts):
        # 喂入解析器
        nalus = parser.feed(chunk)

        # 处理完整的 NALU
        for nalu in nalus:
            nalu_type = parser.get_nalu_type(nalu)
            if nalu_type == NALU_TYPE_IDR:
                print("收到关键帧")
    ```

实现细节：
    - 使用内部缓冲区累积数据
    - 查找起始码定位 NALU 边界
    - 返回完整的 NALU（包含起始码）
    - 处理跨块的 NALU（一个 NALU 可能跨越多个数据块）
"""

from app.core.logging import get_logger
from .constants import (
    NALU_TYPE_IDR,
    NALU_TYPE_SPS,
    NALU_TYPE_PPS,
    NALU_TYPE_SLICE,
)

logger = get_logger(__name__)


class H264Parser:
    """
    H264 NALU 解析器。

    将连续的字节流解析为独立的 NALU 单元。
    """

    def __init__(self):
        """初始化解析器，创建空缓冲区。"""
        self._buffer = bytearray()

    def feed(self, data: bytes) -> list[bytes]:
        """
        喂入数据并返回完整的 NALU。

        参数：
            data: 从编码器读取的原始字节数据。

        返回：
            完整的 NALU 列表（每个 NALU 包含起始码）。

        实现逻辑：
            1. 将新数据追加到缓冲区
            2. 查找 NALU 起始码（0x00 0x00 0x01 或 0x00 0x00 0x00 0x01）
            3. 如果找到两个起始码，提取第一个 NALU
            4. 从缓冲区移除已提取的 NALU
            5. 重复直到没有完整的 NALU
        """
        self._buffer.extend(data)
        nalus = []

        while True:
            # 查找第一个起始码
            start = self._find_start_code()
            if start == -1:
                # 没有找到起始码，等待更多数据
                break

            # 查找第二个起始码（当前 NALU 的结束位置）
            # 从第一个起始码后 3-4 字节开始搜索
            next_start = self._find_start_code(start + 3)
            if next_start == -1:
                # 没有第二个起始码，说明当前 NALU 不完整
                # 等待更多数据
                break

            # 提取完整的 NALU（从 start 到 next_start）
            nalu = bytes(self._buffer[start:next_start])
            nalus.append(nalu)

            # 从缓冲区移除已处理的 NALU
            self._buffer = self._buffer[next_start:]

        return nalus

    def _find_start_code(self, start: int = 0) -> int:
        """
        查找 NALU 起始码的位置。

        起始码格式：
            - 3 字节：0x00 0x00 0x01
            - 4 字节：0x00 0x00 0x00 0x01

        参数：
            start: 开始搜索的位置（默认为 0）。

        返回：
            起始码的位置，如果未找到返回 -1。

        实现：
            遍历缓冲区，检查连续的字节是否匹配起始码模式。
        """
        for i in range(start, len(self._buffer) - 2):
            # 检查 3 字节起始码：0x00 0x00 0x01
            if (self._buffer[i] == 0 and
                self._buffer[i+1] == 0 and
                self._buffer[i+2] == 1):
                return i

            # 检查 4 字节起始码：0x00 0x00 0x00 0x01
            if (i < len(self._buffer) - 3 and
                self._buffer[i] == 0 and
                self._buffer[i+1] == 0 and
                self._buffer[i+2] == 0 and
                self._buffer[i+3] == 1):
                return i

        return -1

    def get_nalu_type(self, nalu: bytes) -> int:
        """
        获取 NALU 的类型。

        NALU 头部格式：
            第 1 字节：forbidden_zero_bit(1) + nal_ref_idc(2) + nal_unit_type(5)
            低 5 位是 NALU 类型。

        参数：
            nalu: NALU 数据（包含起始码）。

        返回：
            NALU 类型（0-31）。

        常见类型：
            1: 非 IDR 帧（P/B 帧）
            5: IDR 关键帧
            7: SPS（序列参数集）
            8: PPS（图像参数集）

        实现：
            跳过起始码（3-4 字节），取 NALU 头部的第 1 字节，
            与 0x1F（二进制 00011111）进行按位与操作，提取低 5 位。
        """
        # 跳过起始码，找到 NALU 头部
        # 起始码可能是 3 字节或 4 字节
        header_pos = 0
        if nalu[0:3] == b'\x00\x00\x01':
            header_pos = 3
        elif nalu[0:4] == b'\x00\x00\x00\x01':
            header_pos = 4
        else:
            # 没有找到起始码，假设整个数据是 NALU
            header_pos = 0

        if header_pos >= len(nalu):
            return -1

        # 提取 NALU 类型（低 5 位）
        return nalu[header_pos] & 0x1F

    def is_key_frame(self, nalu: bytes) -> bool:
        """
        判断 NALU 是否为关键帧（IDR）。

        参数：
            nalu: NALU 数据。

        返回：
            如果是 IDR 关键帧返回 True，否则返回 False。
        """
        return self.get_nalu_type(nalu) == NALU_TYPE_IDR

    def is_sps(self, nalu: bytes) -> bool:
        """
        判断 NALU 是否为 SPS（序列参数集）。

        参数：
            nalu: NALU 数据。

        返回：
            如果是 SPS 返回 True，否则返回 False。
        """
        return self.get_nalu_type(nalu) == NALU_TYPE_SPS

    def is_pps(self, nalu: bytes) -> bool:
        """
        判断 NALU 是否为 PPS（图像参数集）。

        参数：
            nalu: NALU 数据。

        返回：
            如果是 PPS 返回 True，否则返回 False。
        """
        return self.get_nalu_type(nalu) == NALU_TYPE_PPS

    def reset(self):
        """
        重置解析器状态。

        清空内部缓冲区，丢弃所有未处理的数据。
        """
        self._buffer = bytearray()
        logger.debug("h264_parser_reset")
