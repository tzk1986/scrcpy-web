# tests/unit/test_ws_message_parsing.py
from app.interfaces.ws.protocol import parse_ws_message


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
