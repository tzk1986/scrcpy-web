"""
Perf / Network 导出与录制端点（方案 18 Step 4-6）行为测试
=========================================================

覆盖（perf 与 network 两套端点对称）：
    - GET  export：CSV 内容（表头、ts_iso、数据行）、JSON 内容、
      Content-Disposition 文件名净化（device_id 含 ':' → '_'）、
      X-Export-Count / X-Export-Oldest-Ts / X-Export-Newest-Ts 元数据头
    - export 参数校验：format/source/limit 越界 → 400；空数据 → 404 NO_DATA
    - export source 选择：buffer 走服务内存快照、cache 走区间查询（from/to/limit 透传）
    - POST record/start、record/stop、GET record/status 转发服务层结果
    - metrics.recording=false 时 record/start → 400 RECORDING_DISABLED（不静默）

服务层替身经 app.dependency_overrides 注入，不触碰真实 ADB / SQLite。
"""

from datetime import datetime
from typing import Any

import pytest

from app.core.exceptions import RecordingDisabledError
from app.deps import get_network_service, get_performance_service

from .http_testkit import make_client, override

DEV = "192.168.8.18:5555"

PERF_ROWS: list[dict[str, Any]] = [
    {
        "ts": 1726045200.123,
        "cpu_percent": 23.5,
        "total_memory_mb": 4096.0,
        "used_memory_mb": 2048.0,
        "fps": 60.0,
        "jank_count": 0,
        "current_activity": "com.example/.MainActivity",
        "top_package": "com.example",
    },
    {
        "ts": 1726045260.5,
        "cpu_percent": 41.0,
        "total_memory_mb": 4096.0,
        "used_memory_mb": 3000.0,
        "fps": 58.0,
        "jank_count": 7,
        "current_activity": "com.example/.MainActivity",
        "top_package": "com.example",
    },
]

NET_ROWS: list[dict[str, Any]] = [
    {
        "ts": 1726000000.5,
        "rx_bytes": 111111,
        "tx_bytes": 222222,
        "rx_rate_kbps": 12.5,
        "tx_rate_kbps": 3.5,
        "active_connections": 2,
        "wifi_connected": True,
        "wifi_ssid": "MyWiFi",
    },
    {
        "ts": 1726000120.5,
        "rx_bytes": 333333,
        "tx_bytes": 444444,
        "rx_rate_kbps": 90.0,
        "tx_rate_kbps": 10.0,
        "active_connections": 5,
        "wifi_connected": True,
        "wifi_ssid": "MyWiFi",
    },
]


class FakePerformanceService:
    """替身：记录调用参数，返回预置行；record 方法返回预置状态。"""

    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = list(rows or [])
        self.metrics_calls: list[tuple[str, int]] = []
        self.export_calls: list[tuple[str, float | None, float | None, int]] = []
        self.record_calls: list[tuple[str, str]] = []
        self.record_result: dict[str, Any] = {
            "recording": True,
            "reason": None,
            "rows": 2,
            "oldest_ts": 1726045200.123,
            "newest_ts": 1726045260.5,
        }

    async def get_metrics(self, device_id: str, limit: int = 100) -> list[dict[str, Any]]:
        self.metrics_calls.append((device_id, limit))
        return self.rows[-limit:]

    async def export_cached(
        self,
        device_id: str,
        from_ts: float | None = None,
        to_ts: float | None = None,
        limit: int = 50000,
    ) -> list[dict[str, Any]]:
        self.export_calls.append((device_id, from_ts, to_ts, limit))
        return self.rows

    async def record_start(self, device_id: str) -> dict[str, Any]:
        self.record_calls.append(("start", device_id))
        return self.record_result

    async def record_stop(self, device_id: str) -> dict[str, Any]:
        self.record_calls.append(("stop", device_id))
        return {**self.record_result, "recording": False, "reason": "stopped"}

    async def record_status(self, device_id: str) -> dict[str, Any]:
        self.record_calls.append(("status", device_id))
        return self.record_result


class FakeNetworkService:
    """替身：与 FakePerformanceService 同构，buffer 导出走同步快照方法。"""

    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = list(rows or [])
        self.snapshot_calls: list[tuple[str, int]] = []
        self.export_calls: list[tuple[str, float | None, float | None, int]] = []
        self.record_calls: list[tuple[str, str]] = []
        self.record_result: dict[str, Any] = {
            "recording": True,
            "reason": None,
            "rows": 2,
            "oldest_ts": 1726000000.5,
            "newest_ts": 1726000120.5,
        }

    def get_buffer_snapshot(self, device_id: str, limit: int = 50000) -> list[dict[str, Any]]:
        self.snapshot_calls.append((device_id, limit))
        return self.rows[-limit:]

    async def export_cached(
        self,
        device_id: str,
        from_ts: float | None = None,
        to_ts: float | None = None,
        limit: int = 50000,
    ) -> list[dict[str, Any]]:
        self.export_calls.append((device_id, from_ts, to_ts, limit))
        return self.rows

    async def record_start(self, device_id: str) -> dict[str, Any]:
        self.record_calls.append(("start", device_id))
        return self.record_result

    async def record_stop(self, device_id: str) -> dict[str, Any]:
        self.record_calls.append(("stop", device_id))
        return {**self.record_result, "recording": False, "reason": "stopped"}

    async def record_status(self, device_id: str) -> dict[str, Any]:
        self.record_calls.append(("status", device_id))
        return self.record_result


def fail_recording_start(dep: Any, monkeypatch: pytest.MonkeyPatch, fake: Any) -> None:
    """让 record_start 抛 RecordingDisabledError（模拟 metrics.recording=false）。"""
    if dep is get_performance_service:
        async def disabled(device_id: str) -> dict[str, Any]:
            raise RecordingDisabledError()
        monkeypatch.setattr(fake, "record_start", disabled)
    else:
        async def disabled_net(device_id: str) -> dict[str, Any]:
            raise RecordingDisabledError()
        monkeypatch.setattr(fake, "record_start", disabled_net)


def expect_iso(ts: float) -> str:
    return datetime.fromtimestamp(ts).isoformat()


# ---------------------------------------------------------------------------
# Perf export：内容与元数据
# ---------------------------------------------------------------------------

def test_perf_export_csv_content_and_disposition() -> None:
    """CSV 含表头/ts/ts_iso/数据值；Content-Disposition 文件名净化 ':'→'_'。"""
    fake = FakePerformanceService(PERF_ROWS)
    with override(get_performance_service, fake):
        res = make_client().get(f"/api/perf/{DEV}/export", params={"format": "csv"})

    assert res.status_code == 200
    assert "text/csv" in res.headers["content-type"]

    header, line1, line2 = res.text.splitlines()
    assert header == ("ts,ts_iso,cpu_percent,total_memory_mb,used_memory_mb,"
                      "fps,jank_count,current_activity,top_package")
    assert "1726045200.123" in line1
    assert expect_iso(1726045200.123) in line1
    assert "23.5" in line1
    assert "com.example/.MainActivity" in line1
    assert "1726045260.5" in line2
    assert "7" in line2

    # 文件名：device_id "192.168.8.18:5555" → 净化后不含 ':'
    disposition = res.headers["content-disposition"]
    assert disposition.startswith(f'attachment; filename="perf_192.168.8.18_5555_')
    assert disposition.endswith('.csv"')
    assert ":" not in disposition

    # 元数据头（O2）：行数与首尾 ts
    assert res.headers["x-export-count"] == "2"
    assert res.headers["x-export-oldest-ts"] == "1726045200.123"
    assert res.headers["x-export-newest-ts"] == "1726045260.5"


def test_perf_export_json_content() -> None:
    """JSON 导出恢复字段；media_type 为 application/json。"""
    fake = FakePerformanceService(PERF_ROWS)
    with override(get_performance_service, fake):
        res = make_client().get(f"/api/perf/{DEV}/export", params={"format": "json"})

    assert res.status_code == 200
    assert "application/json" in res.headers["content-type"]
    assert res.json() == PERF_ROWS
    assert res.headers["x-export-count"] == "2"


def test_perf_export_source_buffer_uses_get_metrics() -> None:
    """source=buffer：走内存快照 get_metrics，忽略 from/to。"""
    fake = FakePerformanceService(PERF_ROWS)
    with override(get_performance_service, fake):
        res = make_client().get(f"/api/perf/{DEV}/export", params={
            "source": "buffer", "limit": 50, "from": 1.0, "to": 9.0,
        })

    assert res.status_code == 200
    assert fake.metrics_calls == [(DEV, 50)]
    assert fake.export_calls == []


def test_perf_export_source_cache_forwards_range() -> None:
    """source=cache：from/to/limit 原样透传给服务层区间查询。"""
    fake = FakePerformanceService(PERF_ROWS)
    with override(get_performance_service, fake):
        res = make_client().get(f"/api/perf/{DEV}/export", params={
            "source": "cache", "format": "json", "from": 1726045200.0,
            "to": 1726045260.0, "limit": 100,
        })

    assert res.status_code == 200
    assert fake.export_calls == [(DEV, 1726045200.0, 1726045260.0, 100)]
    assert fake.metrics_calls == []


# ---------------------------------------------------------------------------
# Perf export：参数校验与空数据
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("params,message", [
    ({"format": "xml"}, "format must be csv or json"),
    ({"source": "disk"}, "source must be buffer or cache"),
    ({"limit": 0}, "limit must be between 1 and 200000"),
    ({"limit": 200001}, "limit must be between 1 and 200000"),
])
def test_perf_export_invalid_params_400(params: dict[str, Any], message: str) -> None:
    fake = FakePerformanceService(PERF_ROWS)
    with override(get_performance_service, fake):
        res = make_client().get(f"/api/perf/{DEV}/export", params=params)

    assert res.status_code == 400
    assert res.json()["error"]["code"] == "HTTP_ERROR"
    assert res.json()["error"]["message"] == message


@pytest.mark.parametrize("source", ["buffer", "cache"])
def test_perf_export_empty_data_404(source: str) -> None:
    """无数据（两来源均空）→ 404 NO_DATA。"""
    fake = FakePerformanceService([])
    with override(get_performance_service, fake):
        res = make_client().get(f"/api/perf/{DEV}/export", params={"source": source})

    assert res.status_code == 404
    assert res.json()["error"]["code"] == "HTTP_ERROR"
    assert res.json()["error"]["message"] == "NO_DATA"


# ---------------------------------------------------------------------------
# Perf record 端点
# ---------------------------------------------------------------------------

def test_perf_record_start_forwards_service_result() -> None:
    fake = FakePerformanceService()
    with override(get_performance_service, fake):
        res = make_client().post(f"/api/perf/{DEV}/record/start")

    assert res.status_code == 200
    assert res.json() == fake.record_result
    assert fake.record_calls == [("start", DEV)]


def test_perf_record_stop_forwards_and_returns_rows() -> None:
    fake = FakePerformanceService()
    with override(get_performance_service, fake):
        res = make_client().post(f"/api/perf/{DEV}/record/stop")

    assert res.status_code == 200
    body = res.json()
    assert body["recording"] is False
    assert body["reason"] == "stopped"
    assert body["rows"] == 2
    assert fake.record_calls == [("stop", DEV)]


def test_perf_record_status_forwards() -> None:
    fake = FakePerformanceService()
    with override(get_performance_service, fake):
        res = make_client().get(f"/api/perf/{DEV}/record/status")

    assert res.status_code == 200
    assert res.json() == fake.record_result
    assert fake.record_calls == [("status", DEV)]


def test_perf_record_start_disabled_400(monkeypatch: pytest.MonkeyPatch) -> None:
    """metrics.recording=false 时服务抛 RecordingDisabledError → 400，不静默。"""
    fake = FakePerformanceService()
    fail_recording_start(get_performance_service, monkeypatch, fake)
    with override(get_performance_service, fake):
        res = make_client().post(f"/api/perf/{DEV}/record/start")

    assert res.status_code == 400
    assert res.json()["error"]["code"] == "RECORDING_DISABLED"
    assert "disabled" in res.json()["error"]["message"]


# ---------------------------------------------------------------------------
# Network export：内容与元数据
# ---------------------------------------------------------------------------

def test_network_export_csv_content_and_disposition() -> None:
    """CSV 表头/数据；网络行 wifi_ssid 字段入列；文件名 ':'→'_'。"""
    fake = FakeNetworkService(NET_ROWS)
    with override(get_network_service, fake):
        res = make_client().get(f"/api/network/{DEV}/export", params={"format": "csv"})

    assert res.status_code == 200
    assert "text/csv" in res.headers["content-type"]

    header, line1, line2 = res.text.splitlines()
    assert header == ("ts,ts_iso,rx_bytes,tx_bytes,rx_rate_kbps,"
                      "tx_rate_kbps,active_connections,wifi_connected,wifi_ssid")
    assert "1726000000.5" in line1
    assert expect_iso(1726000000.5) in line1
    assert "111111" in line1
    assert "MyWiFi" in line1
    assert "1726000120.5" in line2

    disposition = res.headers["content-disposition"]
    assert disposition.startswith(f'attachment; filename="network_192.168.8.18_5555_')
    assert disposition.endswith('.csv"')
    assert ":" not in disposition

    assert res.headers["x-export-count"] == "2"
    assert res.headers["x-export-oldest-ts"] == "1726000000.5"
    assert res.headers["x-export-newest-ts"] == "1726000120.5"


def test_network_export_json_content() -> None:
    fake = FakeNetworkService(NET_ROWS)
    with override(get_network_service, fake):
        res = make_client().get(f"/api/network/{DEV}/export", params={"format": "json"})

    assert res.status_code == 200
    assert "application/json" in res.headers["content-type"]
    assert res.json() == NET_ROWS


def test_network_export_source_buffer_uses_snapshot() -> None:
    """source=buffer：走同步 get_buffer_snapshot。"""
    fake = FakeNetworkService(NET_ROWS)
    with override(get_network_service, fake):
        res = make_client().get(f"/api/network/{DEV}/export", params={"limit": 1})

    assert res.status_code == 200
    assert res.headers["x-export-count"] == "1"
    assert res.headers["x-export-oldest-ts"] == "1726000120.5"  # 最新 1 条
    assert fake.snapshot_calls == [(DEV, 1)]
    assert fake.export_calls == []


def test_network_export_source_cache_forwards_range() -> None:
    fake = FakeNetworkService(NET_ROWS)
    with override(get_network_service, fake):
        res = make_client().get(f"/api/network/{DEV}/export", params={
            "source": "cache", "format": "json", "from": 1726000000.0,
            "to": 1726000120.0, "limit": 100,
        })

    assert res.status_code == 200
    assert fake.export_calls == [(DEV, 1726000000.0, 1726000120.0, 100)]
    assert fake.snapshot_calls == []


# ---------------------------------------------------------------------------
# Network export：参数校验与空数据
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("params,message", [
    ({"format": "xml"}, "format must be csv or json"),
    ({"source": "disk"}, "source must be buffer or cache"),
    ({"limit": 0}, "limit must be between 1 and 200000"),
    ({"limit": 200001}, "limit must be between 1 and 200000"),
])
def test_network_export_invalid_params_400(params: dict[str, Any], message: str) -> None:
    fake = FakeNetworkService(NET_ROWS)
    with override(get_network_service, fake):
        res = make_client().get(f"/api/network/{DEV}/export", params=params)

    assert res.status_code == 400
    assert res.json()["error"]["code"] == "HTTP_ERROR"
    assert res.json()["error"]["message"] == message


@pytest.mark.parametrize("source", ["buffer", "cache"])
def test_network_export_empty_data_404(source: str) -> None:
    fake = FakeNetworkService([])
    with override(get_network_service, fake):
        res = make_client().get(f"/api/network/{DEV}/export", params={"source": source})

    assert res.status_code == 404
    assert res.json()["error"]["code"] == "HTTP_ERROR"
    assert res.json()["error"]["message"] == "NO_DATA"


# ---------------------------------------------------------------------------
# Network record 端点
# ---------------------------------------------------------------------------

def test_network_record_start_forwards_service_result() -> None:
    fake = FakeNetworkService()
    with override(get_network_service, fake):
        res = make_client().post(f"/api/network/{DEV}/record/start")

    assert res.status_code == 200
    assert res.json() == fake.record_result
    assert fake.record_calls == [("start", DEV)]


def test_network_record_stop_forwards_and_returns_rows() -> None:
    fake = FakeNetworkService()
    with override(get_network_service, fake):
        res = make_client().post(f"/api/network/{DEV}/record/stop")

    assert res.status_code == 200
    body = res.json()
    assert body["recording"] is False
    assert body["reason"] == "stopped"
    assert body["rows"] == 2
    assert fake.record_calls == [("stop", DEV)]


def test_network_record_status_forwards() -> None:
    fake = FakeNetworkService()
    with override(get_network_service, fake):
        res = make_client().get(f"/api/network/{DEV}/record/status")

    assert res.status_code == 200
    assert res.json() == fake.record_result
    assert fake.record_calls == [("status", DEV)]


def test_network_record_start_disabled_400(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeNetworkService()
    fail_recording_start(get_network_service, monkeypatch, fake)
    with override(get_network_service, fake):
        res = make_client().post(f"/api/network/{DEV}/record/start")

    assert res.status_code == 400
    assert res.json()["error"]["code"] == "RECORDING_DISABLED"
    assert "disabled" in res.json()["error"]["message"]