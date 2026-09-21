"""
健康检查与设备列表冒烟测试
============================

测试 /health 端点和设备列表端点的基本功能。

这些是最基础的冒烟测试，确保应用能正常启动和响应请求。
设备列表例用真实 DeviceService + 假基础设施（ADB 驱动/仓库）覆盖依赖，
断言不依赖真实 adb 与设备（CI runner 无设备；真机通路由 tests/e2e 覆盖）。
"""

from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from app.application.device_service import DeviceService
from app.deps import get_device_service
from app.domain.device import DeviceInfo
from app.main import app


def test_health(client: TestClient):
    """测试健康检查端点返回 200 和正确的 JSON。"""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_list_devices(client: TestClient):
    """测试设备列表端点返回 200 和列表格式（假 ADB 驱动 + 假仓库）。"""
    fake_adb = MagicMock()
    fake_adb.list_devices = AsyncMock(return_value=["FAKE-5555"])
    fake_adb.get_device_info = AsyncMock(
        return_value=DeviceInfo(
            id="FAKE-5555",
            model="SmokeTest",
            os_version="14",
            resolution=(1080, 1920),
            battery=100,
            status="online",
        )
    )
    fake_repo = MagicMock()
    fake_repo.save = AsyncMock()

    app.dependency_overrides[get_device_service] = lambda: DeviceService(
        adb=fake_adb, repo=fake_repo
    )
    try:
        response = client.get("/api/devices")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    assert body[0]["id"] == "FAKE-5555"