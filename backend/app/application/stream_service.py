"""
视频流服务
===========

层：应用层。

编排从 Android 设备到客户端的视频流。

流式传输管道：
    设备屏幕 → scrcpy-server（H.264 编码器）→ 原始帧 →
    → WebSocket/WebTransport → 浏览器（WebCodecs 解码器）

本服务：
    1. 从设置中读取编码器配置（max_size、bit_rate 等）
    2. 使用配置选项启动视频编码器（ScrcpyEncoder）
    3. 将 H.264 帧 yield 给 WebSocket 处理器
    4. 支持优雅停止（编码器循环检查 active_streams 标志）

依赖（通过 Protocol 注入）：
    - VideoEncoder: 具体实现是 ScrcpyEncoder（scrcpy-server）

注意：当前每个设备只支持一个流。为同一设备启动第二个流
会在 scrcpy-server 层面产生冲突。
"""

from typing import AsyncIterator

from app.core.config import settings
from app.core.logging import get_logger
from app.domain.ports import EncoderOpts, VideoEncoder

logger = get_logger(__name__)


class StreamService:
    """视频流用例。"""

    def __init__(self, encoder: VideoEncoder):
        """
        参数：
            encoder: 视频编码器实现（ScrcpyEncoder）。
        """
        self.encoder = encoder
        # 按 device_id 跟踪活跃流。流式传输时值为 True。
        # stop_stream() 将其设为 False 以通知编码器循环退出。
        self.active_streams: dict[str, bool] = {}

    async def start_stream(self, device_id: str) -> AsyncIterator[bytes]:
        """
        为给定设备启动视频流。

        从配置读取编码器选项，启动编码器，并产出 H.264 帧。
        循环在每次迭代时检查 active_streams[device_id]——
        如果为 False（由 stop_stream 设置），循环优雅退出。

        参数：
            device_id: 要流式传输的设备的 ADB 序列号。

        产出：
            H.264 帧数据（原始字节）。
        """
        logger.info("starting_video_stream", device=device_id)
        s = settings()
        opts = EncoderOpts(
            max_size=s.stream.max_size,
            bit_rate=s.stream.bit_rate,
            codec=s.stream.codec,
            fps=s.stream.fps,
        )
        self.active_streams[device_id] = True
        try:
            async for frame in self.encoder.start(device_id, opts):
                if not self.active_streams.get(device_id):
                    break
                yield frame
        finally:
            self.active_streams.pop(device_id, None)
            await self.encoder.stop()

    async def stop_stream(self, device_id: str):
        """
        通知编码器停止给定设备的流式传输。

        设置 start_stream 循环检查的标志，使其在下一次迭代时退出。
        实际清理发生在 start_stream 的 finally 块中。

        参数：
            device_id: 要停止的设备的 ADB 序列号。
        """
        logger.info("stopping_video_stream", device=device_id)
        self.active_streams[device_id] = False
