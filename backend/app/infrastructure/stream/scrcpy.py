"""
scrcpy-server 视频编码器
==========================

层：基础设施 → 视频流。

通过启动 scrcpy-server 子进程实现 VideoEncoder 协议（domain/ports.py）。
scrcpy-server 是 scrcpy 项目的核心组件，负责将 Android 屏幕编码为 H.264 视频流。

工作流程：
    1. 启动 scrcpy-server 子进程，指定设备序列号、分辨率、码率等参数
    2. 从子进程的 stdout 管道读取 H.264 帧数据（64KB 块）
    3. 将帧数据 yield 给上层（StreamService → WebSocket）
    4. 收到停止信号时，kill 子进程并清理资源

关键参数（来自 EncoderOpts）：
    - max_size: 最大帧尺寸（宽或高，取较大值）
    - bit_rate: 目标码率（如 "4M" = 4 Mbps）
    - codec: 视频编码格式（目前仅支持 "h264"）
    - fps: 目标帧率

注意事项：
    - scrcpy-server 需要在 PATH 中可用，或者通过配置文件指定路径
    - 每个设备同时只能有一个 scrcpy-server 实例
    - 帧数据是原始 H.264 NAL 单元，前端需要用 WebCodecs 解码
"""

import asyncio
from typing import AsyncIterator

from app.core.logging import get_logger
from app.domain.ports import EncoderOpts

logger = get_logger(__name__)


class ScrcpyEncoder:
    """
    基于 scrcpy-server 的视频编码器。

    实现 domain.ports.VideoEncoder 协议。
    """

    def __init__(self):
        """初始化编码器状态（尚未启动子进程）。"""
        self.process: asyncio.subprocess.Process | None = None
        self._running = False

    async def start(self, device_id: str, opts: EncoderOpts) -> AsyncIterator[bytes]:
        """
        启动 scrcpy-server 并 yield H.264 帧数据。

        参数：
            device_id: 目标设备的 ADB 序列号。
            opts: 编码器配置（分辨率、码率、编码格式、帧率）。

        返回：
            异步生成器，每次 yield 一个 H.264 帧（原始字节）。

        注意：
            - 使用 --no-playback 参数，表示不在服务端播放，只输出原始流
            - 读取块大小为 65536 字节（64KB）
            - 当 self._running 为 False 时，循环退出
        """
        logger.info(
            "starting_scrcpy_encoder",
            device=device_id,
            max_size=opts.max_size,
            bit_rate=opts.bit_rate,
        )

        self.process = await asyncio.create_subprocess_exec(
            "scrcpy-server",
            "--serial",
            device_id,
            "--max-size",
            str(opts.max_size),
            "--bit-rate",
            opts.bit_rate,
            "--video-codec",
            opts.codec,
            "--no-playback",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._running = True

        try:
            while self._running:
                if not self.process.stdout:
                    break
                frame = await self.process.stdout.read(65536)  # 64KB 块读取
                if not frame:
                    break
                yield frame
        finally:
            await self.stop()

    async def stop(self):
        """
        停止编码并释放资源。

        将 _running 标志设为 False，然后 kill 子进程并等待其退出。
        """
        self._running = False
        if self.process:
            self.process.kill()
            await self.process.wait()
            self.process = None
