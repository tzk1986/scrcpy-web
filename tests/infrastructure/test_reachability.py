"""
TCP 可达性预检测试
==================

测试 probe_tcp 的三种结果路径（全部基于 127.0.0.1 回环或 mock，不依赖真机）：

    - 可达：真实临时监听端口 → 正常返回
    - 拒绝：已关闭端口 → DeviceUnreachableError（消息含「拒绝」）
    - 超时：open_connection 挂起 → DeviceUnreachableError（消息含「超时」）

以及方案 36 网段并发扫描 scan_open_hosts：

    - 并发生效（in-flight 峰值）+ 结果数值序 + 单台失败静默吞掉
    - concurrency 上限 / 惰性回退 settings
"""

import asyncio
import socket
from ipaddress import ip_network
from unittest.mock import patch

import pytest

from app.core.exceptions import DeviceUnreachableError
from app.infrastructure.adb.reachability import probe_tcp, scan_open_hosts


class _FakeWriter:
    """探测成功路径的最小 writer 桩（close + wait_closed）。"""

    def close(self) -> None:
        pass

    async def wait_closed(self) -> None:
        pass


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


class TestScanOpenHosts:
    """scan_open_hosts：并发探测 + 静默分类 + 惰性回退 settings。"""

    @pytest.mark.asyncio
    async def test_finds_open_hosts_concurrently_and_sorted(self):
        """/24 并发探测：结果按数值序返回，in-flight 峰值证明并发生效。"""
        open_hosts = {"192.168.8.2", "192.168.8.10", "192.168.8.200"}
        inflight = 0
        max_inflight = 0

        async def fake_open_connection(host: str, port: int, **kwargs: object):
            nonlocal inflight, max_inflight
            inflight += 1
            max_inflight = max(max_inflight, inflight)
            try:
                await asyncio.sleep(0.02)
                if host not in open_hosts:
                    raise ConnectionRefusedError(10061, "拒绝")
                return asyncio.StreamReader(), _FakeWriter()
            finally:
                inflight -= 1

        with patch("asyncio.open_connection", new=fake_open_connection):
            result = await scan_open_hosts(
                ip_network("192.168.8.0/24"), port=5555, timeout=1.0, concurrency=254
            )

        assert result == ["192.168.8.2", "192.168.8.10", "192.168.8.200"]
        assert max_inflight > 200

    @pytest.mark.asyncio
    async def test_respects_concurrency_limit(self):
        """/28 + concurrency=2：in-flight 峰值恰为 2（既限流、又确实并行）。"""
        inflight = 0
        max_inflight = 0

        async def fake_open_connection(host: str, port: int, **kwargs: object):
            nonlocal inflight, max_inflight
            inflight += 1
            max_inflight = max(max_inflight, inflight)
            try:
                await asyncio.sleep(0.005)
                raise ConnectionRefusedError(10061, "拒绝")
            finally:
                inflight -= 1

        with patch("asyncio.open_connection", new=fake_open_connection):
            result = await scan_open_hosts(
                ip_network("192.168.8.0/28"), port=5555, timeout=1.0, concurrency=2
            )

        assert result == []
        assert max_inflight == 2

    @pytest.mark.asyncio
    async def test_swallows_all_failure_kinds(self):
        """超时 / DNS 失败 / 连接错误逐台静默吞掉，不影响开放主机返回。"""

        async def fake_open_connection(host: str, port: int, **kwargs: object):
            if host == "192.168.8.1":
                return asyncio.StreamReader(), _FakeWriter()
            if host == "192.168.8.2":
                await asyncio.sleep(10)  # 拖过 timeout → wait_for 超时
            if host == "192.168.8.3":
                raise socket.gaierror(-2, "Name or service not known")
            raise OSError(10065, "网络不可达")

        with patch("asyncio.open_connection", new=fake_open_connection):
            result = await scan_open_hosts(
                ip_network("192.168.8.0/29"), port=5555, timeout=0.05, concurrency=8
            )

        assert result == ["192.168.8.1"]

    @pytest.mark.asyncio
    async def test_silent_per_host_and_one_summary_log(self):
        """/24 大量拒绝 + 1 台开放：零 warning，仅一条 scan_completed 汇总。"""

        async def fake_open_connection(host: str, port: int, **kwargs: object):
            if host == "192.168.8.18":
                return asyncio.StreamReader(), _FakeWriter()
            raise ConnectionRefusedError(10061, "拒绝")

        with patch("asyncio.open_connection", new=fake_open_connection), patch(
            "app.infrastructure.adb.reachability.logger"
        ) as mock_logger:
            result = await scan_open_hosts(
                ip_network("192.168.8.0/24"), port=5555, timeout=1.0, concurrency=254
            )

        assert result == ["192.168.8.18"]
        assert mock_logger.warning.call_count == 0
        assert mock_logger.info.call_count == 1
        args, kwargs = mock_logger.info.call_args
        assert args == ("scan_completed",)
        assert kwargs["probed"] == 254
        assert kwargs["found"] == 1
        assert "elapsed_ms" in kwargs

    @pytest.mark.asyncio
    async def test_lazy_defaults_from_settings(self, monkeypatch):
        """timeout/concurrency 省略时惰性读取 settings（probe_timeout / scan_concurrency）。"""
        inflight = 0
        max_inflight = 0
        waits: list[float | None] = []

        class _FakeAdb:
            probe_timeout = 0.5
            scan_concurrency = 3

        class _FakeSettings:
            adb = _FakeAdb()

        async def fake_open_connection(host: str, port: int, **kwargs: object):
            nonlocal inflight, max_inflight
            inflight += 1
            max_inflight = max(max_inflight, inflight)
            try:
                await asyncio.sleep(0.005)
                raise ConnectionRefusedError(10061, "拒绝")
            finally:
                inflight -= 1

        real_wait_for = asyncio.wait_for

        async def spy_wait_for(awaitable, timeout=None):
            waits.append(timeout)
            return await real_wait_for(awaitable, timeout=timeout)

        monkeypatch.setattr(
            "app.infrastructure.adb.reachability.settings", lambda: _FakeSettings()
        )
        with patch("asyncio.open_connection", new=fake_open_connection), patch(
            "asyncio.wait_for", new=spy_wait_for
        ):
            result = await scan_open_hosts(ip_network("192.168.8.0/28"), port=5555)

        assert result == []
        assert waits == [0.5] * 14
        assert max_inflight == 3