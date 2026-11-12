# backend/app/interfaces/ws/protocol.py
"""WS 帧消息解析（方案 37 A4）：非法/缺型消息归一为 None，杜绝 500 与栈泄露。

两函数分工：
    - parse_ws_message：debug / performance 端使用。协议帧恒带 op（str），
      非法 JSON / 非对象 / op 缺失或非 str 一律 None（调用侧回 BAD_MESSAGE）。
    - parse_video_input：video 输入面使用。视频输入协议与 debug 端不同构——
      存在无 op 的触控帧（{"action": "touch"/"swipe"/...}，前端
      inputController）与 {"op": "stats"} 帧率上报，不能要求 op 恒在；
      仅要求 JSON 对象且 op / action（如存在）为 str，否则 None
      （调用侧忽略该帧，输入面不断）。
"""
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


def parse_video_input(raw: str | bytes) -> dict[str, Any] | None:
    """视频输入帧解析（方案 37 A4）：非法 JSON / 非对象 / op、action 键型不符
    归一为 None（调用侧忽略，输入面不断）。

    与 protocol.parse_ws_message 的差异：视频输入协议存在无 op 的
    {"action": "touch"/"swipe"/...} 帧（前端 inputController），不能要求
    op 恒在；仅要求 JSON 对象且 op / action（如存在）为 str。
    """
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    for key in ("op", "action"):
        value = data.get(key)
        if value is not None and not isinstance(value, str):
            return None
    # 已知嵌套字段类型校验（方案 58 B-01，8.1 入站校验层）：copy_key 仅
    # clipboard_get 携带，须可 int 化（int 或数字串，bool 除外），否则归一
    # None 忽略——不得把 ValueError 放进 handler 的 int() 终结输入循环。
    copy_key = data.get("copy_key")
    if copy_key is not None:
        if isinstance(copy_key, bool):
            return None
        if isinstance(copy_key, str):
            if not copy_key.lstrip("+-").isdigit():
                return None
        elif not isinstance(copy_key, int):
            return None
    return data
