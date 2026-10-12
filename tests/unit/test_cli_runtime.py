# tests/unit/test_cli_runtime.py
"""
AdbCliDriver dumpsys activity 三态解析测试（方案 37 C2）
======================================================

以 C1 三真机原始样本（tests/application/fixtures）为 fixture，覆盖：

    - parse_activity_runtime：三真机样本 top/resumed 正则锚定解析 +
      漂移样本三字段全空不抛
    - parse_services：真实样本 ServiceRecord 清单（主输出内嵌块与
      追加段双来源并存、重复行照单全收）
    - parse_providers：真实样本 Provider 清单 + 无 providers 段文本空清单
    - get_runtime_info：三命令并发 gather、providers 失败静默为 []、
      activity/services 失败冒泡 AdbError
"""

from pathlib import Path

import pytest

from app.core.exceptions import AdbError
from app.domain.runtime import ServiceInfo
from app.infrastructure.adb.cli import (
    AdbCliDriver,
    parse_activity_runtime,
    parse_providers,
    parse_services,
)

FIX = Path(__file__).parent.parent / "application" / "fixtures"


def test_parse_activity_18_real_sample():
    info = parse_activity_runtime((FIX / "dumpsys_activity_18.txt").read_text(encoding="utf-8"))
    assert info.resumed == "com.duzkj.facecash/.module.orderMeal.MainActivity"
    assert info.top_activity == "com.duzkj.facecash/.module.orderMeal.MainActivity"
    assert info.pkg == "com.duzkj.facecash"


def test_parse_activity_25_real_sample():
    info = parse_activity_runtime((FIX / "dumpsys_activity_25.txt").read_text(encoding="utf-8"))
    assert info.resumed == "com.android.settings/.SubSettings"
    assert info.top_activity == "com.android.settings/.SubSettings"
    assert info.pkg == "com.android.settings"


def test_parse_activity_19_real_sample():
    info = parse_activity_runtime((FIX / "dumpsys_activity_19.txt").read_text(encoding="utf-8"))
    assert info.resumed == "com.epfly.takefoodtank/.page.activity.MainActivity"
    assert info.top_activity == "com.epfly.takefoodtank/.page.activity.MainActivity"
    assert info.pkg == "com.epfly.takefoodtank"


def test_parse_activity_drift_never_raises():
    info = parse_activity_runtime((FIX / "dumpsys_activity_drift.txt").read_text(encoding="utf-8"))
    assert info.resumed == "" and info.top_activity == "" and info.pkg == ""
    assert info.services == [] and info.providers == []


def test_parse_services_real_sample():
    services = parse_services((FIX / "dumpsys_activity_19.txt").read_text(encoding="utf-8"))
    assert services
    # 样本实测锚点（app= 行驱动状态）：ImeService 进程承载=running，
    # HidService app=null=stopped；双来源并存，重复行照单全收
    assert ServiceInfo("com.baidu.input/.ImeService", "running") in services
    assert ServiceInfo("com.android.bluetooth/.hid.HidService", "stopped") in services
    # 每条都有非空组件名（pkg/class 形态）与状态字段
    assert all(s.name and "/" in s.name for s in services)
    assert all(s.state in {"running", "stopped", "unknown"} for s in services)


def test_parse_providers_real_sample():
    providers = parse_providers((FIX / "dumpsys_activity_18.txt").read_text(encoding="utf-8"))
    assert providers
    assert "com.android.providers.telephony/.TelephonyProvider" in providers
    assert all(p and "/" in p for p in providers)


def test_parse_providers_empty_on_missing():
    providers = parse_providers("ACTIVITY MANAGER (dumpsys activity)\n  已连接设备正常")
    assert providers == []


# --- get_runtime_info：三命令并发 + 容错分支 ---

_ACTIVITY_OUT = (
    "    mResumedActivity: ActivityRecord{1a2b3c4 u0 com.demo.app/.MainActivity t1}\n"
    "  mFocusedActivity: ActivityRecord{1a2b3c4 u0 com.demo.app/.MainActivity t1}\n"
)
_SERVICES_OUT = (
    "  * ServiceRecord{5e6f7a8 u0 com.demo.app/.BgService}\n"
    "    app=ProcessRecord{1 999:com.demo.app/u0a1}\n"
)
_PROVIDERS_OUT = "  * ContentProviderRecord{9a8b7c6 u0 com.demo.app/.DemoProvider}\n"


def _make_fake_run(outputs):
    """构造 _run_serial 替身：按 args[-1] 分发三命令；异常对象直接抛出，calls 记录参数。"""
    calls = []

    async def fake_run(adr, *args, timeout=None):
        calls.append((args, timeout))
        result = outputs[args[-1]]
        if isinstance(result, Exception):
            raise result
        return result

    return fake_run, calls


async def test_get_runtime_info_gathers_three_commands(monkeypatch):
    fake_run, calls = _make_fake_run({
        "activity": _ACTIVITY_OUT,
        "services": _SERVICES_OUT,
        "providers": _PROVIDERS_OUT,
    })
    monkeypatch.setattr(AdbCliDriver, "_run_serial", fake_run)
    info = await AdbCliDriver().get_runtime_info("d1")
    assert [c[0] for c in calls] == [
        ("d1", "shell", "dumpsys", "activity"),
        ("d1", "shell", "dumpsys", "activity", "services"),
        ("d1", "shell", "dumpsys", "activity", "providers"),
    ]
    assert all(t == 10 for _, t in calls)
    assert info.pkg == "com.demo.app"
    assert info.resumed == "com.demo.app/.MainActivity"
    assert info.top_activity == "com.demo.app/.MainActivity"
    assert info.services == [ServiceInfo("com.demo.app/.BgService", "running")]
    assert info.providers == ["com.demo.app/.DemoProvider"]


async def test_get_runtime_info_tolerates_provider_error(monkeypatch):
    fake_run, _ = _make_fake_run({
        "activity": _ACTIVITY_OUT,
        "services": _SERVICES_OUT,
        "providers": AdbError("ADB command failed: Can't find service: activity providers"),
    })
    monkeypatch.setattr(AdbCliDriver, "_run_serial", fake_run)
    info = await AdbCliDriver().get_runtime_info("d1")
    assert info.providers == []
    assert info.resumed == "com.demo.app/.MainActivity"
    assert info.services == [ServiceInfo("com.demo.app/.BgService", "running")]


async def test_get_runtime_info_propagates_activity_error(monkeypatch):
    fake_run, _ = _make_fake_run({
        "activity": AdbError("ADB command failed"),
        "services": _SERVICES_OUT,
        "providers": _PROVIDERS_OUT,
    })
    monkeypatch.setattr(AdbCliDriver, "_run_serial", fake_run)
    with pytest.raises(AdbError, match="dumpsys activity"):
        await AdbCliDriver().get_runtime_info("d1")


async def test_get_runtime_info_propagates_services_error(monkeypatch):
    fake_run, _ = _make_fake_run({
        "activity": _ACTIVITY_OUT,
        "services": AdbError("ADB command failed"),
        "providers": _PROVIDERS_OUT,
    })
    monkeypatch.setattr(AdbCliDriver, "_run_serial", fake_run)
    with pytest.raises(AdbError, match="dumpsys activity services"):
        await AdbCliDriver().get_runtime_info("d1")
