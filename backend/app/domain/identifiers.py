"""外部输入标识符白名单（方案 37 A5：H1 宿主注入 / M3 设备侧注入的入口收敛）。

device_id 会进入 adb argv（ConPTY 经 cmd /C 拼接，元字符不转义）；
包名会 f-string 进设备侧 shell。仅放行保守字符集，其余拒绝。
"""

import re

_DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9._:\-]+$")
_PACKAGE_RE = re.compile(r"^[A-Za-z0-9._]+$")


def is_valid_device_id(device_id: str) -> bool:
    return bool(_DEVICE_ID_RE.fullmatch(device_id))


def is_valid_package_name(package: str) -> bool:
    return bool(_PACKAGE_RE.fullmatch(package))
