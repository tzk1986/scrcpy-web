"""
健康检查端点单元测试
======================

测试 /health 端点和设备列表端点的基本功能。

这些是最基础的冒烟测试，确保应用能正常启动和响应请求。
"""

from fastapi.testclient import TestClient


def test_health(client: TestClient):
    """测试健康检查端点返回 200 和正确的 JSON。"""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_list_devices(client: TestClient):
    """测试设备列表端点返回 200 和列表格式。"""
    response = client.get("/api/devices")
    assert response.status_code == 200
    assert isinstance(response.json(), list)
