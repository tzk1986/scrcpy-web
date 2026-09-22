"""
网络监控 HTTP 端点（backend/app/interfaces/http/network.py）行为测试
====================================================================

方案 18 Step 1 语义上移后 HTTP 层无状态：用例经 app.dependency_overrides
注入 FakeNetworkService，验证字段序列化、protocol 过滤与 device_id 转发。

采样/缓冲/失联语义测试见 tests/application/test_network_service.py。
"""

import asyncio
from typing import Any

from app.deps import get_network_service
from app.infrastructure.network.sampler import NetworkConnection, NetworkStats

from .http_testkit import make_client, override

DEV = "unit-net-dev"


def make_stats(**overrides: Any) -> NetworkStats:
    fields: dict[str, Any] = {
        "ts": 1726000000.5,
        "rx_bytes": 111111,
        "tx_bytes": 222222,
        "rx_rate_kbps": 12.5,
        "tx_rate_kbps": 3.5,
        "active_connections": 2,
        "wifi_connected": True,
        "wifi_ssid": "MyWiFi",
    }
    fields.update(overrides)
    return NetworkStats(**fields)


def make_conn(protocol: str = "tcp", **overrides: Any) -> NetworkConnection:
    fields: dict[str, Any] = {
        "protocol": protocol,
        "local_addr": "127.0.0.1",
        "local_port": 80,
        "remote_addr": "10.0.0.1",
        "remote_port": 5555,
        "state": "ESTABLISHED",
        "uid": 1001,
    }
    fields.update(overrides)
    return NetworkConnection(**fields)


class FakeNetworkService:
    """替身：直接返回预置的 stats/connections，并记录调用。"""

    def __init__(
        self,
        stats: NetworkStats | None = None,
        connections: list[NetworkConnection] | None = None,
    ) -> None:
        self.stats = stats if stats is not None else make_stats()
        self.connections = connections if connections is not None else []
        self.stats_calls: list[str] = []
        self.conn_calls: list[str] = []

    async def get_stats(self, device_id: str) -> NetworkStats:
        self.stats_calls.append(device_id)
        return self.stats

    async def get_connections(self, device_id: str) -> list[NetworkConnection]:
        self.conn_calls.append(device_id)
        return self.connections


# ---------------------------------------------------------------------------
# GET /api/network/{device_id}/stats
# ---------------------------------------------------------------------------

def test_stats_snapshot_fields() -> None:
    """stats 响应字段与 NetworkStats 一一对应。"""
    fake = FakeNetworkService()
    with override(get_network_service, fake):
        response = make_client().get(f"/api/network/{DEV}/stats")

    assert response.status_code == 200
    assert response.json() == {
        "ts": 1726000000.5,
        "rx_bytes": 111111,
        "tx_bytes": 222222,
        "rx_rate_kbps": 12.5,
        "tx_rate_kbps": 3.5,
        "active_connections": 2,
        "wifi_connected": True,
        "wifi_ssid": "MyWiFi",
    }


def test_stats_wifi_disconnected() -> None:
    """无 WiFi 时 wifi_ssid 为 null。"""
    fake = FakeNetworkService(stats=make_stats(wifi_connected=False, wifi_ssid=None))
    with override(get_network_service, fake):
        body = make_client().get(f"/api/network/{DEV}/stats").json()

    assert body["wifi_connected"] is False
    assert body["wifi_ssid"] is None


def test_stats_forwards_device_id_to_service() -> None:
    """路径 device_id 原样转发给服务层。"""
    fake = FakeNetworkService()
    with override(get_network_service, fake):
        make_client().get(f"/api/network/{DEV}/stats")

    assert fake.stats_calls == [DEV]


# ---------------------------------------------------------------------------
# GET /api/network/{device_id}/connections
# ---------------------------------------------------------------------------

def test_connections_all_and_filter() -> None:
    """无 protocol 返回全部；指定 protocol 只返回匹配项且 total 同步收缩。"""
    conns = [make_conn("tcp"), make_conn("udp", local_port=68, uid=None)]
    fake = FakeNetworkService(connections=conns)
    with override(get_network_service, fake):
        client = make_client()
        all_body = client.get(f"/api/network/{DEV}/connections").json()
        filtered = client.get(
            f"/api/network/{DEV}/connections", params={"protocol": "udp"}
        ).json()
        missing = client.get(
            f"/api/network/{DEV}/connections", params={"protocol": "tcp6"}
        ).json()

    assert all_body["total"] == 2
    assert [c["protocol"] for c in all_body["connections"]] == ["tcp", "udp"]
    assert all_body["connections"][0] == {
        "protocol": "tcp",
        "local_addr": "127.0.0.1",
        "local_port": 80,
        "remote_addr": "10.0.0.1",
        "remote_port": 5555,
        "state": "ESTABLISHED",
        "uid": 1001,
    }
    assert all_body["connections"][1]["uid"] is None
    assert filtered["total"] == 1
    assert filtered["connections"][0]["protocol"] == "udp"
    assert missing == {"connections": [], "total": 0}


def test_connections_empty_list() -> None:
    """无连接时返回空列表与 total 0。"""
    fake = FakeNetworkService(connections=[])
    with override(get_network_service, fake):
        body = make_client().get(f"/api/network/{DEV}/connections").json()

    assert body == {"connections": [], "total": 0}


def test_connections_forwards_device_id_to_service() -> None:
    """路径 device_id 原样转发给服务层。"""
    fake = FakeNetworkService()
    with override(get_network_service, fake):
        make_client().get(f"/api/network/{DEV}/connections")

    assert fake.conn_calls == [DEV]


def test_fake_exposes_service_interface() -> None:
    """替身实现服务层同一接口（get_stats / get_connections 均为 async）。"""
    fake = FakeNetworkService()
    assert asyncio.iscoroutinefunction(fake.get_stats)
    assert asyncio.iscoroutinefunction(fake.get_connections)