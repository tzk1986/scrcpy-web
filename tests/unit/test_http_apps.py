"""
应用管理 HTTP 端点（backend/app/interfaces/http/apps.py）行为测试
=================================================================

用 hand-rolled FakeAppService 经 dependency_overrides 注入，覆盖 8 个端点：
    - GET  /api/apps/{device_id}                     列表（统计聚合、空列表、include_system）
    - GET  /api/apps/{device_id}/{package}           详情（命中 / 404）
    - POST /api/apps/{device_id}/{package}/launch    启动（成功 / 500）
    - POST /api/apps/{device_id}/{package}/stop      停止（成功 / 500）
    - POST /api/apps/{device_id}/{package}/uninstall 卸载（成功 / 500）
    - POST /api/apps/{device_id}/{package}/clear-data 清数据（成功 / 500）
    - GET  /api/apps/{device_id}/{package}/memory    内存（命中 / 404）
    - GET  /api/apps/{device_id}/{package}/runtime   运行时状态（结构六键 / 序列化归一 / 透传）

不触碰真实 ADB。
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.application.app_service import AppInfo, AppService
from app.core.exceptions import AdbError, InvalidIdentifierError
from app.deps import get_app_service
from app.domain.runtime import RuntimeInfo, ServiceInfo

from .http_testkit import make_client, override

DEV = "dev-1"
PKG = "com.example.app"


def make_app_info(**overrides: Any) -> AppInfo:
    """构造 AppInfo，未指定的字段取默认值。"""
    fields: dict[str, Any] = {
        "package_name": PKG,
        "version_name": "1.2.3",
        "version_code": 123,
        "install_time": "2025-01-01 10:00:00",
        "update_time": "2025-06-01 11:30:00",
        "apk_size_mb": 12.5,
        "is_system": False,
        "is_running": True,
        "pid": 1234,
        "memory_kb": 2048,
    }
    fields.update(overrides)
    return AppInfo(**fields)


class FakeAppService:
    """替身：记录调用参数，返回预置结果。"""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.apps: list[AppInfo] = []
        self.app_info: AppInfo | None = None
        self.launch_result = True
        self.stop_result = True
        self.uninstall_result = True
        self.clear_result = True
        self.memory_kb: int | None = 4096
        self.runtime_info: dict[str, Any] = {
            "pkg": PKG,
            "top_activity": f"{PKG}/.MainActivity",
            "resumed": f"{PKG}/.MainActivity",
            "services": [{"name": f"{PKG}/.Worker", "state": "running"}],
            "providers": [f"{PKG}/.Provider"],
            "parse_warning": "",
        }
        self.runtime_error: Exception | None = None
        self.read_error: Exception | None = None

    async def list_apps(self, device_id: str, include_system: bool = False) -> list[AppInfo]:
        self.calls.append(("list_apps", device_id, include_system))
        if self.read_error is not None:
            raise self.read_error
        return self.apps

    async def get_app_info(self, device_id: str, package: str) -> AppInfo | None:
        self.calls.append(("get_app_info", device_id, package))
        if self.read_error is not None:
            raise self.read_error
        return self.app_info

    async def launch_app(self, device_id: str, package: str) -> bool:
        self.calls.append(("launch_app", device_id, package))
        return self.launch_result

    async def stop_app(self, device_id: str, package: str) -> bool:
        self.calls.append(("stop_app", device_id, package))
        return self.stop_result

    async def uninstall_app(self, device_id: str, package: str) -> bool:
        self.calls.append(("uninstall_app", device_id, package))
        return self.uninstall_result

    async def clear_app_data(self, device_id: str, package: str) -> bool:
        self.calls.append(("clear_app_data", device_id, package))
        return self.clear_result

    async def get_app_memory(self, device_id: str, package: str) -> int | None:
        self.calls.append(("get_app_memory", device_id, package))
        return self.memory_kb

    async def get_runtime_info(self, device_id: str, package: str) -> dict[str, Any]:
        self.calls.append(("get_runtime_info", device_id, package))
        if self.runtime_error is not None:
            raise self.runtime_error
        return self.runtime_info


@pytest.fixture
def fake() -> FakeAppService:
    return FakeAppService()


# ---------------------------------------------------------------------------
# GET /api/apps/{device_id}
# ---------------------------------------------------------------------------

def test_list_apps_aggregates_stats(fake: FakeAppService) -> None:
    """列表返回完整字段，并聚合运行数与总内存（MB，保留 1 位）。"""
    fake.apps = [
        make_app_info(is_running=True, memory_kb=2048),
        make_app_info(package_name="com.example.two", is_running=False, pid=None, memory_kb=None),
    ]
    client = make_client()
    with override(get_app_service, fake):
        response = client.get(f"/api/apps/{DEV}")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert body["running_count"] == 1
    assert body["total_memory_mb"] == 2.0  # (2048 + 0) / 1024
    assert body["apps"][0] == {
        "package_name": PKG,
        "version_name": "1.2.3",
        "version_code": 123,
        "install_time": "2025-01-01 10:00:00",
        "update_time": "2025-06-01 11:30:00",
        "apk_size_mb": 12.5,
        "is_system": False,
        "is_running": True,
        "pid": 1234,
        "memory_kb": 2048,
    }
    assert body["apps"][1]["pid"] is None
    assert fake.calls == [("list_apps", DEV, False)]


def test_list_apps_include_system_query_param(fake: FakeAppService) -> None:
    """include_system=true 透传给服务。"""
    client = make_client()
    with override(get_app_service, fake):
        response = client.get(f"/api/apps/{DEV}", params={"include_system": "true"})

    assert response.status_code == 200
    assert fake.calls == [("list_apps", DEV, True)]


def test_list_apps_empty(fake: FakeAppService) -> None:
    """空列表返回零统计。"""
    client = make_client()
    with override(get_app_service, fake):
        response = client.get(f"/api/apps/{DEV}")

    assert response.json() == {
        "apps": [],
        "total": 0,
        "running_count": 0,
        "total_memory_mb": 0.0,
    }


# ---------------------------------------------------------------------------
# GET /api/apps/{device_id}/{package}
# ---------------------------------------------------------------------------

def test_get_app_info_found(fake: FakeAppService) -> None:
    """详情命中返回应用字段。"""
    fake.app_info = make_app_info()
    client = make_client()
    with override(get_app_service, fake):
        response = client.get(f"/api/apps/{DEV}/{PKG}")

    assert response.status_code == 200
    assert response.json()["package_name"] == PKG
    assert fake.calls == [("get_app_info", DEV, PKG)]


def test_get_app_info_not_found_404(fake: FakeAppService) -> None:
    """服务返回 None → 404。"""
    client = make_client()
    with override(get_app_service, fake):
        response = client.get(f"/api/apps/{DEV}/{PKG}")

    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "HTTP_ERROR"
    assert PKG in body["error"]["message"]


# 方案 51 C9：ADB 故障不再吞成假空列表/假 404，经既有映射明确报错（裁决记档：
# 方案文本「502」有误，AdbError 既有映射为 400/ADB_ERROR）


def test_list_apps_adb_error_maps_400(fake: FakeAppService) -> None:
    """列表 ADB 故障上抛 → 400 ADB_ERROR（而非 200 空列表）。"""
    fake.read_error = AdbError("device offline")
    client = make_client()
    with override(get_app_service, fake):
        response = client.get(f"/api/apps/{DEV}")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "ADB_ERROR"


def test_get_app_info_adb_error_maps_400_not_404(fake: FakeAppService) -> None:
    """详情 ADB 故障上抛 → 400 ADB_ERROR（而非假 404）。"""
    fake.read_error = AdbError("device offline")
    client = make_client()
    with override(get_app_service, fake):
        response = client.get(f"/api/apps/{DEV}/{PKG}")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "ADB_ERROR"


# ---------------------------------------------------------------------------
# POST 操作类端点
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("path", "call_name"),
    [
        ("launch", "launch_app"),
        ("stop", "stop_app"),
        ("uninstall", "uninstall_app"),
        ("clear-data", "clear_app_data"),
    ],
)
def test_actions_success(fake: FakeAppService, path: str, call_name: str) -> None:
    """四个操作端点成功时返回 success + package。"""
    client = make_client()
    with override(get_app_service, fake):
        response = client.post(f"/api/apps/{DEV}/{PKG}/{path}")

    assert response.status_code == 200
    assert response.json() == {"success": True, "package": PKG}
    assert fake.calls == [(call_name, DEV, PKG)]


@pytest.mark.parametrize(
    ("path", "attr", "detail_keyword"),
    [
        ("launch", "launch_result", "Failed to launch app"),
        ("stop", "stop_result", "Failed to stop app"),
        ("uninstall", "uninstall_result", "Failed to uninstall app"),
        ("clear-data", "clear_result", "Failed to clear app data"),
    ],
)
def test_actions_failure_500(
    fake: FakeAppService, path: str, attr: str, detail_keyword: str
) -> None:
    """服务返回 False → 500 且错误信息标注操作类型。"""
    setattr(fake, attr, False)
    client = make_client()
    with override(get_app_service, fake):
        response = client.post(f"/api/apps/{DEV}/{PKG}/{path}")

    assert response.status_code == 500
    body = response.json()
    assert body["error"]["code"] == "HTTP_ERROR"
    assert detail_keyword in body["error"]["message"]


# ---------------------------------------------------------------------------
# GET /api/apps/{device_id}/{package}/memory
# ---------------------------------------------------------------------------

def test_get_app_memory_found(fake: FakeAppService) -> None:
    """内存查询命中返回 KB 值。"""
    client = make_client()
    with override(get_app_service, fake):
        response = client.get(f"/api/apps/{DEV}/{PKG}/memory")

    assert response.status_code == 200
    assert response.json() == {"package": PKG, "memory_kb": 4096}
    assert fake.calls == [("get_app_memory", DEV, PKG)]


def test_get_app_memory_not_available_404(fake: FakeAppService) -> None:
    """服务返回 None → 404。"""
    fake.memory_kb = None
    client = make_client()
    with override(get_app_service, fake):
        response = client.get(f"/api/apps/{DEV}/{PKG}/memory")

    assert response.status_code == 404
    assert PKG in response.json()["error"]["message"]


# ---------------------------------------------------------------------------
# GET /api/apps/{device_id}/{package}/runtime
# ---------------------------------------------------------------------------

def test_runtime_endpoint_shape(fake: FakeAppService) -> None:
    """结构含六个键（pkg/top_activity/resumed/services/providers/parse_warning）。"""
    client = make_client()
    with override(get_app_service, fake):
        response = client.get(f"/api/apps/{DEV}/{PKG}/runtime")

    assert response.status_code == 200
    body = response.json()
    for key in ("pkg", "top_activity", "resumed", "services", "providers", "parse_warning"):
        assert key in body
    assert fake.calls == [("get_runtime_info", DEV, PKG)]


def test_runtime_endpoint_dataclass_serialized_to_plain_dict() -> None:
    """真 AppService 透传服务层 dict 时，ServiceInfo dataclass 被序列化为纯 dict。

    锁定接口层归一化语义：C3 服务层 services 字段是 list[ServiceInfo]
    （dataclass）。当前 FastAPI 的 response 编码器可兜底序列化 dataclass
    （移除 jsonable_encoder 在当前版本下仍返回 200 而非 500），apps.py 保留
    jsonable_encoder 是版本前瞻防御——显式归一为纯 JSON 形态，不依赖
    编码器兜底行为。
    """
    adb = MagicMock()
    adb.get_runtime_info = AsyncMock(
        return_value=RuntimeInfo(
            pkg="com.x",
            top_activity="com.x/.Main",
            resumed="com.x/.Main",
            services=[ServiceInfo(name="com.x/.S", state="running")],
            providers=["com.x/.P"],
        )
    )
    client = make_client()
    with override(get_app_service, AppService(adb)):
        response = client.get("/api/apps/d1/com.x/runtime")

    assert response.status_code == 200
    body = response.json()
    assert body["services"] == [{"name": "com.x/.S", "state": "running"}]
    assert body["providers"] == ["com.x/.P"]
    assert body["pkg"] == "com.x"


def test_runtime_endpoint_transparent_passthrough(fake: FakeAppService) -> None:
    """端点逐字段透传服务层 dict（非空 parse_warning、空解析形态）。"""
    fake.runtime_info = {
        "pkg": PKG,
        "top_activity": "",
        "resumed": "",
        "services": [],
        "providers": [],
        "parse_warning": "resumed 缺失; top_activity 缺失",
    }
    client = make_client()
    with override(get_app_service, fake):
        response = client.get(f"/api/apps/{DEV}/{PKG}/runtime")

    assert response.status_code == 200
    assert response.json() == fake.runtime_info
    assert fake.calls == [("get_runtime_info", DEV, PKG)]


def test_runtime_endpoint_invalid_package_400(fake: FakeAppService) -> None:
    """路径包名非法 → 服务抛 InvalidIdentifierError → 400，且不回显原值。"""
    fake.runtime_error = InvalidIdentifierError("package")
    client = make_client()
    with override(get_app_service, fake):
        response = client.get(f"/api/apps/{DEV}/bad-pkg/runtime")

    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "INVALID_IDENTIFIER"
    assert body["error"]["message"] == "Invalid package format"
    assert "bad-pkg" not in body["error"]["message"]
    assert fake.calls == [("get_runtime_info", DEV, "bad-pkg")]
