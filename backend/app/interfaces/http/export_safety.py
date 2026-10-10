"""导出文件名净化（方案 37 A3）：白名单外替换、防穿越/盘符/隐藏文件。"""

import re

_FOLD = re.compile(r"_+")


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