# tests/unit/test_cli_cpu_battery.py
"""
AdbCliDriver dumpsys cpuinfo / dumpsys battery 解析测试（方案 37 D2）
======================================================================

以 D1 双真机原始样本（tests/application/fixtures）为 fixture，覆盖：

    - parse_cpuinfo：双机真实样本进程行正则锚定解析（51 标准 + 12 `+` 前缀
      = 63 条锁定 .18；.25 9 条；drift 0 条不抛）、名段不
      含 pid、名内含冒号（kworker/3:0 等）非贪婪定位、TOTAL 汇总行自然跳过
    - parse_battery：双机四字段解析（temperature ×0.1 摄氏：26 → 2.6、
      424 → 42.4 板级怪值如实）、health 枚举映射 1-7、drift 三行缺失三 None、
      level 缺 → 0；`Max charging voltage:` 不以行首锚定误命中
    - get_cpu_usage / get_battery_info：各单命令（不做 gather）、超时 5s、
      返回透传；整命令 AdbError 不吞、向上抛（调用方 D3 容错）
"""

from pathlib import Path

import pytest

from app.core.exceptions import AdbError
from app.domain.runtime import BatteryInfo, CpuProcessInfo
from app.infrastructure.adb.cli import AdbCliDriver, parse_battery, parse_cpuinfo

FIX = Path(__file__).parent.parent / "application" / "fixtures"


# --- parse_cpuinfo：纯函数（fixture 真实样本） ---

def test_parse_cpuinfo_18_real_sample():
    procs = parse_cpuinfo((FIX / "dumpsys_cpuinfo_18.txt").read_text(encoding="utf-8"))
    # 51 条标准进程行 + 12 条 `+` 前缀新进程行 = 63（控制器裁决锁定值）
    assert len(procs) == 63
    # 每条都有非空进程名，且名段为纯名尾（不含 pid 数字段与 user/kernel 残留）
    assert all(p.proc for p in procs)
    assert all("user" not in p.proc and "kernel" not in p.proc for p in procs)
    # 带 faults 后缀行照常解析，后缀不参与
    assert CpuProcessInfo("com.duzkj.facecash", 2.9, 0.7) in procs
    assert CpuProcessInfo("system_server", 0.6, 0.1) in procs
    # 进程名内含冒号：kworker/0:1（`+` 前缀新进程形态）与 kworker/u9:1 后缀变体
    assert CpuProcessInfo("kworker/0:1", 0.0, 0.0) in procs
    assert CpuProcessInfo("kworker/u9:1-kbase_pm_poweroff_wait", 0.0, 0.0) in procs
    # `+` 前缀行按普通进程解析（sleep 三条即加号新进程）
    assert CpuProcessInfo("sleep", 0.0, 0.0) in procs
    # TOTAL 汇总行（3.5% TOTAL: ...）不出现为进程
    assert not any(p.proc == "TOTAL" for p in procs)


def test_parse_cpuinfo_25_real_sample():
    procs = parse_cpuinfo((FIX / "dumpsys_cpuinfo_25.txt").read_text(encoding="utf-8"))
    assert len(procs) == 9
    assert all(p.proc for p in procs)
    # 首进程整值锚定：2.3% 9004/com.android.settings: 2% user + 0.2% kernel
    assert procs[0] == CpuProcessInfo("com.android.settings", 2.0, 0.2)
    # 名内含冒号：kworker/3:0 与 kworker/u9:1（1.7.1.2 形态无 faults 后缀）
    assert CpuProcessInfo("kworker/3:0", 0.0, 0.0) in procs
    assert CpuProcessInfo("kworker/u9:1", 0.0, 0.1) in procs
    # 7.1.2 TOTAL 行仅两分量，同样不匹配进程行
    assert not any(p.proc == "TOTAL" for p in procs)


def test_parse_cpuinfo_drift_never_raises():
    procs = parse_cpuinfo((FIX / "dumpsys_cpuinfo_drift.txt").read_text(encoding="utf-8"))
    assert procs == []


def test_parse_cpuinfo_missing_header_returns_empty():
    # `CPU usage from` 头行缺失 → 空列表（不抛）
    assert parse_cpuinfo("Load: 0.11 / 0.09 / 0.09\n  1.2% 42/foo: 1% user + 0.2% kernel\n") == []


def test_parse_cpuinfo_plus_prefixed_nonzero():
    # `+` 前缀非零占用新进程形态（fixture 只有 +0%，补一条非零断言正则完整匹配）
    procs = parse_cpuinfo(
        "CPU usage from 1000ms to 0ms ago:\n"
        " 3.2% 42/sleep: 1.2% user + 1.3% kernel\n"
        " +1.1% 43/sleep: 0.5% user + 0.6% kernel\n"
        "2.3% TOTAL: 1% user + 0.9% kernel\n"
    )
    assert procs == [
        CpuProcessInfo("sleep", 1.2, 1.3),
        CpuProcessInfo("sleep", 0.5, 0.6),
    ]


# --- parse_battery：纯函数（fixture 真实样本） ---

def test_parse_battery_18_real_sample():
    b = parse_battery((FIX / "dumpsys_battery_18.txt").read_text(encoding="utf-8"))
    assert b.level == 50
    assert b.temperature == 2.6  # 原值 26（十分之一摄氏度）→ 2.6
    assert b.voltage == 3
    assert b.health == "GOOD"  # health: 2


def test_parse_battery_25_real_sample():
    b = parse_battery((FIX / "dumpsys_battery_25.txt").read_text(encoding="utf-8"))
    assert b.level == 100
    assert b.temperature == 42.4  # 原值 424（板级怪值如实，不特判）
    assert b.voltage == 0
    assert b.health == "GOOD"


def test_parse_battery_drift_missing_fields():
    # health/voltage/temperature 三行被删 → 三 None 不抛；level 保留 50
    b = parse_battery((FIX / "dumpsys_battery_drift.txt").read_text(encoding="utf-8"))
    assert b.level == 50
    assert b.temperature is None
    assert b.voltage is None
    assert b.health is None


def test_parse_battery_level_missing_falls_back_zero():
    b = parse_battery("Current Battery Service state:\n")
    assert b.level == 0
    assert b.temperature is None
    assert b.voltage is None
    assert b.health is None


_HEALTH_EXPECT = {
    1: "UNKNOWN",
    2: "GOOD",
    3: "OVERHEAT",
    4: "DEAD",
    5: "OVER_VOLTAGE",
    6: "UNSPECIFIED_FAILURE",
    7: "COLD",
}


def test_parse_battery_health_enum_mapping():
    for code, name in _HEALTH_EXPECT.items():
        b = parse_battery(f"  level: 1\n  health: {code}\n")
        assert b.health == name
    # 未识别码 → UNKNOWN；行缺失 → None
    assert parse_battery("  level: 1\n  health: 9\n").health == "UNKNOWN"
    assert parse_battery("  level: 1\n").health is None


def test_parse_battery_max_charging_voltage_not_mistaken():
    # `Max charging voltage:` 行含 voltage 一词，必须以行首字段名锚定（drift 样本已含此行）
    b = parse_battery("Current Battery Service state:\n  Max charging voltage: 0\n  level: 50\n")
    assert b.voltage is None
    assert b.level == 50


# --- get_cpu_usage / get_battery_info：方法层命令组装 + 透传 ---

def _make_fake_run(result):
    """构造 _run_serial 替身：记录参数，异常对象直接抛出，其余原样返回。"""
    calls = []

    async def fake_run(adr, *args, timeout=None):
        calls.append((args, timeout))
        if isinstance(result, Exception):
            raise result
        return result

    return fake_run, calls


async def test_get_cpu_usage_single_command(monkeypatch):
    fake_run, calls = _make_fake_run(
        "CPU usage from 1000ms to 0ms ago:\n  1.5% 42/tester: 1% user + 0.5% kernel\n"
    )
    monkeypatch.setattr(AdbCliDriver, "_run_serial", fake_run)
    procs = await AdbCliDriver().get_cpu_usage("d1")
    # 单命令（不做 gather——D3 采样循环自有布局）
    assert [c[0] for c in calls] == [("d1", "shell", "dumpsys", "cpuinfo")]
    assert [c[1] for c in calls] == [5]
    assert procs == [CpuProcessInfo("tester", 1.0, 0.5)]


async def test_get_cpu_usage_propagates_adb_error(monkeypatch):
    fake_run, _ = _make_fake_run(AdbError("ADB command failed"))
    monkeypatch.setattr(AdbCliDriver, "_run_serial", fake_run)
    with pytest.raises(AdbError):
        await AdbCliDriver().get_cpu_usage("d1")


async def test_get_battery_info_single_command(monkeypatch):
    fake_run, calls = _make_fake_run(
        "  level: 88\n  temperature: 300\n  voltage: 4000\n  health: 2\n"
    )
    monkeypatch.setattr(AdbCliDriver, "_run_serial", fake_run)
    b = await AdbCliDriver().get_battery_info("d1")
    assert [c[0] for c in calls] == [("d1", "shell", "dumpsys", "battery")]
    assert [c[1] for c in calls] == [5]
    assert b == BatteryInfo(level=88, temperature=30.0, voltage=4000, health="GOOD")


async def test_get_battery_info_propagates_adb_error(monkeypatch):
    fake_run, _ = _make_fake_run(AdbError("ADB command failed"))
    monkeypatch.setattr(AdbCliDriver, "_run_serial", fake_run)
    with pytest.raises(AdbError):
        await AdbCliDriver().get_battery_info("d1")