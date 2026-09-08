"""
Pytest 测试配置
================

提供测试用的 fixture：
    - client: FastAPI 测试客户端（TestClient）
    - mock_adb: Mock ADB 驱动（避免真实设备依赖）

测试目录结构：
    tests/
        conftest.py      — 本文件，共享 fixture
        unit/            — 单元测试（不依赖外部服务）
        integration/     — 集成测试（可能需要数据库等）
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    """
    FastAPI 测试客户端。

    使用 TestClient 模拟 HTTP 请求，无需启动真实服务器。
    """
    return TestClient(app)


@pytest.fixture
def mock_adb():
    """
    Mock ADB 驱动。

    模拟 AdbDriver 协议，返回预设的测试数据。
    用于单元测试中避免依赖真实 ADB 连接。
    """
    from unittest.mock import AsyncMock, MagicMock

    from app.domain.ports import AdbDriver

    mock = MagicMock(spec=AdbDriver)
    mock.list_devices = AsyncMock(return_value=["emulator-5554"])
    mock.get_device_info = AsyncMock()
    mock.shell = AsyncMock(return_value="mock output")
    return mock
