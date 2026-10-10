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
