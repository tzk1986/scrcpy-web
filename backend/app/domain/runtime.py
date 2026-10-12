"""
运行时状态实体
==============

层：领域层。

「应用检查 → 运行时状态层」（方案 37 子项 C）使用的数据对象：
从 dumpsys activity 系命令输出解析出的当前栈 / Service 清单 / Provider 清单。

被以下模块使用：
    - AdbDriver.get_runtime_info()    → 返回 RuntimeInfo
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
