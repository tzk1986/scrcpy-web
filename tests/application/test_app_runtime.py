"""
应用运行时查询容错测试（方案 37 C3）
====================================

测试 AppService.get_runtime_info（backend/app/application/app_service.py）：

    - 返回前端直用 dict：
        * pkg 键 = 服务方法参数 package（非 RuntimeInfo.pkg 解析值）
        * top_activity / resumed / services / providers 原样透传
    - 容错：
        * top_activity / resumed 为空串 → 逐项追加 parse_warning（缺失累加）
        * services / providers 为空属合法空，不计 warning
        * 全字段齐 → parse_warning == ""
        * AdbError → 收敛空形态 dict + 非空 parse_warning（不抛）

ADB 替身使用 MagicMock + AsyncMock：服务内 `await self.adb.get_runtime_info(...)`
要求该属性为协程，裸 MagicMock 属性（普通返回值）不可 await 会 TypeError。
"""

from unittest.mock import AsyncMock, MagicMock

from app.application.app_service import AppService
from app.core.exceptions import AdbError
from app.domain.runtime import RuntimeInfo, ServiceInfo

DEVICE = "192.168.1.100"


def _service_with(info: RuntimeInfo) -> AppService:
    """构造返回预设 RuntimeInfo 的 AppService（AsyncMock 替身）。"""
    adb = MagicMock()
    adb.get_runtime_info = AsyncMock(return_value=info)
    return AppService(adb)


async def test_runtime_returns_fields_with_parse_warning_on_missing():
    info = RuntimeInfo(
        pkg="x", top_activity="com.a/.B", resumed="", services=[], providers=[]
    )
    out = await _service_with(info).get_runtime_info(DEVICE, "com.x")
    assert out["top_activity"] == "com.a/.B"
    assert out["resumed"] == ""
    assert out["services"] == []
    assert out["providers"] == []
    assert "parse_warning" in out
    assert out["parse_warning"] != ""
    assert "resumed" in out["parse_warning"]


async def test_runtime_adberror_converges_empty_with_warning():
    adb = MagicMock()
    adb.get_runtime_info = AsyncMock(side_effect=AdbError("dumpsys failed"))
    out = await AppService(adb).get_runtime_info(DEVICE, "com.x")
    assert out["pkg"] == "com.x"
    assert out["top_activity"] == ""
    assert out["resumed"] == ""
    assert out["services"] == []
    assert out["providers"] == []
    assert out["parse_warning"] != ""
    assert "ADB 查询失败" in out["parse_warning"]


async def test_runtime_top_activity_missing_accumulates_warning():
    info = RuntimeInfo(
        pkg="x", top_activity="", resumed="com.a/.B", services=[], providers=[]
    )
    out = await _service_with(info).get_runtime_info(DEVICE, "com.x")
    assert "top_activity" in out["parse_warning"]
    assert "resumed" not in out["parse_warning"]


async def test_runtime_both_missing_accumulates_two_warnings():
    info = RuntimeInfo(pkg="x", top_activity="", resumed="", services=[], providers=[])
    out = await _service_with(info).get_runtime_info(DEVICE, "com.x")
    assert "resumed" in out["parse_warning"]
    assert "top_activity" in out["parse_warning"]


async def test_runtime_all_fields_clean_no_warning():
    info = RuntimeInfo(
        pkg="x",
        top_activity="com.a/.B",
        resumed="com.a/.B",
        services=[ServiceInfo(name="com.a/.S", state="running")],
        providers=["com.a/.P"],
    )
    out = await _service_with(info).get_runtime_info(DEVICE, "com.x")
    assert out["parse_warning"] == ""


async def test_runtime_pkg_key_uses_parameter():
    info = RuntimeInfo(
        pkg="com.y", top_activity="com.y/.B", resumed="com.y/.B", services=[], providers=[]
    )
    out = await _service_with(info).get_runtime_info(DEVICE, "com.x")
    assert out["pkg"] == "com.x"


async def test_runtime_services_providers_passthrough():
    services = [ServiceInfo(name="com.a/.S", state="running")]
    providers = ["com.a/.P"]
    info = RuntimeInfo(
        pkg="x",
        top_activity="com.a/.B",
        resumed="com.a/.B",
        services=services,
        providers=providers,
    )
    out = await _service_with(info).get_runtime_info(DEVICE, "com.x")
    assert out["services"] == services
    assert out["providers"] == providers


async def test_runtime_empty_services_providers_no_warning():
    """空 services/providers 清单是合法空，不产生 parse_warning（与 resumed 缺失分离）。"""
    info = RuntimeInfo(
        pkg="x", top_activity="com.a/.B", resumed="com.a/.B", services=[], providers=[]
    )
    out = await _service_with(info).get_runtime_info(DEVICE, "com.x")
    assert out["services"] == []
    assert out["providers"] == []
    assert out["parse_warning"] == ""


async def test_runtime_adberror_warning_truncated_to_200_chars():
    """AdbError 摘要 parse_warning 截断 200 字符（防超长错误文本撑大响应体）。"""
    adb = MagicMock()
    adb.get_runtime_info = AsyncMock(side_effect=AdbError("x" * 500))
    out = await AppService(adb).get_runtime_info(DEVICE, "com.x")
    assert len(out["parse_warning"]) == 200
    assert out["parse_warning"].startswith("ADB 查询失败: ")
    assert out["parse_warning"].endswith("xx")
