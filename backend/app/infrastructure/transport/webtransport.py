"""
WebTransport 传输层实现（占位符）
===================================

层：基础设施 → 传输。

WebTransport 是 Chrome 专属的低延迟传输协议，基于 HTTP/3 (QUIC)。
相比 WebSocket，WebTransport 具有以下优势：
    - 更低的延迟（无 TCP 队头阻塞）
    - 支持单向流、双向流、数据报
    - 原生支持多路复用

当前状态：占位符
    需要 aioquic 库和 HTTP/3 服务器配置才能完整实现。
    目前所有方法都抛出 NotImplementedError。

未来实现计划：
    1. 集成 aioquic 作为 QUIC 传输层
    2. 配置 HTTP/3 端点（需要 HTTPS 证书）
    3. 实现数据报和流的发送/接收
    4. 在前端检测 WebTransport 支持情况，自动降级到 WebSocket

浏览器兼容性：
    - Chrome 97+：完整支持
    - Firefox：实验性支持
    - Safari：不支持
"""

from app.core.logging import get_logger

logger = get_logger(__name__)


class WebTransportTransport:
    """
    WebTransport 传输层封装（尚未完整实现）。

    实现 domain.ports.Transport 协议。
    """

    def __init__(self):
        """初始化时记录警告日志。"""
        logger.warning("WebTransport not yet fully implemented")

    async def send(self, frame: bytes):
        """
        发送二进制帧。

        当前状态：未实现。
        """
        raise NotImplementedError("WebTransport not yet implemented")

    async def receive(self) -> bytes:
        """
        接收二进制帧。

        当前状态：未实现。
        """
        raise NotImplementedError("WebTransport not yet implemented")

    async def close(self):
        """关闭传输连接（当前为空操作）。"""
        pass
