"""spawn argv 的 cmd 元字符防护（方案 37 A5 / H1）。无需真实 ConPTY。"""

import pytest

from app.infrastructure.adb.winpty import _validate_argv, spawn_conpty


def test_validate_argv_accepts_device_id_forms():
    _validate_argv(["-s", "192.168.8.18:5555", "shell", "-tt"])  # 不抛


def test_validate_argv_rejects_cmd_metachars():
    for bad in ("x&calc", "a|b", "a>b", "a<b", "a^b", "a%b", 'a"b', "a(b)", "a!b", "a\nb"):
        with pytest.raises(ValueError):
            _validate_argv(["-s", bad, "shell", "-tt"])


async def test_spawn_conpty_rejects_injection_before_platform_check():
    """校验先于平台可用性检查：非 Windows 环境同样以 ValueError 失败（可测性锚点）。"""
    with pytest.raises(ValueError):
        await spawn_conpty(["cmd.exe", "-s", "x&whoami", "shell"])
