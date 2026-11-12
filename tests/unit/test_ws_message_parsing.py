# tests/unit/test_ws_message_parsing.py
from app.interfaces.ws.protocol import parse_video_input, parse_ws_message


def test_invalid_json_returns_none():
    assert parse_ws_message('{"op": ') is None


def test_non_object_returns_none():
    assert parse_ws_message("[1,2,3]") is None
    assert parse_ws_message('"string"') is None


def test_op_non_string_returns_none():
    assert parse_ws_message('{"op": 42}') is None


def test_valid_message():
    assert parse_ws_message('{"op": "exec", "command": "ls"}') == {"op": "exec", "command": "ls"}


def test_extra_fields_preserved():
    assert parse_ws_message('{"op": "x", "k": 1}') == {"op": "x", "k": 1}


# ---------------------------------------------------------------------------
# parse_video_input（video 输入面专用，Fix 轮 R1 补直接单测）
# ---------------------------------------------------------------------------

def test_video_invalid_json_returns_none():
    assert parse_video_input("{") is None


def test_video_non_object_returns_none():
    assert parse_video_input("[1,2]") is None
    assert parse_video_input('"s"') is None


def test_video_action_non_string_returns_none():
    assert parse_video_input('{"action": 42}') is None


def test_video_op_non_string_returns_none():
    assert parse_video_input('{"op": 42}') is None


def test_video_touch_frame_passes_through():
    """无 op 的触控帧（前端 inputController 协议）原样通过。"""
    assert parse_video_input('{"action": "touch", "x": 1, "y": 2}') == {
        "action": "touch", "x": 1, "y": 2,
    }


def test_video_stats_frame_passes_through():
    """帧率上报帧原样通过。"""
    assert parse_video_input('{"op": "stats", "fps": 25}') == {"op": "stats", "fps": 25}


def test_video_clipboard_get_bad_copy_key_returns_none():
    """方案 58 B-01：copy_key 不可 int 化（"abc"/dict/bool）归一 None 忽略——
    畸形嵌套字段不得让 ValueError 进入输入循环（旧实现一发即杀整条输入通道）。"""
    assert parse_video_input('{"action": "clipboard_get", "copy_key": "abc"}') is None
    assert parse_video_input('{"action": "clipboard_get", "copy_key": {}}') is None
    assert parse_video_input('{"action": "clipboard_get", "copy_key": true}') is None
    assert parse_video_input('{"action": "clipboard_get", "copy_key": [1]}') is None


def test_video_clipboard_get_int_like_copy_key_passes():
    """int 与数字串照旧透传（copy_key 语义不变）；缺省/None 合法（handler or 0 兜底）。"""
    assert parse_video_input('{"action": "clipboard_get", "copy_key": 7}') == {
        "action": "clipboard_get", "copy_key": 7,
    }
    assert parse_video_input('{"action": "clipboard_get", "copy_key": "-3"}') == {
        "action": "clipboard_get", "copy_key": "-3",
    }
    assert parse_video_input('{"action": "clipboard_get"}') == {"action": "clipboard_get"}
    assert parse_video_input('{"action": "clipboard_get", "copy_key": null}') == {
        "action": "clipboard_get", "copy_key": None,
    }


def test_video_stats_bad_fps_returns_none():
    """方案 58 B-20（8.1 入站校验层）：fps 为 NaN/Inf/bool/dict/list/非数串
    归一 None 忽略——NaN 挤满决策窗令自适应升降档永久冻结（静默 DoS），
    Infinity 恒计 good 误升档。"""
    assert parse_video_input('{"op": "stats", "fps": NaN}') is None
    assert parse_video_input('{"op": "stats", "fps": Infinity}') is None
    assert parse_video_input('{"op": "stats", "fps": -Infinity}') is None
    assert parse_video_input('{"op": "stats", "fps": "NaN"}') is None
    assert parse_video_input('{"op": "stats", "fps": "Infinity"}') is None
    assert parse_video_input('{"op": "stats", "fps": "abc"}') is None
    assert parse_video_input('{"op": "stats", "fps": true}') is None
    assert parse_video_input('{"op": "stats", "fps": {}}') is None
    assert parse_video_input('{"op": "stats", "fps": [1]}') is None


def test_video_stats_good_fps_passes():
    """有限数值/数字串/缺省与 null（既有 0.0 语义）放行；越界有限值由 handler
    0..120 钳制纵深承接，不在协议层拒。"""
    assert parse_video_input('{"op": "stats", "fps": 25}') is not None
    assert parse_video_input('{"op": "stats", "fps": "25.5"}') is not None
    assert parse_video_input('{"op": "stats", "fps": 1e9}') is not None
    assert parse_video_input('{"op": "stats", "fps": -50}') is not None
    assert parse_video_input('{"op": "stats"}') is not None
    assert parse_video_input('{"op": "stats", "fps": null}') is not None


# ---------------------------------------------------------------------------
# parse_video_input：duration/hand 量程钳制（方案 58 A-04，8.1 入站校验层）
# ---------------------------------------------------------------------------

def test_video_duration_clamped_to_max():
    """超大 duration 钳到 5000ms：long_press 的 sleep 与 swipe 的 step_delay
    都在协议层封顶，输入通道不再被睡死；控制/兜底两路共享钳后值（同口径）。"""
    data = parse_video_input('{"action": "long_press", "x": 1, "y": 2, "duration": 999999}')
    assert data is not None and data["duration"] == 5000
    data = parse_video_input('{"action": "swipe", "x1": 0, "y1": 0, "x2": 9, "y2": 9, "duration": 1e12}')
    assert data is not None and data["duration"] == 5000


def test_video_duration_clamped_to_floor():
    """负 duration 钳到 0：swipe 的 `duration < 100` 直落 UP 与负 sleep 的
    DOWN/UP 乱序都不再发生。"""
    data = parse_video_input('{"action": "long_press", "x": 1, "y": 2, "duration": -100}')
    assert data is not None and data["duration"] == 0
    data = parse_video_input('{"action": "swipe", "x1": 0, "y1": 0, "x2": 9, "y2": 9, "duration": -100}')
    assert data is not None and data["duration"] == 0


def test_video_duration_non_numeric_rejects_frame():
    """不可数值化/bool/非有限值的 duration → 整帧归一 None（B-01 同风格），
    不得把 ValueError 放进下游 int()/sleep。"""
    assert parse_video_input('{"action": "long_press", "duration": "abc"}') is None
    assert parse_video_input('{"action": "long_press", "duration": true}') is None
    assert parse_video_input('{"action": "long_press", "duration": {}}') is None
    assert parse_video_input('{"action": "swipe", "x1": 0, "y1": 0, "x2": 9, "y2": 9, "duration": [1]}') is None
    assert parse_video_input('{"action": "long_press", "duration": "1e400"}') is None


def test_video_duration_normal_values_untouched():
    """量程内与缺省（下游默认 1000/300）不改动。"""
    data = parse_video_input('{"action": "long_press", "x": 1, "y": 2, "duration": 600}')
    assert data is not None and data["duration"] == 600
    data = parse_video_input('{"action": "long_press", "x": 1, "y": 2}')
    assert data is not None and "duration" not in data


def test_video_scroll_hand_clamped_and_guarded():
    """scroll 的 h_scroll/v_scroll 幅值钳到 ±1000；非数值同帧拒绝。"""
    data = parse_video_input('{"action": "scroll", "x": 1, "y": 1, "h_scroll": -5000, "v_scroll": 5000}')
    assert data is not None and data["h_scroll"] == -1000 and data["v_scroll"] == 1000
    data = parse_video_input('{"action": "scroll", "x": 1, "y": 1, "h_scroll": -1, "v_scroll": 1}')
    assert data is not None and data["h_scroll"] == -1 and data["v_scroll"] == 1
    assert parse_video_input('{"action": "scroll", "x": 1, "y": 1, "v_scroll": "xx"}') is None
