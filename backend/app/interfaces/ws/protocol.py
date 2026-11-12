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
import math
from typing import Any

# 方案 58 A-04：duration/hand 量程（毫秒 / scroll 幅值单位）
_DURATION_MIN, _DURATION_MAX = 0, 5000
_HAND_MIN, _HAND_MAX = -1000, 1000


def _clamp_int(value: Any, lo: int, hi: int) -> int | None:
    """数值化并钳制到 [lo, hi]；不可数值化（bool / 非数串 / 非有限 / 其它类型）
    返回 None（调用侧据此拒绝该帧）。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if not math.isfinite(value):
            return None
        return max(lo, min(hi, int(value)))
    if isinstance(value, str):
        try:
            num = float(value)
        except ValueError:
            return None
        if not math.isfinite(num):
            return None
        return max(lo, min(hi, int(num)))
    return None


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
    # 方案 58 B-20（8.1 入站校验层）：stats 帧 fps 拒 NaN/Inf/bool/非数——
    # NaN 挤满决策窗致升降档永久冻结，Infinity 恒计 good 误升档（静默 DoS）。
    # 越界有限值不在此拒（handler 0..120 钳制纵深）；缺失/null 走既有 0.0 语义。
    if data.get("op") == "stats" and data.get("fps") is not None:
        fps = data["fps"]
        if isinstance(fps, bool) or not isinstance(fps, int | float | str):
            return None
        try:
            if not math.isfinite(float(fps)):
                return None
        except ValueError:
            return None
    # 方案 58 A-04（8.1 入站校验层）：duration/hand 量程钳制。超大 duration
    # 会把 long_press 的 sleep / swipe 的 step_delay 睡死输入通道（服务端长
    # 阻塞），负值致 swipe `duration < 100` 直落 UP、负 sleep 令 DOWN/UP
    # 乱序；scroll 的 h_scroll/v_scroll 同钳幅值。控制 socket 与 adb 兜底
    # 两路共享同一份钳后 data，天然同口径。不可数值化/bool/非有限 → 整帧
    # 归一 None（B-01 同风格），把 ValueError 挡在下游 int()/sleep 之前。
    action = data.get("action")
    if action in ("long_press", "swipe") and data.get("duration") is not None:
        clamped = _clamp_int(data["duration"], _DURATION_MIN, _DURATION_MAX)
        if clamped is None:
            return None
        data["duration"] = clamped
    elif action == "scroll":
        for key in ("h_scroll", "v_scroll"):
            if data.get(key) is None:
                continue
            clamped = _clamp_int(data[key], _HAND_MIN, _HAND_MAX)
            if clamped is None:
                return None
            data[key] = clamped
    return data
