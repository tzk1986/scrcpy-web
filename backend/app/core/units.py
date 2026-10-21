"""
通用单位解析工具
==================

层：核心。

方案 51 B12：从 application/bitrate_advisor.py 下沉至 core——
infrastructure（scrcpy.py）复用会构成 infra→application 逆依赖，
纯函数归核心层后各层可正向引用。
"""


def parse_bit_rate(value: str | int) -> int:
    """解析码率配置值："4M"→4000000、"512K"→512000、int 原样返回。"""
    if isinstance(value, int):
        return value
    s = str(value).strip()
    if s.upper().endswith("M"):
        return int(float(s[:-1]) * 1_000_000)
    if s.upper().endswith("K"):
        return int(float(s[:-1]) * 1_000)
    return int(s)
