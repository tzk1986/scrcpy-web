"""
WebSocket 传输层实现
======================

层：基础设施 → 传输。

将 FastAPI 的 WebSocket 对象封装为统一的 Transport 接口（domain/ports.py）。

为什么需要封装？
    - 统一接口：WebSocket 和 WebTransport 都实现 Transport 协议
    - 简化测试：可以 mock Transport 而不需要 mock WebSocket
    - 解耦：视频流服务不需要知道底层是 WebSocket 还是 WebTransport

使用方法：
    在 interfaces/ws/video.py 中创建 WebSocketTransport 实例，
    然后将其传给视频流处理逻辑。
"""

from fastapi import WebSocket

from app.core.logging import get_logger

logger = get_logger(__name__)


class WebSocketTransport:
    """
    WebSocket 传输层封装。

    实现 domain.ports.Transport 协议。
    """

    def __init__(self, ws: WebSocket):
        """
        参数：
            ws: FastAPI WebSocket 实例（已 accept）。
        """
        self.ws = ws

    async def send(self, frame: bytes):
        """
        发送二进制帧。

        参数：
            frame: 要发送的原始字节（H.264 数据等）。
        """
        await self.ws.send_bytes(frame)

    async def receive(self) -> bytes:
        """
        接收二进制帧。

        返回：
            接收到的原始字节。
        """
        return await self.ws.receive_bytes()

    async def close(self):
        """关闭 WebSocket 连接。"""
        await self.ws.close()
