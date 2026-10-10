# backend/app/interfaces/ws/protocol.py
"""WS 帧消息解析（方案 37 A4）：非法/缺型消息归一为 None，杜绝 500 与栈泄露。"""
import json
from typing import Any


def parse_ws_message(raw: str | bytes) -> dict[str, Any] | None:
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("op"), str):
        return None
    return data
