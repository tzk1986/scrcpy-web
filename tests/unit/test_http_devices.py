"""
设备管理 HTTP 端点（backend/app/interfaces/http/devices.py）行为测试
====================================================================

用 hand-rolled FakeDeviceService 经 dependency_overrides 注入，覆盖：
    - GET  /api/devices                        列表（含空列表）
    - GET  /api/devices/{id}                   命中 / 未找到
    - POST /api/devices/{id}/install           安装
    - POST /api/devices/batch/install          批量安装（字面路径不被 {device_id} 吞掉）
    - GET  /api/devices/{id}/screenshot        截图二进制 + Content-Type
    - POST /api/devices/connect                ip/port 透传 + 默认端口
    - POST /api/devices/{id}/disconnect        "ip:port" 解析 / 无冒号不过滤
    - GET  /api/devices/events                 SSE 响应头、事件负载、断连回调清理

SSE 端点不通过 HTTP 层测试：本环境 starlette TestClient / httpx ASGITransport
都要等 ASGI app 运行结束才返回响应，而 SSE 是无限流（等 queue.get()），
HTTP 请求会永久挂起。因此改为：
    - 直调端点函数：断言 StreamingResponse 的 media_type/headers、
      驱动异步生成器验证事件负载、athrow(CancelledError) 触发回调清理；
    - 路由表顺序守卫：字面路径路由必须注册在 /{device_id} 参数路由之前。

不触碰真实 ADB。
"""

import asyncio
import json
from typing import Any

import pytest

from app.deps import get_device_service
from app.domain.device import DeviceInfo
from app.interfaces.http import devices as devices_module

from .http_testkit import make_client, override

DEV = "emulator-5554"


def make_device(device_id: str = DEV, **overrides: Any) -> DeviceInfo:
    """构造 DeviceInfo，未指定字段取默认值。"""
    fields: dict[str, Any] = {
        "id": device_id,
        "model": "Pixel 6",
        "os_version": "13",
        "resolution": (1080, 2400),
        "battery": 88,
        "status": "online",
    }
    fields.update(overrides)
    return DeviceInfo(**fields)


class FakeDeviceService:
    """
    替身。

    注意：SSE 端点的取消清理分支直接访问 service._on_device_connected /
    _on_device_disconnected（与 DeviceService 私有属性同名），故此处保持同名。
    """

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.devices: list[DeviceInfo] = []
        self.device: DeviceInfo | None = None
        self.install_result = "Success"
        self.batch_results: list[str] = []
        self.png = b"\x89PNG\r\n\x1a\nfakepng"
        self.new_device_id = "192.168.1.5:5555"
        self._on_device_connected: list[Any] = []
        self._on_device_disconnected: list[Any] = []

    async def list_devices(self) -> list[DeviceInfo]:
        self.calls.append(("list_devices",))
        return self.devices

    async def get_device(self, device_id: str) -> DeviceInfo | None:
        self.calls.append(("get_device", device_id))
        return self.device

    async def install_apk(self, device_id: str, apk_path: str) -> str:
        self.calls.append(("install_apk", device_id, apk_path))
        return self.install_result

    async def batch_install(self, device_ids: list[str], apk_path: str) -> list[str]:
        self.calls.append(("batch_install", tuple(device_ids), apk_path))
        return self.batch_results

    async def screenshot(self, device_id: str) -> bytes:
        self.calls.append(("screenshot", device_id))
        return self.png

    async def connect_tcp(self, ip: str, port: int = 5555) -> str:
        self.calls.append(("connect_tcp", ip, port))
        return self.new_device_id

    async def disconnect_tcp(self, ip: str, port: int = 5555) -> None:
        self.calls.append(("disconnect_tcp", ip, port))

    def on_device_connected(self, callback: Any) -> None:
        self._on_device_connected.append(callback)

    def on_device_disconnected(self, callback: Any) -> None:
        self._on_device_disconnected.append(callback)


@pytest.fixture
def fake() -> FakeDeviceService:
    return FakeDeviceService()


# ---------------------------------------------------------------------------
# 设备查询
# ---------------------------------------------------------------------------

def test_list_devices(fake: FakeDeviceService) -> None:
    """设备列表按 DeviceInfo 字段序列化。"""
    fake.devices = [make_device(), make_device("192.168.1.5:5555", battery=50, status="offline")]
    client = make_client()
    with override(get_device_service, fake):
        response = client.get("/api/devices")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2
    assert body[0] == {
        "id": DEV,
        "model": "Pixel 6",
        "os_version": "13",
        "resolution": [1080, 2400],
        "battery": 88,
        "status": "online",
        "ip": None,
        "port": None,
    }
    assert body[1]["status"] == "offline"


def test_list_devices_empty(fake: FakeDeviceService) -> None:
    """无设备时返回空数组。"""
    client = make_client()
    with override(get_device_service, fake):
        response = client.get("/api/devices")

    assert response.status_code == 200
    assert response.json() == []


def test_get_device_found(fake: FakeDeviceService) -> None:
    """设备存在返回详情。"""
    fake.device = make_device()
    client = make_client()
    with override(get_device_service, fake):
        response = client.get(f"/api/devices/{DEV}")

    assert response.status_code == 200
    assert response.json()["id"] == DEV
    assert fake.calls == [("get_device", DEV)]


def test_get_device_not_found(fake: FakeDeviceService) -> None:
    """设备不存在返回 404 + 结构化错误体（统一契约，2026-09-21 由 200+字符串改）。"""
    client = make_client()
    with override(get_device_service, fake):
        response = client.get(f"/api/devices/{DEV}")

    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "DEVICE_NOT_FOUND", "message": f"Device not found: {DEV}"}
    }


def test_validation_error_unified_shape(fake: FakeDeviceService) -> None:
    """参数校验错误（缺必需查询参数）返回 422 且为 {"error": {...}} 统一形状。"""
    client = make_client()
    with override(get_device_service, fake):
        response = client.post("/api/devices/connect")  # 缺 ip

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert "ip" in body["error"]["message"]
    assert "detail" not in body


# ---------------------------------------------------------------------------
# 安装 / 截图
# ---------------------------------------------------------------------------

def test_install_apk(fake: FakeDeviceService) -> None:
    """安装透传 apk_path 并返回结果。"""
    client = make_client()
    with override(get_device_service, fake):
        response = client.post(f"/api/devices/{DEV}/install", params={"apk_path": "/tmp/app.apk"})

    assert response.status_code == 200
    assert response.json() == {"success": True, "result": "Success"}
    assert fake.calls == [("install_apk", DEV, "/tmp/app.apk")]


def test_batch_install(fake: FakeDeviceService) -> None:
    """批量安装：device_ids 请求体 + apk_path 查询参数透传。"""
    fake.batch_results = ["d1: success", "d2: failed - offline"]
    client = make_client()
    with override(get_device_service, fake):
        response = client.post(
            "/api/devices/batch/install",
            params={"apk_path": "/tmp/app.apk"},
            json=["d1", "d2"],
        )

    assert response.status_code == 200
    assert response.json() == {"success": True, "results": fake.batch_results}
    assert fake.calls == [("batch_install", ("d1", "d2"), "/tmp/app.apk")]


def test_screenshot(fake: FakeDeviceService) -> None:
    """截图返回 PNG 二进制与 Content-Type。"""
    client = make_client()
    with override(get_device_service, fake):
        response = client.get(f"/api/devices/{DEV}/screenshot")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == fake.png
    assert fake.calls == [("screenshot", DEV)]


# ---------------------------------------------------------------------------
# 连接 / 断开
# ---------------------------------------------------------------------------

def test_connect_device_with_explicit_port(fake: FakeDeviceService) -> None:
    """connect 透传 ip/port 并回显 device_id。"""
    client = make_client()
    with override(get_device_service, fake):
        response = client.post(
            "/api/devices/connect", params={"ip": "192.168.1.5", "port": 5556}
        )

    assert response.status_code == 200
    assert response.json() == {"success": True, "device_id": "192.168.1.5:5555"}
    assert fake.calls == [("connect_tcp", "192.168.1.5", 5556)]


def test_connect_device_default_port(fake: FakeDeviceService) -> None:
    """未传 port 时默认 5555。"""
    client = make_client()
    with override(get_device_service, fake):
        response = client.post("/api/devices/connect", params={"ip": "192.168.1.5"})

    assert response.status_code == 200
    assert fake.calls == [("connect_tcp", "192.168.1.5", 5555)]


def test_disconnect_device_with_port(fake: FakeDeviceService) -> None:
    """device_id 形如 "ip:port" → 解析后调用 disconnect_tcp。"""
    client = make_client()
    with override(get_device_service, fake):
        response = client.post("/api/devices/192.168.1.5:5555/disconnect")

    assert response.status_code == 200
    assert response.json() == {"success": True}
    assert fake.calls == [("disconnect_tcp", "192.168.1.5", 5555)]


def test_disconnect_device_without_port(fake: FakeDeviceService) -> None:
    """device_id 无冒号（USB 序列号）→ 不调用 disconnect_tcp，仍返回 success。"""
    client = make_client()
    with override(get_device_service, fake):
        response = client.post(f"/api/devices/{DEV}/disconnect")

    assert response.status_code == 200
    assert response.json() == {"success": True}
    assert fake.calls == []


# ---------------------------------------------------------------------------
# SSE 事件流
# ---------------------------------------------------------------------------

async def test_events_sse_over_asgi_not_shadowed(fake: FakeDeviceService) -> None:
    """ASGI 层请求 /api/devices/events：路由命中 SSE 端点（而非被 /{device_id}
    吞成 JSON），响应头正确且回调已注册；随后取消任务回收请求。"""
    from app.main import app

    messages: list[dict[str, Any]] = []
    started = asyncio.Event()
    receive_count = 0

    async def receive() -> dict[str, Any]:
        nonlocal receive_count
        receive_count += 1
        if receive_count == 1:
            return {"type": "http.request", "body": b"", "more_body": False}
        await asyncio.sleep(3600)  # 无请求体：挂起直到任务被取消
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)
        if message["type"] == "http.response.start":
            started.set()

    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/api/devices/events",
        "raw_path": b"/api/devices/events",
        "query_string": b"",
        "root_path": "",
        "headers": [],
        "server": ("testserver", 80),
        "client": ("testclient", 123),
    }

    with override(get_device_service, fake):
        task = asyncio.create_task(app(scope, receive, send))  # type: ignore[arg-type]
        try:
            await asyncio.wait_for(started.wait(), timeout=5)
            assert len(fake._on_device_connected) == 1
            assert len(fake._on_device_disconnected) == 1
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    assert messages[0]["type"] == "http.response.start"
    assert messages[0]["status"] == 200
    headers = {k.decode().lower(): v.decode() for k, v in messages[0]["headers"]}
    assert headers["content-type"].startswith("text/event-stream")
    assert headers["cache-control"] == "no-cache"
    assert headers["x-accel-buffering"] == "no"


async def test_events_stream_payload_and_cancel_cleanup(fake: FakeDeviceService) -> None:
    """直调端点：响应头、事件负载序列化；athrow(CancelledError) 触发回调清理。"""
    response = await devices_module.device_events(fake)  # type: ignore[arg-type]
    assert response.media_type == "text/event-stream"
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["connection"] == "keep-alive"
    assert response.headers["x-accel-buffering"] == "no"

    stream = response.body_iterator
    assert len(fake._on_device_connected) == 1
    assert len(fake._on_device_disconnected) == 1

    # 设备连接事件
    await fake._on_device_connected[0](make_device())
    line = await stream.__anext__()
    assert line.endswith("\n\n")
    payload = json.loads(line.removeprefix("data: ").strip())
    assert payload == {
        "type": "connected",
        "device": {
            "id": DEV,
            "model": "Pixel 6",
            "os_version": "13",
            "resolution": [1080, 2400],
            "battery": 88,
            "status": "online",
        },
    }

    # 设备断开事件
    await fake._on_device_disconnected[0]("192.168.1.5:5555")
    line = await stream.__anext__()
    assert json.loads(line.removeprefix("data: ").strip()) == {
        "type": "disconnected",
        "device_id": "192.168.1.5:5555",
    }

    # 客户端断连：CancelledError 进入生成器 → 清理回调后重抛
    with pytest.raises(asyncio.CancelledError):
        await stream.athrow(asyncio.CancelledError())
    assert fake._on_device_connected == []
    assert fake._on_device_disconnected == []