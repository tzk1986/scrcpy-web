"""
运行时状态实体
==============

层：领域层。

「应用检查 → 运行时状态层」（方案 37 子项 C）使用的数据对象：
从 dumpsys activity 系命令输出解析出的当前栈 / Service 清单 / Provider 清单。
子项 D（性能监控增强）的数据对象：
从 dumpsys cpuinfo / dumpsys battery 输出解析出的进程级 CPU 明细 / 电池四字段。

被以下模块使用：
    - AdbDriver.get_runtime_info()    → 返回 RuntimeInfo
    - AdbDriver.get_cpu_usage()       → 返回 list[CpuProcessInfo]
    - AdbDriver.get_battery_info()    → 返回 BatteryInfo
    - infrastructure/adb/cli.py       → 解析纯函数产物（TopActivityInfo 等）

属性：
    TopActivityInfo:
        pkg:          当前前台包名（取自 resumed 组件串的包名段；resumed 缺失时为空串）。
        top_activity: 当前 top Activity 组件（"pkg/class" 形态）。
        resumed:      系统 resumed Activity 组件（"pkg/class" 形态）。
    ServiceInfo:
        name:         Service 组件（"pkg/class" 形态）。
        state:        运行状态 — "running"（app=ProcessRecord）| "stopped"（app=null）；
                      app= 行缺失时 "unknown"。
    RuntimeInfo:
        pkg:          同 TopActivityInfo.pkg。
        top_activity: 同 TopActivityInfo.top_activity。
        resumed:      同 TopActivityInfo.resumed。
        services:     Service 清单（list[ServiceInfo]）。
        providers:    Provider 组件清单（list[str]，"pkg/class" 形态）。
    CpuProcessInfo:
        proc:         进程名（pid/ 后至名尾的名称段，不含 pid；名内可含冒号）。
        user:         用户态 CPU 占用百分比。
        kernel:       内核态 CPU 占用百分比。
    BatteryInfo:
        level:        电量 0-100（缺失 → 0）。
        temperature:  摄氏温度浮点（解析层 ÷10）；缺失 → None。
        voltage:      电压（整数原值）；缺失 → None。
        health:       健康枚举串；行缺失 → None；未识别码 → "UNKNOWN"。
"""

from dataclasses import dataclass


@dataclass
class TopActivityInfo:
    """dumpsys activity 主输出的顶层状态快照（top/resumed 锚定解析产物）。"""

    pkg: str
    top_activity: str
    resumed: str


@dataclass
class ServiceInfo:
    """单条 ServiceRecord（"pkg/class" 组件 + app= 行驱动的运行状态）。"""

    name: str
    state: str


@dataclass
class RuntimeInfo:
    """设备运行时状态聚合（三命令各自解析后组装）。"""

    pkg: str
    top_activity: str
    resumed: str
    services: list[ServiceInfo]
    providers: list[str]


@dataclass
class CpuProcessInfo:
    """
    dumpsys cpuinfo 单进程行解析产物（方案 37 D2）。

    属性：
        proc:   进程名（pid/ 后至名尾的名称段，不含 pid；名内可含冒号，
                如 kworker/3:0、kworker/u9:1-kbase_pm_poweroff_wait）。
        user:   用户态 CPU 占用百分比（原值直读，浮点）。
        kernel: 内核态 CPU 占用百分比（原值直读，浮点）。
    """

    proc: str
    user: float
    kernel: float


@dataclass
class BatteryInfo:
    """
    dumpsys battery 解析产物（方案 37 D2）。

    属性：
        level:       电量 0-100；行缺失/不可解析 → 0。
        temperature: 摄氏温度浮点（AOSP 原值为十分之一摄氏度，解析层 ÷10，
                     26 → 2.6、424 → 42.4，板级怪值如实不特判，D3/D5 直接
                     使用不再换算）；行缺失/不可解析 → None。
        voltage:     电压（整数原值）；行缺失/不可解析 → None。
        health:      健康枚举串（GOOD/OVERHEAT/DEAD/...，未识别码 → "UNKNOWN"）；
                     行缺失 → None。
    """

    level: int
    temperature: float | None
    voltage: int | None
    health: str | None
