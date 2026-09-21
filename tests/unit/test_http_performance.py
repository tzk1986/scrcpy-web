"""
性能监控 HTTP 端点（backend/app/interfaces/http/performance.py）行为测试
========================================================================

用 hand-rolled FakePerformanceService 经 dependency_overrides 注入，覆盖：
    - GET  /api/perf/{device_id}/metrics   limit 边界校验（400）× 成功（含空列表）
    - POST /api/perf/{device_id}/start     interval 边界校验（400）× 成功 × 默认值
    - POST /api/perf/{device_id}/stop      成功

不触碰真实 ADB。
"""

from typing import Any

import pytest

from app.deps import get_performance_service

from .http_testkit import make_client, override

DEV = "dev-1"
METRIC: dict[str, Any] = {
    "ts": 1726045200.123,
    "cpu_percent": 23.5,
    "total_memory_mb": 4096,
    "used_memory_mb": 2048,
    "fps": 60,
    "jank_count": 0,
    "current_activity": "com.example/.MainActivity",
    "top_package": "com.example",
}


class FakePerformanceService:
    """替身：记录调用参数，返回预置指标。"""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.metrics: list[dict[str, Any]] = []

    async def get_metrics(self, device_id: str, limit: int = 100) -> list[dict[str, Any]]:
        self.calls.append(("get_metrics", device_id, limit))
        return self.metrics

    async def start_monitoring(self, device_id: str, interval: float = 1.0) -> None:
        self.calls.append(("start_monitoring", device_id, interval))

    async def stop_monitoring(self, device_id: str) -> None:
        self.calls.append(("stop_monitoring", device_id))


@pytest.fixture
def fake() -> FakePerformanceService:
    return FakePerformanceService()


# ---------------------------------------------------------------------------
# GET /api/perf/{device_id}/metrics
# ---------------------------------------------------------------------------

def test_get_metrics_success(fake: FakePerformanceService) -> None:
    """指标查询返回 metrics 列表与 count，limit 透传。"""
    fake.metrics = [dict(METRIC)]
    client = make_client()
    with override(get_performance_service, fake):
        response = client.get(f"/api/perf/{DEV}/metrics", params={"limit": 50})

    assert response.status_code == 200
    assert response.json() == {"metrics": [METRIC], "count": 1}
    assert fake.calls == [("get_metrics", DEV, 50)]


def test_get_metrics_default_limit(fake: FakePerformanceService) -> None:
    """未传 limit 时默认 100。"""
    client = make_client()
    with override(get_performance_service, fake):
        response = client.get(f"/api/perf/{DEV}/metrics")

    assert response.status_code == 200
    assert response.json() == {"metrics": [], "count": 0}
    assert fake.calls == [("get_metrics", DEV, 100)]


@pytest.mark.parametrize("limit", [0, -1, 3601])
def test_get_metrics_limit_out_of_range_400(fake: FakePerformanceService, limit: int) -> None:
    """limit 超出 [1, 3600] → 400，且不调用服务。"""
    client = make_client()
    with override(get_performance_service, fake):
        response = client.get(f"/api/perf/{DEV}/metrics", params={"limit": limit})

    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "HTTP_ERROR"
    assert "1 and 3600" in body["error"]["message"]
    assert fake.calls == []


@pytest.mark.parametrize("limit", [1, 3600])
def test_get_metrics_limit_boundaries_ok(fake: FakePerformanceService, limit: int) -> None:
    """limit 边界值 1 / 3600 合法。"""
    client = make_client()
    with override(get_performance_service, fake):
        response = client.get(f"/api/perf/{DEV}/metrics", params={"limit": limit})

    assert response.status_code == 200
    assert fake.calls == [("get_metrics", DEV, limit)]


# ---------------------------------------------------------------------------
# POST /api/perf/{device_id}/start
# ---------------------------------------------------------------------------

def test_start_monitoring_success(fake: FakePerformanceService) -> None:
    """启动监控返回确认字段，interval 透传。"""
    client = make_client()
    with override(get_performance_service, fake):
        response = client.post(f"/api/perf/{DEV}/start", params={"interval": 2.5})

    assert response.status_code == 200
    assert response.json() == {"success": True, "device_id": DEV, "interval": 2.5}
    assert fake.calls == [("start_monitoring", DEV, 2.5)]


def test_start_monitoring_default_interval(fake: FakePerformanceService) -> None:
    """未传 interval 时默认 1.0。"""
    client = make_client()
    with override(get_performance_service, fake):
        response = client.post(f"/api/perf/{DEV}/start")

    assert response.status_code == 200
    assert response.json()["interval"] == 1.0
    assert fake.calls == [("start_monitoring", DEV, 1.0)]


@pytest.mark.parametrize("interval", [0.05, 0.0, 61, 100])
def test_start_monitoring_interval_out_of_range_400(
    fake: FakePerformanceService, interval: float
) -> None:
    """interval 超出 [0.1, 60] → 400，且不调用服务。"""
    client = make_client()
    with override(get_performance_service, fake):
        response = client.post(f"/api/perf/{DEV}/start", params={"interval": interval})

    assert response.status_code == 400
    assert "0.1 and 60" in response.json()["error"]["message"]
    assert fake.calls == []


@pytest.mark.parametrize("interval", [0.1, 60])
def test_start_monitoring_interval_boundaries_ok(
    fake: FakePerformanceService, interval: float
) -> None:
    """interval 边界值 0.1 / 60 合法。"""
    client = make_client()
    with override(get_performance_service, fake):
        response = client.post(f"/api/perf/{DEV}/start", params={"interval": interval})

    assert response.status_code == 200
    assert fake.calls == [("start_monitoring", DEV, interval)]


# ---------------------------------------------------------------------------
# POST /api/perf/{device_id}/stop
# ---------------------------------------------------------------------------

def test_stop_monitoring(fake: FakePerformanceService) -> None:
    """停止监控返回确认字段。"""
    client = make_client()
    with override(get_performance_service, fake):
        response = client.post(f"/api/perf/{DEV}/stop")

    assert response.status_code == 200
    assert response.json() == {"success": True, "device_id": DEV}
    assert fake.calls == [("stop_monitoring", DEV)]