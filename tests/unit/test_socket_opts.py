"""socket_opts.enable_tcp_nodelay 行为测试（方案 17 实施项 4 顺手项）。

覆盖：
    - 正常路径：经 receive.__self__.transport 取到 socket 并设置 TCP_NODELAY；
    - Windows Proactor 回退：'socket' 为 None 时改用 'pipe'；
    - 路径失效（中间件包裹 receive / 无 transport / 非 socket 对象）：
      返回 False 且不抛异常。
"""

import socket

from app.interfaces.ws.socket_opts import enable_tcp_nodelay


class FakeTransport:
    def __init__(self, sock: object = None, pipe: object = None) -> None:
        self._info = {"socket": sock, "pipe": pipe}

    def get_extra_info(self, name: str) -> object:
        return self._info.get(name)


class RaisingTransport:
    def get_extra_info(self, name: str) -> object:
        raise RuntimeError("transport get_extra_info failed")


class FakeProtocol:
    """模拟 uvicorn 协议实例：receive 为绑定方法，__self__ 即本实例。"""

    def __init__(self, transport: object) -> None:
        self.transport = transport

    async def receive(self) -> dict:
        return {"type": "websocket.connect"}


class FakeWebSocket:
    """仅需 _receive 属性的 WebSocket 替代品。"""

    def __init__(self, receive: object) -> None:
        self._receive = receive


def make_ws(transport: object) -> FakeWebSocket:
    return FakeWebSocket(FakeProtocol(transport).receive)


def make_tcp_socket() -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # 先显式置 0，确保测试验证的是函数本身的效果（Nagle 默认可能是 on/off）
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 0)
    return sock


class TestEnableTcpNodelay:
    def test_sets_nodelay_via_socket_extra(self) -> None:
        sock = make_tcp_socket()
        try:
            assert enable_tcp_nodelay(make_ws(FakeTransport(sock=sock))) is True
            assert sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY) == 1
        finally:
            sock.close()

    def test_falls_back_to_pipe_when_socket_none(self) -> None:
        sock = make_tcp_socket()
        try:
            ws = make_ws(FakeTransport(sock=None, pipe=sock))
            assert enable_tcp_nodelay(ws) is True
            assert sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY) == 1
        finally:
            sock.close()

    def test_returns_false_without_transport(self) -> None:
        # receive 为普通函数（无 __self__），模拟中间件包裹场景
        async def plain_receive() -> dict:
            return {}

        assert enable_tcp_nodelay(FakeWebSocket(plain_receive)) is False

    def test_returns_false_when_extra_is_not_socket(self) -> None:
        ws = make_ws(FakeTransport(sock=object()))
        assert enable_tcp_nodelay(ws) is False

    def test_returns_false_on_transport_error(self) -> None:
        ws = make_ws(RaisingTransport())
        assert enable_tcp_nodelay(ws) is False