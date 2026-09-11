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
    2. 为每个设备创建独立的编码器实例
    3. 将 H.264 帧 yield 给 WebSocket 处理器
    4. 支持优雅停止（编码器循环检查 active_streams 标志）
    5. 支持多设备并发流式传输

依赖（通过 Protocol 注入）：
    - VideoEncoder: 具体实现是 ScrcpyEncoder（scrcpy-server）

多设备支持：
    每个设备有独立的编码器实例和流状态。
    可以同时为多个设备启动视频流。
"""

from typing import AsyncIterator

from app.core.config import settings
from app.core.logging import get_logger
from app.domain.ports import EncoderOpts, VideoEncoder
from app.infrastructure.stream.scrcpy import ScrcpyEncoder

logger = get_logger(__name__)


class StreamService:
    """视频流用例。"""

    def __init__(self):
        """
        初始化视频流服务。

        为每个设备维护独立的编码器实例。
        """
        # 按 device_id 跟踪活跃流
        self.active_streams: dict[str, bool] = {}
        # 按 device_id 跟踪编码器实例
        self.encoders: dict[str, ScrcpyEncoder] = {}

    async def start_stream(self, device_id: str) -> AsyncIterator[bytes]:
        """
        为给定设备启动视频流。

        从配置读取编码器选项，创建编码器实例，启动编码，并产出 H.264 帧。
        循环在每次迭代时检查 active_streams[device_id]——
        如果为 False（由 stop_stream 设置），循环优雅退出。

        参数：
            device_id: 要流式传输的设备的 ADB 序列号。

        产出：
            H.264 帧数据（原始字节）。
        """
        logger.info("starting_video_stream", device=device_id)

        # 检查是否已经有流在运行
        if device_id in self.active_streams:
            logger.warning("stream_already_active", device=device_id)
            return

        # 创建新的编码器实例
        encoder = ScrcpyEncoder()
        self.encoders[device_id] = encoder

        s = settings()
        opts = EncoderOpts(
            max_size=s.stream.max_size,
            bit_rate=s.stream.bit_rate,
            codec=s.stream.codec,
            fps=s.stream.fps,
        )
        self.active_streams[device_id] = True

        try:
            async for frame in encoder.start(device_id, opts):
                if not self.active_streams.get(device_id):
                    break
                yield frame
        finally:
            self.active_streams.pop(device_id, None)
            await encoder.stop()
            self.encoders.pop(device_id, None)
            logger.info("video_stream_stopped", device=device_id)

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

    async def stop_all_streams(self):
        """
        停止所有活跃的视频流。

        用于应用关闭时的清理。
        """
        logger.info("stopping_all_video_streams", count=len(self.active_streams))
        for device_id in list(self.active_streams.keys()):
            await self.stop_stream(device_id)

    def get_active_streams(self) -> list[str]:
        """
        获取当前活跃的视频流设备列表。

        返回：
            正在流式传输的设备 ID 列表。
        """
        return [device_id for device_id, active in self.active_streams.items() if active]

    def get_encoder(self, device_id: str) -> ScrcpyEncoder | None:
        """
        获取设备对应的编码器实例。

        用于 WebSocket 输入处理：通过编码器实例的 send_input() 方法
        发送二进制控制消息（延迟 <5ms），而非 adb shell input（50-200ms）。

        参数：
            device_id: 设备的 ADB 序列号。

        返回：
            ScrcpyEncoder 实例，如果设备没有活跃流则返回 None。
        """
        return self.encoders.get(device_id)
