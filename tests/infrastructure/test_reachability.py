"""
TCP 可达性预检测试
==================

测试 probe_tcp 的三种结果路径（全部基于 127.0.0.1 回环或 mock，不依赖真机）：

    - 可达：真实临时监听端口 → 正常返回
    - 拒绝：已关闭端口 → DeviceUnreachableError（消息含「拒绝」）
    - 超时：open_connection 挂起 → DeviceUnreachableError（消息含「超时」）
"""

import asyncio
import socket
from unittest.mock import patch

import pytest

from app.core.exceptions import DeviceUnreachableError
from app.infrastructure.adb.reachability import probe_tcp


async def _accept_and_close(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    writer.close()


@pytest.mark.asyncio
async def test_probe_reachable():
    """真实监听端口可达时，probe_tcp 正常返回 None。"""
    server = await asyncio.start_server(_accept_and_close, host="127.0.0.1", port=0)
    assert server.sockets is not None
    port = server.sockets[0].getsockname()[1]
    try:
        assert await probe_tcp("127.0.0.1", port, timeout=1.5) is None
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_probe_refused():
    """探测已关闭端口 → 抛 DeviceUnreachableError，消息含「拒绝」。"""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    # 超时放宽到 5s：本机 Windows 防火墙对回环关闭端口的 RST 有约 2s 延迟，
    # 若用默认 1.5s 会先触发超时分支，测不到「拒绝」路径（CI 上拒绝是即时的）
    with pytest.raises(DeviceUnreachableError, match="拒绝"):
        await probe_tcp("127.0.0.1", port, timeout=5.0)


@pytest.mark.asyncio
async def test_probe_timeout():
    """建连挂起超过 timeout → 抛 DeviceUnreachableError，消息含「超时」。"""

    async def hanging_open_connection(*args, **kwargs):
        await asyncio.sleep(10)

    with patch("asyncio.open_connection", new=hanging_open_connection):
        with pytest.raises(DeviceUnreachableError, match="超时"):
            await probe_tcp("127.0.0.1", 5555, timeout=0.05)