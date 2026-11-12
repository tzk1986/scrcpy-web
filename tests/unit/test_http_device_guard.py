"""
HTTP 入站 device_id 统一校验（方案 58 C-16，8.7 入站标识校验接线）
==================================================================

is_valid_device_id 自方案 37 A5 起就有谓词实现，但 HTTP 路径参数从未接线：
垃圾键可直通 adb argv、DB 查询与缓冲字典键。本批次把守卫作为**路由级依赖**
挂到六个含 /{device_id} 路径参数的路由（devices/apps/files/network/
performance/recording）：

    - 非法 device_id → 400 INVALID_IDENTIFIER，端点与服务层不执行
      （替身收不到任何调用）；
    - 合法 device_id → 放行，服务层被正常调用；
    - 不带 device_id 的字面路由（如 GET /api/devices 列表）不受影响。

不触碰真实 ADB：全部端点均以最小替身覆盖依赖。
"""

from typing import Any
from urllib.parse import quote

import pytest

from app.deps import (
    get_app_service,
    get_device_service,
    get_file_service,
    get_network_service,
    get_performance_service,
    get_recording_service,
)
from app.domain.device import DeviceInfo
from app.domain.runtime import NetworkStats

from .http_testkit import make_client, override

VALID = "192.168.1.5:5555"

# 非法但可出现在 URL 路径里的载荷：守卫须在读到服务层之前拦截
INVALID_IDS = ["evil;id", "evil$id", "evil(id", "a b", "evil|pipe"]


class FakeDeviceService:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    async def list_devices(self) -> list[DeviceInfo]:
        self.calls.append(("list_devices",))
        return []

    async def get_device(self, device_id: str) -> DeviceInfo:
        self.calls.append(("get_device", device_id))
        return DeviceInfo(
            id=device_id, model="M", os_version="13",
            resolution=(100, 200), battery=50, status="online",
        )


class FakeAppService:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    async def list_apps(self, device_id: str, include_system: bool = False) -> list[Any]:
        self.calls.append(("list_apps", device_id))
        return []


class FakeFileService:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    async def list_dir(self, device_id: str, path: str) -> list[Any]:
        self.calls.append(("list_dir", device_id))
        return []


class FakeNetworkService:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    async def get_stats(self, device_id: str) -> NetworkStats:
        self.calls.append(("get_stats", device_id))
        return NetworkStats(
            ts=0.0, rx_bytes=0, tx_bytes=0, rx_rate_kbps=0.0, tx_rate_kbps=0.0,
            active_connections=0, wifi_connected=False, wifi_ssid=None,
        )

    def get_alerts(self, device_id: str, ts: float) -> list[Any]:
        return []


class FakePerformanceService:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    def effective_buffer_capacity(self, device_id: str) -> int:
        # 方案 58 C-08：端点校验改问服务有效容量（守卫用例给足容量）
        return 3600

    async def get_metrics(self, device_id: str, limit: int) -> list[Any]:
        self.calls.append(("get_metrics", device_id))
        return []


class FakeRecordingService:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    def get(self, device_id: str) -> Any:
        self.calls.append(("get", device_id))
        return None  # 无会话 → 404：证明守卫放行后走到了服务层


PROBES = [
    pytest.param(get_device_service, FakeDeviceService, "/api/devices/{device_id}", id="devices"),
    pytest.param(get_app_service, FakeAppService, "/api/apps/{device_id}", id="apps"),
    pytest.param(get_file_service, FakeFileService, "/api/devices/{device_id}/files", id="files"),
    pytest.param(get_network_service, FakeNetworkService, "/api/network/{device_id}/stats", id="network"),
    pytest.param(get_performance_service, FakePerformanceService, "/api/perf/{device_id}/metrics?limit=1", id="perf"),
    pytest.param(get_recording_service, FakeRecordingService, "/api/recording/{device_id}", id="recording"),
]


@pytest.mark.parametrize("bad_id", INVALID_IDS)
@pytest.mark.parametrize("dep,fake_cls,url_template", PROBES)
def test_router_guard_rejects_invalid_device_id(dep, fake_cls, url_template, bad_id) -> None:
    fake = fake_cls()
    client = make_client()
    url = url_template.replace("{device_id}", quote(bad_id))

    with override(dep, fake):
        response = client.get(url)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_IDENTIFIER"
    assert fake.calls == []


@pytest.mark.parametrize("dep,fake_cls,url_template", PROBES)
def test_guard_passes_valid_device_id(dep, fake_cls, url_template) -> None:
    fake = fake_cls()
    client = make_client()
    url = url_template.replace("{device_id}", VALID)

    with override(dep, fake):
        response = client.get(url)

    assert fake.calls == [(fake.calls[0][0], VALID)]
    assert response.status_code in (200, 404)


def test_literal_route_without_device_id_unaffected() -> None:
    """devices 路由挂了守卫后，无 device_id 的字面路由照常放行。"""
    fake = FakeDeviceService()
    client = make_client()

    with override(get_device_service, fake):
        response = client.get("/api/devices")

    assert response.status_code == 200
    assert fake.calls == [("list_devices",)]
