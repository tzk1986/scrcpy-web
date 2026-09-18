"""WebSocket 底层 socket 选项工具（best-effort）。

仅供接口层使用：为 WS 连接设置 TCP_NODELAY，减少浏览器在局域网远端时
小包的 Nagle 延迟（方案 17 实施项 4 顺手项）。

可达路径依赖 uvicorn 的调用约定：uvicorn 以协议实例的绑定方法调用 ASGI
应用（websockets_sansio_impl: ``await self.app(scope, self.receive, self.send)``），
Starlette 将其原样存为 ``WebSocket._receive``，故 ``_receive.__self__``
即协议实例，其上 ``.transport`` 为 asyncio transport。任何中间件若包裹了
receive/send，此路径失效，函数静默返回 False（不影响连接本身）。
"""

import socket as _socket
from typing import Any

from fastapi import WebSocket

from app.core.logging import get_logger

logger = get_logger(__name__)


def _get_transport(websocket: WebSocket) -> Any | None:
    """从 Starlette WebSocket 取 uvicorn 协议实例的 asyncio transport。"""
    receive = getattr(websocket, "_receive", None)
    protocol = getattr(receive, "__self__", None)
    return getattr(protocol, "transport", None)


def enable_tcp_nodelay(websocket: WebSocket) -> bool:
    """为 WebSocket 的 TCP 连接启用 TCP_NODELAY（best-effort）。

    返回 True 表示设置成功。任何失败（取不到 socket、非 TCP 传输等）仅
    记录 debug 日志并返回 False，绝不影响连接本身。

    Windows Proactor 下 ``get_extra_info("socket")`` 返回 None，回退
    ``"pipe"``（Proactor 用 socket 模拟管道，同样支持 setsockopt）。
    """
    try:
        transport = _get_transport(websocket)
        if transport is None:
            return False
        sock = transport.get_extra_info("socket")
        if sock is None:
            sock = transport.get_extra_info("pipe")
        if sock is None or not hasattr(sock, "setsockopt"):
            return False
        sock.setsockopt(_socket.IPPROTO_TCP, _socket.TCP_NODELAY, 1)
        return True
    except Exception as e:
        logger.debug("tcp_nodelay_set_failed", error=str(e))
        return False