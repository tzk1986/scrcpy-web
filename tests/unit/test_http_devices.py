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
    - POST /api/devices/pair                   无线调试配对（方案 47 T4：参数校验 400 / 失败分类）
    - POST /api/devices/scan                   CIDR 校验（422）/ 互斥（409）/ 序列化（方案 36）
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
import gzip
import json
from ipaddress import ip_address, ip_network
from typing import Any

import pytest

from app.application.device_service import ConnectAttempt, ScanResult
from app.core.exceptions import (
    AdbError,
    DeviceNotReadyError,
    InvalidScanRangeError,
    PairError,
    ScanBusyError,
)
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


def _hosts(cidr: str) -> list[str]:
    """CIDR 展开为 hosts 列表（数值升序，与接口层行为一致）。"""
    return [str(ip) for ip in ip_network(cidr).hosts()]


def _ip_range(start: str, end: str) -> list[str]:
    """起止 IP（含两端）展开为数值升序列表。"""
    lo, hi = int(ip_address(start)), int(ip_address(end))
    return [str(ip_address(i)) for i in range(lo, hi + 1)]


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
        self.raw_gz = gzip.compress(b"RAW_FRAME_PAYLOAD")
        self.rotation = 0
        self.raw_error: Exception | None = None
        self.new_device_id = "192.168.1.5:5555"
        self.scan_result = ScanResult(
            label="192.168.8.0/24",
            probed=254,
            open_hosts=["192.168.8.18"],
            connect_results=[
                ConnectAttempt(ip="192.168.8.18", ok=True, device_id="192.168.8.18:5555")
            ],
            truncated=False,
        )
        self.scan_error: Exception | None = None
        self.pair_error: Exception | None = None
        self.connect_error: Exception | None = None
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

    async def screenshot_raw_gzip(self, device_id: str) -> bytes:
        self.calls.append(("screenshot_raw_gzip", device_id))
        if self.raw_error is not None:
            raise self.raw_error
        return self.raw_gz

    async def get_rotation(self, device_id: str) -> int:
        self.calls.append(("get_rotation", device_id))
        return self.rotation

    async def connect_tcp(self, ip: str, port: int = 5555) -> str:
        self.calls.append(("connect_tcp", ip, port))
        if self.connect_error is not None:
            raise self.connect_error
        return self.new_device_id

    async def pair_tcp(self, ip: str, port: int, code: str) -> None:
        self.calls.append(("pair_tcp", ip, port, code))
        if self.pair_error is not None:
            raise self.pair_error

    async def scan_and_connect(
        self, label: str, targets: list[str], connect: bool = True, port: int = 5555
    ) -> ScanResult:
        self.calls.append(("scan_and_connect", label, targets, connect, port))
        if self.scan_error is not None:
            raise self.scan_error
        return self.scan_result

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
        "slow": False,
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
    """截图返回 PNG 二进制与 Content-Type，并携带 X-Rotation（方案 38 B6）。"""
    client = make_client()
    with override(get_device_service, fake):
        response = client.get(f"/api/devices/{DEV}/screenshot")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == fake.png
    assert response.headers["x-rotation"] == "0"
    assert fake.calls == [("get_rotation", DEV), ("screenshot", DEV)]


def test_screenshot_png_rotation_header(fake: FakeDeviceService) -> None:
    """rotation=2 设备 PNG 截图头标注 X-Rotation: 2。"""
    fake.rotation = 2
    client = make_client()
    with override(get_device_service, fake):
        response = client.get(f"/api/devices/{DEV}/screenshot")

    assert response.status_code == 200
    assert response.headers["x-rotation"] == "2"


def test_screenshot_raw(fake: FakeDeviceService) -> None:
    """format=raw 原样透传 gzip 字节 + gzip/格式/rotation 响应头（方案 29/38）。

    注意：httpx（与浏览器同行为）对 Content-Encoding: gzip 响应透明解压，
    response.content 为解压后的原始字节——这正是前端依赖的传输层行为。
    """
    client = make_client()
    with override(get_device_service, fake):
        response = client.get(f"/api/devices/{DEV}/screenshot", params={"format": "raw"})

    assert response.status_code == 200
    assert response.content == gzip.decompress(fake.raw_gz)
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-encoding"] == "gzip"
    assert response.headers["x-frame-format"] == "raw-rgba"
    assert response.headers["x-rotation"] == "0"
    assert fake.calls == [("get_rotation", DEV), ("screenshot_raw_gzip", DEV)]


def test_screenshot_raw_falls_back_to_png(fake: FakeDeviceService) -> None:
    """raw 链路失败 → 单请求内回退 PNG 且标注 X-Frame-Format: png（含 X-Rotation）。"""
    fake.raw_error = AdbError("sh: gzip: not found")
    fake.rotation = 3
    client = make_client()
    with override(get_device_service, fake):
        response = client.get(f"/api/devices/{DEV}/screenshot", params={"format": "raw"})

    assert response.status_code == 200
    assert response.content == fake.png
    assert response.headers["content-type"] == "image/png"
    assert response.headers["x-frame-format"] == "png"
    assert response.headers["x-rotation"] == "3"
    assert fake.calls == [("get_rotation", DEV), ("screenshot_raw_gzip", DEV), ("screenshot", DEV)]


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


def test_connect_device_not_ready_passes_400(fake: FakeDeviceService) -> None:
    """假成功守卫（路线图 15）：DeviceNotReadyError → 400 + 中文指引原样回显。"""
    fake.connect_error = DeviceNotReadyError.classify("192.168.8.25:5555", "offline")
    client = make_client()
    with override(get_device_service, fake):
        response = client.post("/api/devices/connect", params={"ip": "192.168.8.25"})

    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "DEVICE_NOT_READY"
    assert "offline" in body["error"]["message"]


# ---------------------------------------------------------------------------
# 无线调试配对（方案 47 T4）
# ---------------------------------------------------------------------------

def test_pair_device_success(fake: FakeDeviceService) -> None:
    """合法参数 → service.pair_tcp 透传，回显 paired 目标。"""
    client = make_client()
    with override(get_device_service, fake):
        response = client.post(
            "/api/devices/pair", params={"ip": "192.168.1.5", "port": 37123, "code": "123456"}
        )

    assert response.status_code == 200
    assert response.json() == {"success": True, "paired": "192.168.1.5:37123"}
    assert fake.calls == [("pair_tcp", "192.168.1.5", 37123, "123456")]


def test_pair_device_leading_zero_code_preserved(fake: FakeDeviceService) -> None:
    """code 以字符串接参：前导零不得丢（Review Focus 1）。"""
    client = make_client()
    with override(get_device_service, fake):
        response = client.post(
            "/api/devices/pair", params={"ip": "192.168.1.5", "port": 37123, "code": "012345"}
        )

    assert response.status_code == 200
    assert fake.calls == [("pair_tcp", "192.168.1.5", 37123, "012345")]


@pytest.mark.parametrize(
    "params",
    [
        {"ip": "192.168.1.5", "port": 37123, "code": "12345"},  # 不足 6 位
        {"ip": "192.168.1.5", "port": 37123, "code": "1234567"},  # 超过 6 位
        {"ip": "192.168.1.5", "port": 37123, "code": "abc123"},  # 非数字
        {"ip": "192.168.1.5", "port": 37123, "code": "１２３４５６"},  # 全角数字不受理
        {"ip": "not-an-ip", "port": 37123, "code": "123456"},  # 非法 IP
        {"ip": "::1", "port": 37123, "code": "123456"},  # 仅受理 IPv4
        {"ip": "192.168.1.5", "port": 0, "code": "123456"},  # 端口越下界
        {"ip": "192.168.1.5", "port": 70000, "code": "123456"},  # 端口越上界
    ],
)
def test_pair_device_invalid_request_400(fake: FakeDeviceService, params: dict[str, Any]) -> None:
    """参数校验失败 → 400 INVALID_PAIR_REQUEST（非 500 兜底），不触达 service。"""
    client = make_client()
    with override(get_device_service, fake):
        response = client.post("/api/devices/pair", params=params)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_PAIR_REQUEST"
    assert fake.calls == []


def test_pair_device_failure_maps_structured_400(fake: FakeDeviceService) -> None:
    """service 抛 PairError → 400 PAIR_FAILED 结构化中文文案。"""
    fake.pair_error = PairError("配对失败：配对码错误或已过期")
    client = make_client()
    with override(get_device_service, fake):
        response = client.post(
            "/api/devices/pair", params={"ip": "192.168.1.5", "port": 37123, "code": "123456"}
        )

    assert response.status_code == 400
    body = response.json()["error"]
    assert body["code"] == "PAIR_FAILED"
    assert body["message"] == "配对失败：配对码错误或已过期"


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
# 网段扫描（方案 36）
# ---------------------------------------------------------------------------

def test_scan_ok_serializes_scan_result(fake: FakeDeviceService) -> None:
    """/24 合法 + 默认参数：ScanResult 整体序列化为 JSON，targets 已展开。"""
    client = make_client()
    with override(get_device_service, fake):
        response = client.post("/api/devices/scan", params={"cidr": "192.168.8.0/24"})

    assert response.status_code == 200
    assert response.json() == {
        "label": "192.168.8.0/24",
        "probed": 254,
        "open_hosts": ["192.168.8.18"],
        "connect_results": [
            {
                "ip": "192.168.8.18",
                "ok": True,
                "device_id": "192.168.8.18:5555",
                "reason": None,
                "message": None,
            }
        ],
        "truncated": False,
    }
    assert fake.calls == [
        ("scan_and_connect", "192.168.8.0/24", _hosts("192.168.8.0/24"), True, 5555)
    ]


def test_scan_passes_query_params(fake: FakeDeviceService) -> None:
    """connect=false 与 port 经查询串透传。"""
    client = make_client()
    with override(get_device_service, fake):
        response = client.post(
            "/api/devices/scan",
            params={"cidr": "192.168.8.0/28", "connect": "false", "port": 5556},
        )

    assert response.status_code == 200
    assert fake.calls == [
        ("scan_and_connect", "192.168.8.0/28", _hosts("192.168.8.0/28"), False, 5556)
    ]


def test_scan_normalizes_host_bits(fake: FakeDeviceService) -> None:
    """strict=False：host 位非零归一化后展开 targets；label 保留原输入（D11）。"""
    client = make_client()
    with override(get_device_service, fake):
        response = client.post("/api/devices/scan", params={"cidr": "192.168.8.5/24"})

    assert response.status_code == 200
    assert fake.calls == [
        ("scan_and_connect", "192.168.8.5/24", _hosts("192.168.8.0/24"), True, 5555)
    ]


def test_scan_range_param_expands_targets(fake: FakeDeviceService) -> None:
    """区间形态：label 原样透传，targets 按起止展开（数值升序，D11）。"""
    client = make_client()
    with override(get_device_service, fake):
        response = client.post(
            "/api/devices/scan", params={"cidr": "192.168.8.10-20", "connect": "false"}
        )

    assert response.status_code == 200
    assert fake.calls == [
        (
            "scan_and_connect",
            "192.168.8.10-20",
            _ip_range("192.168.8.10", "192.168.8.20"),
            False,
            5555,
        )
    ]


def test_scan_single_ip_param(fake: FakeDeviceService) -> None:
    """单 IP 形态：仅探测该主机（targets 单元素，D11）。"""
    client = make_client()
    with override(get_device_service, fake):
        response = client.post("/api/devices/scan", params={"cidr": "192.168.8.18"})

    assert response.status_code == 200
    assert fake.calls == [
        ("scan_and_connect", "192.168.8.18", ["192.168.8.18"], True, 5555)
    ]


@pytest.mark.parametrize(
    "cidr",
    [
        "not-a-cidr",       # 无法解析
        "192.168.8.0/21",   # 超上限（>1022 台）
        "192.168.8.0/8",    # 远超上限
        "::/64",            # IPv6 不支持
        "192.168.8.0/33",   # 前缀非法
        "192.168.8.100-10",           # 区间倒序（D11）
        "192.168.8.10-256",           # 简写末段超 255（D11）
        "192.168.8.10-010",           # 简写右端前导零（D11）
        "192.168.8.0/24-192.168.9.1", # / 与 - 混写（D11）
        "192.168.8.1-192.168.12.16",  # 区间总数 1040 台超上限（D11）
    ],
)
def test_scan_invalid_cidr_422(fake: FakeDeviceService, cidr: str) -> None:
    """非法/超上限目标（CIDR 或区间形态）→ 422 INVALID_SCAN_RANGE，不触达 service。"""
    client = make_client()
    with override(get_device_service, fake):
        response = client.post("/api/devices/scan", params={"cidr": cidr})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_SCAN_RANGE"
    assert fake.calls == []


@pytest.mark.parametrize("port", [0, -1, 65536, 99999])
def test_scan_invalid_port_422(fake: FakeDeviceService, port: int) -> None:
    """越界端口 → 422 FastAPI 校验拦截，不触达探测层（终评 I-2）。"""
    client = make_client()
    with override(get_device_service, fake):
        response = client.post(
            "/api/devices/scan", params={"cidr": "192.168.8.0/24", "port": port}
        )

    assert response.status_code == 422
    assert fake.calls == []


def test_scan_busy_409(fake: FakeDeviceService) -> None:
    """扫描互斥：ScanBusyError → 409 SCAN_BUSY + 中文 message。"""
    fake.scan_error = ScanBusyError()
    client = make_client()
    with override(get_device_service, fake):
        response = client.post("/api/devices/scan", params={"cidr": "192.168.8.0/24"})

    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == "SCAN_BUSY"
    assert body["error"]["message"]


# ---------------------------------------------------------------------------
# _parse_scan_targets 三形态（方案 36 增补 D11）
# ---------------------------------------------------------------------------

def test_parse_targets_cidr_keeps_label_and_expands() -> None:
    """CIDR 形态：label 取原输入，targets 按归一化网段展开为数值升序列表。"""
    label, targets = devices_module._parse_scan_targets("192.168.8.0/24")
    assert label == "192.168.8.0/24"
    assert targets == _hosts("192.168.8.0/24")
    assert len(targets) == 254


def test_parse_targets_cidr_host_bits_labels_original() -> None:
    """host 位非零：展开用归一化网段，label 保留原输入（strip 后）。"""
    label, targets = devices_module._parse_scan_targets(" 192.168.8.5/24 ")
    assert label == "192.168.8.5/24"
    assert targets == _hosts("192.168.8.0/24")


def test_parse_targets_single_ip() -> None:
    """单 IP：targets 单元素。"""
    label, targets = devices_module._parse_scan_targets("192.168.8.18")
    assert label == "192.168.8.18"
    assert targets == ["192.168.8.18"]


def test_parse_targets_short_range() -> None:
    """简写区间：右端为末段数字，前缀取自左端同段。"""
    label, targets = devices_module._parse_scan_targets("192.168.8.10-100")
    assert label == "192.168.8.10-100"
    assert targets == _ip_range("192.168.8.10", "192.168.8.100")
    assert len(targets) == 91


def test_parse_targets_full_range_crosses_subnet() -> None:
    """完整区间：跨网段也支持，仍数值升序。"""
    label, targets = devices_module._parse_scan_targets("192.168.8.250-192.168.9.10")
    assert label == "192.168.8.250-192.168.9.10"
    assert targets == _ip_range("192.168.8.250", "192.168.9.10")
    assert len(targets) == 17


def test_parse_targets_single_ip_range_degenerate() -> None:
    """首尾相同的简写区间：退化为单机探测。"""
    label, targets = devices_module._parse_scan_targets("192.168.8.18-18")
    assert targets == ["192.168.8.18"]


@pytest.mark.parametrize(
    "raw",
    [
        "",                             # 空输入
        "not-an-ip",                    # 无法解析
        "192.168.8.100-10",             # 倒序区间
        "192.168.8.10-256",             # 简写末段超 255
        "192.168.8.010-100",            # 左端八位组前导零
        "192.168.8.10-010",             # 简写右端前导零（防 int("010") 静默通过）
        "192.168.8.10-",                # 右端缺失
        "192.168.8.1-192.168.12.16",    # 总数 1040 台超上限 1022
        "192.168.8.0/24-192.168.9.1",   # / 与 - 混写
        "192.168.8.0/21",               # 前缀超下限
        "::1-::5",                      # IPv6 区间
        "192.168.8.18/33",              # 前缀非法
    ],
)
def test_parse_targets_invalid(raw: str) -> None:
    """非法/超上限输入 → InvalidScanRangeError（D11）。"""
    with pytest.raises(InvalidScanRangeError):
        devices_module._parse_scan_targets(raw)


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


async def test_events_stream_terminates_on_shutdown_requested(
    fake: FakeDeviceService,
) -> None:
    """停机请求后 SSE 生成器优雅收尾（方案 23 T4 R2 缺陷回归）。

    uvicorn 优雅停机会等待在途响应结束：无限 SSE 不自行返回则停机挂死
    （打包复测实证 "Waiting for connections to close" 永不完成）。置位
    停机标志后，生成器应在 get 空闲超时内 return，且回调照常清理。
    """
    from app.core import launcher

    launcher.register_shutdown_handler(lambda: None)
    try:
        response = await devices_module.device_events(fake)  # type: ignore[arg-type]
        stream = response.body_iterator
        assert len(fake._on_device_connected) == 1

        assert launcher.request_shutdown() is True
        with pytest.raises(StopAsyncIteration):
            # wait_for 兜底：回归时生成器不返回则测试挂死蔓延全仓，须有界失败
            await asyncio.wait_for(stream.__anext__(), timeout=2.0)
        assert fake._on_device_connected == []
        assert fake._on_device_disconnected == []
    finally:
        launcher.register_shutdown_handler(None)


async def test_events_stream_keeps_running_without_shutdown(
    fake: FakeDeviceService,
) -> None:
    """未请求停机时生成器正常推事件（防过度修复：只收停机信号）。"""
    from app.core import launcher

    launcher.register_shutdown_handler(None)  # 确保停机标志为 False
    response = await devices_module.device_events(fake)  # type: ignore[arg-type]
    stream = response.body_iterator
    await fake._on_device_connected[0](make_device())
    line = await asyncio.wait_for(stream.__anext__(), timeout=1.0)
    assert line.startswith("data: ")
    assert json.loads(line.removeprefix("data: ").strip())["type"] == "connected"