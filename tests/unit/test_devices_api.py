"""
设备连接 API 不可达快速失败测试
================================

测试 POST /api/devices/connect 在 DeviceUnreachableError 时
经 openscrcpy_exception_handler 转为 400 + DEVICE_UNREACHABLE。
"""

from unittest.mock import AsyncMock, MagicMock

from app.core.exceptions import DeviceUnreachableError
from app.deps import get_device_service
from app.main import app


def test_connect_unreachable_returns_400(client):
    """connect_tcp 抛 DeviceUnreachableError → HTTP 400 且 code=DEVICE_UNREACHABLE"""
    fake_service = MagicMock()
    fake_service.connect_tcp = AsyncMock(
        side_effect=DeviceUnreachableError("192.168.8.99", 5555, "1.5 秒内无响应（连接超时）")
    )
    app.dependency_overrides[get_device_service] = lambda: fake_service
    try:
        response = client.post(
            "/api/devices/connect", params={"ip": "192.168.8.99", "port": 5555}
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "DEVICE_UNREACHABLE"
    assert "不可达" in body["error"]["message"]