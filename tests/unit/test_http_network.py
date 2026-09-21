"""
网络监控 HTTP 端点（backend/app/interfaces/http/network.py）行为测试
====================================================================

覆盖：
    - GET /api/network/{device_id}/stats       快照字段序列化
    - GET /api/network/{device_id}/connections 全量 / protocol 过滤 / 空列表
    - _get_sampler 模块级缓存：按设备复用、访问时间刷新、TTL 过期清理

Sampler 缓存是模块级全局状态，用例通过 save/restore fixture 隔离。
真实 NetworkSampler 路径用假 ADB（shell 返回空串）驱动，不碰真机。
"""

import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.infrastructure.network.sampler import (
    NetworkConnection,
    NetworkSampler,
    NetworkStats,
)
from app.interfaces.http import network

from .http_testkit import make_client

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


class FakeSampler:
    """替身：直接返回预置的 stats/connections。"""

    def __init__(self, stats: NetworkStats | None = None,
                 connections: list[NetworkConnection] | None = None) -> None:
        self.stats = stats if stats is not None else make_stats()
        self.connections = connections if connections is not None else []

    async def get_stats(self, device_id: str) -> NetworkStats:
        return self.stats

    async def get_connections(self, device_id: str) -> list[NetworkConnection]:
        return self.connections


@pytest.fixture(autouse=True)
def isolate_sampler_cache():
    """隔离 network._samplers 全局缓存（save/restore）。"""
    saved = dict(network._samplers)
    network._samplers.clear()
    yield
    network._samplers.clear()
    network._samplers.update(saved)


# ---------------------------------------------------------------------------
# GET /api/network/{device_id}/stats
# ---------------------------------------------------------------------------

def test_stats_snapshot_fields() -> None:
    """stats 响应字段与 NetworkStats 一一对应。"""
    network._samplers[DEV] = (FakeSampler(), time.time())
    client = make_client()

    response = client.get(f"/api/network/{DEV}/stats")

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
    network._samplers[DEV] = (
        FakeSampler(stats=make_stats(wifi_connected=False, wifi_ssid=None)),
        time.time(),
    )
    client = make_client()

    body = client.get(f"/api/network/{DEV}/stats").json()

    assert body["wifi_connected"] is False
    assert body["wifi_ssid"] is None


# ---------------------------------------------------------------------------
# GET /api/network/{device_id}/connections
# ---------------------------------------------------------------------------

def test_connections_all_and_filter() -> None:
    """无 protocol 返回全部；指定 protocol 只返回匹配项且 total 同步收缩。"""
    conns = [make_conn("tcp"), make_conn("udp", local_port=68, uid=None)]
    network._samplers[DEV] = (FakeSampler(connections=conns), time.time())
    client = make_client()

    all_body = client.get(f"/api/network/{DEV}/connections").json()
    filtered = client.get(f"/api/network/{DEV}/connections", params={"protocol": "udp"}).json()
    missing = client.get(f"/api/network/{DEV}/connections", params={"protocol": "tcp6"}).json()

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


# ---------------------------------------------------------------------------
# _get_sampler 缓存行为
# ---------------------------------------------------------------------------

def test_sampler_created_and_reused(monkeypatch: pytest.MonkeyPatch) -> None:
    """首次访问创建真实 NetworkSampler 并缓存；再次访问复用同一实例并刷新时间。"""
    fake_adb = MagicMock()
    fake_adb.shell = AsyncMock(return_value="")
    monkeypatch.setattr("app.deps.get_adb_driver", lambda: fake_adb)
    client = make_client()

    first = client.get(f"/api/network/{DEV}/stats")
    assert first.status_code == 200
    sampler, first_access = network._samplers[DEV]
    assert isinstance(sampler, NetworkSampler)
    assert first.json()["rx_bytes"] == 0  # 空输出 → 零流量

    second = client.get(f"/api/network/{DEV}/stats")
    assert second.status_code == 200
    sampler2, second_access = network._samplers[DEV]
    assert sampler2 is sampler
    assert second_access >= first_access


def test_expired_sampler_evicted(monkeypatch: pytest.MonkeyPatch) -> None:
    """超过 TTL 的缓存项在下次取值时被清理。"""
    stale = FakeSampler()
    network._samplers["stale-dev"] = (stale, time.time() - network._SAMPLER_TTL - 1)
    fake_adb = MagicMock()
    fake_adb.shell = AsyncMock(return_value="")
    monkeypatch.setattr("app.deps.get_adb_driver", lambda: fake_adb)
    client = make_client()

    response = client.get(f"/api/network/{DEV}/stats")

    assert response.status_code == 200
    assert "stale-dev" not in network._samplers
    assert DEV in network._samplers