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
