"""导出安全（方案 37 A3 / 方案 58 C-02/C-03）：文件名净化 + CSV 公式逃逸中和。"""

import re
from typing import Any

_FOLD = re.compile(r"_+")

# 电子表格公式/DDE 触发前导（OWASP CSV injection 口径）
_FORMULA_PREFIX = ("=", "+", "-", "@")


def csv_safe(value: Any) -> Any:
    """CSV 单元格公式逃逸中和：设备可控字符串首字符为 = + - @ 时前插
    ``'``（Excel/WPS/LibreOffice 按字面文本处理）；非字符串原样透传。
    三处导出（network SSID / debug logcat tag+message / perf activity）共用。"""
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIX):
        return "'" + value
    return value


def sanitize_export_filename(name: str) -> str:
    """
    净化导出文件名片段（debug/perf/network 三导出端点共用）。

    规则：
      - 白名单 ``[A-Za-z0-9._-]``（ASCII）之外全部替换为 ``_``；
      - 连续 ``_`` 折叠为单 ``_``；
      - 去除前导 ``.``/``_``（防隐藏文件语义与空脚），空串回退 ``export``。
    """
    cleaned = _FOLD.sub(
        "_",
        "".join(
            ch if ch.isascii() and (ch.isalnum() or ch in "._-") else "_"
            for ch in name
        ),
    )
    return cleaned.lstrip("._") or "export"
