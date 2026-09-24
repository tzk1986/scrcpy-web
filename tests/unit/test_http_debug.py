"""
调试 HTTP 端点（backend/app/interfaces/http/debug.py）行为测试
==============================================================

用 hand-rolled FakeDebugService 经 dependency_overrides 注入，覆盖：
    - POST   /api/debug/sessions                     创建（返回 session_id）
    - GET    /api/debug/sessions/{id}                存在 / 不存在（404）
    - GET    /api/debug/sessions/{id}/logs           过滤参数透传与默认值
    - GET    /api/debug/sessions/{id}/logs/export    json / csv 两种格式与响应头；空数据→404
    - POST   /api/debug/sessions/{id}/shell          正常输出 / 会话缺失→404
    - DELETE /api/debug/sessions/{id}                关闭
    - POST   /api/debug/cleanup                      手动清理
    - DELETE /api/debug/sessions/{id}/logs           会话日志清理
    - GET    /api/debug/stats                        统计数据透传

不触碰真实 ADB/数据库。
"""

import json
from typing import Any

import pytest

from app.core.exceptions import SessionNotFoundError
from app.deps import get_debug_service

from .http_testkit import make_client, override

SESSION = "dev-1_user-1_1726000000"
LOGS: list[dict[str, Any]] = [
    {
        "ts": 1726000000.5,
        "level": "I",
        "pid": 1234,
        "tid": 5678,
        "tag": "ActivityManager",
        "message": "starting activity",
        "raw": "08-17 10:00:00.500  1234  5678 I ActivityManager: starting activity",
    },
    {"level": "E", "message": "boom"},  # 缺字段：导出时走 .get 默认值
]


class FakeSession:
    def __init__(
        self,
        sid: str = SESSION,
        device_id: str = "dev-1",
        user_id: str = "user-1",
        is_active: bool = True,
    ) -> None:
        self.id = sid
        self.device_id = device_id
        self.user_id = user_id
        self.is_active = is_active


class FakeDebugService:
    """替身：记录调用参数，返回预置结果。"""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.session: FakeSession | None = None
        self.logs: list[dict[str, Any]] = list(LOGS)
        self.shell_output = "total 0"
        self.shell_error: Exception | None = None
        self.cleaned = 7
        self.cleanup_result: dict[str, Any] = {
            "logs_deleted": 1,
            "shell_deleted": 2,
            "size_trimmed": 0,
            "db_size_bytes": 1048576,
            "db_size_mb": 1.0,
        }
        self.stats: dict[str, Any] = {
            "db_size_bytes": 2048,
            "db_size_mb": 0.0,
            "active_sessions": 1,
            "total_subscribers": 0,
        }

    async def create_session(self, device_id: str, user_id: str) -> FakeSession:
        self.calls.append(("create_session", device_id, user_id))
        self.session = FakeSession(f"{device_id}_{user_id}_1726000000")
        return self.session

    async def get_session(self, session_id: str) -> FakeSession | None:
        self.calls.append(("get_session", session_id))
        return self.session

    async def get_logs(
        self,
        session_id: str,
        level: str | None = None,
        tag: str | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        self.calls.append(("get_logs", session_id, level, tag, limit))
        return self.logs

    async def exec_shell(self, session_id: str, cmd: str) -> str:
        self.calls.append(("exec_shell", session_id, cmd))
        if self.shell_error is not None:
            raise self.shell_error
        return self.shell_output

    async def close_session(self, session_id: str) -> None:
        self.calls.append(("close_session", session_id))

    async def run_cleanup(self) -> dict[str, Any]:
        self.calls.append(("run_cleanup",))
        return self.cleanup_result

    async def cleanup_session(self, session_id: str) -> int:
        self.calls.append(("cleanup_session", session_id))
        return self.cleaned

    async def get_db_stats(self) -> dict[str, Any]:
        self.calls.append(("get_db_stats",))
        return self.stats


@pytest.fixture
def fake() -> FakeDebugService:
    return FakeDebugService()


# ---------------------------------------------------------------------------
# 会话生命周期
# ---------------------------------------------------------------------------

def test_create_session(fake: FakeDebugService) -> None:
    """创建会话返回 session_id，query 参数透传。"""
    client = make_client()
    with override(get_debug_service, fake):
        response = client.post(
            "/api/debug/sessions", params={"device_id": "dev-1", "user_id": "user-1"}
        )

    assert response.status_code == 200
    assert response.json() == {"session_id": "dev-1_user-1_1726000000"}
    assert fake.calls == [("create_session", "dev-1", "user-1")]


def test_get_session_found(fake: FakeDebugService) -> None:
    """会话存在返回 4 个字段。"""
    fake.session = FakeSession()
    client = make_client()
    with override(get_debug_service, fake):
        response = client.get(f"/api/debug/sessions/{SESSION}")

    assert response.status_code == 200
    assert response.json() == {
        "session_id": SESSION,
        "device_id": "dev-1",
        "user_id": "user-1",
        "is_active": True,
    }


def test_get_session_not_found(fake: FakeDebugService) -> None:
    """会话不存在返回 404 + 结构化错误体（统一契约，2026-09-21 由 200+字符串改）。"""
    client = make_client()
    with override(get_debug_service, fake):
        response = client.get(f"/api/debug/sessions/{SESSION}")

    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "SESSION_NOT_FOUND", "message": f"Session not found: {SESSION}"}
    }


def test_close_session(fake: FakeDebugService) -> None:
    """关闭会话返回 success。"""
    client = make_client()
    with override(get_debug_service, fake):
        response = client.delete(f"/api/debug/sessions/{SESSION}")

    assert response.status_code == 200
    assert response.json() == {"success": True}
    assert fake.calls == [("close_session", SESSION)]


# ---------------------------------------------------------------------------
# 日志查询
# ---------------------------------------------------------------------------

def test_get_logs_passes_filters_and_defaults(fake: FakeDebugService) -> None:
    """level/tag/limit 透传；未传时 limit 默认 1000。"""
    client = make_client()
    with override(get_debug_service, fake):
        default_resp = client.get(f"/api/debug/sessions/{SESSION}/logs")
        filtered_resp = client.get(
            f"/api/debug/sessions/{SESSION}/logs",
            params={"level": "E", "tag": "Activity", "limit": 50},
        )

    assert default_resp.status_code == 200
    assert default_resp.json() == {"logs": LOGS}
    assert filtered_resp.json() == {"logs": LOGS}
    assert fake.calls == [
        ("get_logs", SESSION, None, None, 1000),
        ("get_logs", SESSION, "E", "Activity", 50),
    ]


def test_export_logs_json(fake: FakeDebugService) -> None:
    """json 导出：Content-Type/文件名/内容与原日志一致，limit 默认 50000。"""
    client = make_client()
    with override(get_debug_service, fake):
        response = client.get(f"/api/debug/sessions/{SESSION}/logs/export")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["content-disposition"] == f'attachment; filename="logs_{SESSION}.json"'
    assert response.json() == LOGS
    assert fake.calls == [("get_logs", SESSION, None, None, 50000)]


def test_export_logs_csv(fake: FakeDebugService) -> None:
    """csv 导出：表头 + 行内容，缺字段条目用空串占位。"""
    client = make_client()
    with override(get_debug_service, fake):
        response = client.get(
            f"/api/debug/sessions/{SESSION}/logs/export",
            params={"format": "csv", "level": "I", "limit": 10},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["content-disposition"] == f'attachment; filename="logs_{SESSION}.csv"'
    lines = response.text.splitlines()
    assert lines[0] == "timestamp,level,pid,tid,tag,message"
    assert lines[1] == (
        "1726000000.5,I,1234,5678,ActivityManager,starting activity"
    )
    assert lines[2] == ",E,,,,boom"  # 缺 ts/pid/tid/tag → 空串
    assert fake.calls == [("get_logs", SESSION, "I", None, 10)]


def test_export_logs_no_data_404(fake: FakeDebugService) -> None:
    """空日志导出（未开始采集或过滤后为空）→ 404 NO_DATA，不产出空文件。"""
    fake.logs = []
    client = make_client()
    with override(get_debug_service, fake):
        response = client.get(f"/api/debug/sessions/{SESSION}/logs/export")

    assert response.status_code == 404
    assert response.json() == {"error": {"code": "HTTP_ERROR", "message": "NO_DATA"}}


# ---------------------------------------------------------------------------
# shell 执行 / 清理 / 统计
# ---------------------------------------------------------------------------

def test_exec_shell_success(fake: FakeDebugService) -> None:
    """shell 命令输出透传。"""
    client = make_client()
    with override(get_debug_service, fake):
        response = client.post(
            f"/api/debug/sessions/{SESSION}/shell", params={"command": "ls /sdcard"}
        )

    assert response.status_code == 200
    assert response.json() == {"output": "total 0"}
    assert fake.calls == [("exec_shell", SESSION, "ls /sdcard")]


def test_exec_shell_session_missing_404(fake: FakeDebugService) -> None:
    """服务抛 SessionNotFoundError（会话不存在）→ 404 SESSION_NOT_FOUND（原为兜底 500）。"""
    fake.shell_error = SessionNotFoundError(SESSION)
    client = make_client()
    with override(get_debug_service, fake):
        response = client.post(f"/api/debug/sessions/{SESSION}/shell", params={"command": "ls"})

    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "SESSION_NOT_FOUND", "message": f"Session not found: {SESSION}"}
    }


def test_run_cleanup(fake: FakeDebugService) -> None:
    """手动清理返回服务统计结果。"""
    client = make_client()
    with override(get_debug_service, fake):
        response = client.post("/api/debug/cleanup")

    assert response.status_code == 200
    assert response.json() == fake.cleanup_result
    assert fake.calls == [("run_cleanup",)]


def test_cleanup_session_logs(fake: FakeDebugService) -> None:
    """会话日志清理返回删除条数。"""
    client = make_client()
    with override(get_debug_service, fake):
        response = client.delete(f"/api/debug/sessions/{SESSION}/logs")

    assert response.status_code == 200
    assert response.json() == {"deleted": 7}
    assert fake.calls == [("cleanup_session", SESSION)]


def test_get_stats(fake: FakeDebugService) -> None:
    """统计数据原样返回。"""
    client = make_client()
    with override(get_debug_service, fake):
        response = client.get("/api/debug/stats")

    assert response.status_code == 200
    assert response.json() == fake.stats
    assert fake.calls == [("get_db_stats",)]


def test_export_logs_unicode_json(fake: FakeDebugService) -> None:
    """json 导出保留中文（ensure_ascii=False）。"""
    fake.logs = [{"ts": 1.0, "level": "I", "message": "中文日志", "tag": "T"}]
    client = make_client()
    with override(get_debug_service, fake):
        response = client.get(f"/api/debug/sessions/{SESSION}/logs/export")

    assert json.loads(response.content.decode("utf-8")) == fake.logs
    assert "中文日志" in response.content.decode("utf-8")