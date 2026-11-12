"""
A1 L4：回显用户输入的错误消息净化（截断 + 去控制字符）
========================================================

异常消息会把用户输入（设备 ID/路径/任务 ID 等）原样回显给前端。
审查报告 A1 L4 指出：超长载荷与控制字符（ANSI 转义/换行）随消息
进入响应与日志，存在刷屏与注入面。_clip 统一截断至 120 字符并
剔除不可打印字符；正常短输入不受影响（既有断言零破坏）。
"""

import asyncio
import json

from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.exceptions import (
    DeviceNotFoundError,
    JobNotFoundError,
    NotAFileError,
    PathNotFoundError,
    RecordingDeleteError,
    RecordingNotFoundError,
    SessionNotFoundError,
    TargetExistsError,
    _clip,
    http_exception_handler,
)

LONG = "x" * 300


def test_clip_truncates_with_ellipsis() -> None:
    assert _clip(LONG) == "x" * 120 + "…"


def test_clip_keeps_normal_text() -> None:
    assert _clip("emulator-5554") == "emulator-5554"
    assert _clip("录屏产物.mp4") == "录屏产物.mp4"


def test_clip_strips_control_chars() -> None:
    assert _clip("a\nb\tc\x1b[31mred\x00d") == "abc[31mredd"


def test_clip_empty() -> None:
    assert _clip("") == ""


def test_no_clip_boundary_exactly_limit() -> None:
    assert _clip("y" * 120) == "y" * 120


def test_all_echoing_exceptions_clip_input() -> None:
    payload = "\x1b" + LONG
    cases = [
        DeviceNotFoundError(payload),
        SessionNotFoundError(payload),
        RecordingNotFoundError(payload),
        RecordingDeleteError(payload),  # 方案 51 D07：第 13 类
        PathNotFoundError(payload),
        TargetExistsError(payload),
        JobNotFoundError(payload),
        NotAFileError(payload),
    ]
    for exc in cases:
        assert "\x1b" not in exc.message
        assert "\n" not in exc.message
        assert len(exc.message) < 200
        assert "…" in exc.message


def test_normal_inputs_messages_unchanged() -> None:
    """既有行为兼容：短正常输入的消息与治理前逐字一致（存量断言不破坏）。"""
    assert DeviceNotFoundError("dev-1").message == "Device not found: dev-1"
    assert JobNotFoundError("job-9").message == "Transfer job not found: job-9"
    assert SessionNotFoundError("abc").message == "Session not found: abc"


# ---------------------------------------------------------------------------
# C-18（方案 58）：http_exception_handler 的 detail 同样过 _clip
# ---------------------------------------------------------------------------

def _handler_body(detail: object) -> dict:
    resp = asyncio.run(http_exception_handler(None, StarletteHTTPException(404, detail)))  # type: ignore[arg-type]
    assert resp.status_code == 404
    return json.loads(resp.body)


def test_http_handler_clips_str_detail() -> None:
    """方案 58 C-18：detail 字符串经 _clip——控制字符剔除、超限截断。"""
    body = _handler_body("\x1b" + LONG)
    message = body["error"]["message"]
    assert "\x1b" not in message
    assert len(message) <= 121  # 120 + 省略号
    assert message.endswith("…")


def test_http_handler_short_str_detail_unchanged() -> None:
    """既有口径：短 detail（404/400 常用文本）逐字不变。"""
    body = _handler_body("source must be buffer or cache")
    assert body["error"] == {"code": "HTTP_ERROR", "message": "source must be buffer or cache"}


def test_http_handler_non_str_detail_passthrough() -> None:
    """detail 非字符串（dict/list，FastAPI 内部构造）原样透传，不被 _clip 字符串化。"""
    detail = {"loc": ["query", "limit"], "msg": "value is not a valid integer"}
    body = _handler_body(detail)
    assert body["error"]["message"] == detail
