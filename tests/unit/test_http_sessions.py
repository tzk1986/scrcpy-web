"""
协作会话 HTTP 端点（backend/app/interfaces/http/sessions.py）行为测试
=====================================================================

用 hand-rolled FakeSessionService 经 dependency_overrides 注入，覆盖：
    - POST /api/sessions                      创建（返回 session_id）
    - GET  /api/sessions/{id}                 存在（原样透传）/ 不存在
    - POST /api/sessions/{id}/join            permission 默认 viewer / 显式 admin
    - POST /api/sessions/{id}/leave           离开
    - POST /api/sessions/{id}/transfer        转移成功 / PermissionError→500

不触碰真实设备。
"""

from typing import Any

import pytest

from app.deps import get_session_service

from .http_testkit import make_client, override

SESSION = "dev-1_user-1"


def make_session() -> dict[str, Any]:
    """构造协作会话字典（结构同 SessionService.create_session）。"""
    return {
        "id": SESSION,
        "device_id": "dev-1",
        "owner": "user-1",
        "participants": {"user-1": "admin"},
        "active_controller": "user-1",
    }


class FakeSessionService:
    """替身：内存会话表 + 调用记录，可注入异常。"""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.sessions: dict[str, dict[str, Any]] = {}
        self.join_error: Exception | None = None
        self.transfer_error: Exception | None = None

    async def create_session(self, device_id: str, user_id: str) -> dict[str, Any]:
        self.calls.append(("create_session", device_id, user_id))
        session = {
            "id": f"{device_id}_{user_id}",
            "device_id": device_id,
            "owner": user_id,
            "participants": {user_id: "admin"},
            "active_controller": user_id,
        }
        self.sessions[session["id"]] = session
        return session

    async def get_session(self, session_id: str) -> dict[str, Any] | None:
        self.calls.append(("get_session", session_id))
        return self.sessions.get(session_id)

    async def join_session(self, session_id: str, user_id: str, permission: str = "viewer") -> None:
        self.calls.append(("join_session", session_id, user_id, permission))
        if self.join_error is not None:
            raise self.join_error

    async def leave_session(self, session_id: str, user_id: str) -> None:
        self.calls.append(("leave_session", session_id, user_id))

    async def transfer_control(self, session_id: str, from_user: str, to_user: str) -> None:
        self.calls.append(("transfer_control", session_id, from_user, to_user))
        if self.transfer_error is not None:
            raise self.transfer_error


@pytest.fixture
def fake() -> FakeSessionService:
    return FakeSessionService()


# ---------------------------------------------------------------------------
# 创建 / 查询
# ---------------------------------------------------------------------------

def test_create_session(fake: FakeSessionService) -> None:
    """创建会话返回 session_id（由 device_id/user_id 派生）。"""
    client = make_client()
    with override(get_session_service, fake):
        response = client.post(
            "/api/sessions", params={"device_id": "dev-1", "user_id": "user-1"}
        )

    assert response.status_code == 200
    assert response.json() == {"session_id": SESSION}
    assert fake.calls == [("create_session", "dev-1", "user-1")]


def test_get_session_found_passthrough(fake: FakeSessionService) -> None:
    """会话存在时原样透传（含 participants / active_controller）。"""
    fake.sessions[SESSION] = make_session()
    client = make_client()
    with override(get_session_service, fake):
        response = client.get(f"/api/sessions/{SESSION}")

    assert response.status_code == 200
    assert response.json() == make_session()


def test_get_session_not_found(fake: FakeSessionService) -> None:
    """会话不存在返回 200 + error 字段（前端约定，非 404）。"""
    client = make_client()
    with override(get_session_service, fake):
        response = client.get(f"/api/sessions/{SESSION}")

    assert response.status_code == 200
    assert response.json() == {"error": "Session not found"}


# ---------------------------------------------------------------------------
# 加入 / 离开
# ---------------------------------------------------------------------------

def test_join_session_default_permission(fake: FakeSessionService) -> None:
    """未传 permission 时默认 viewer。"""
    fake.sessions[SESSION] = make_session()
    client = make_client()
    with override(get_session_service, fake):
        response = client.post(f"/api/sessions/{SESSION}/join", params={"user_id": "user-2"})

    assert response.status_code == 200
    assert response.json() == {"success": True}
    assert fake.calls == [("join_session", SESSION, "user-2", "viewer")]


def test_join_session_admin_permission(fake: FakeSessionService) -> None:
    """显式 permission=admin 透传。"""
    fake.sessions[SESSION] = make_session()
    client = make_client()
    with override(get_session_service, fake):
        response = client.post(
            f"/api/sessions/{SESSION}/join",
            params={"user_id": "user-2", "permission": "admin"},
        )

    assert response.status_code == 200
    assert fake.calls == [("join_session", SESSION, "user-2", "admin")]


def test_leave_session(fake: FakeSessionService) -> None:
    """离开会话返回 success，参数透传。"""
    fake.sessions[SESSION] = make_session()
    client = make_client()
    with override(get_session_service, fake):
        response = client.post(f"/api/sessions/{SESSION}/leave", params={"user_id": "user-1"})

    assert response.status_code == 200
    assert response.json() == {"success": True}
    assert fake.calls == [("leave_session", SESSION, "user-1")]


# ---------------------------------------------------------------------------
# 控制权转移
# ---------------------------------------------------------------------------

def test_transfer_control_success(fake: FakeSessionService) -> None:
    """转移成功返回 success，from/to 透传。"""
    fake.sessions[SESSION] = make_session()
    client = make_client()
    with override(get_session_service, fake):
        response = client.post(
            f"/api/sessions/{SESSION}/transfer",
            params={"from_user": "user-1", "to_user": "user-2"},
        )

    assert response.status_code == 200
    assert response.json() == {"success": True}
    assert fake.calls == [("transfer_control", SESSION, "user-1", "user-2")]


def test_transfer_control_permission_denied_500(fake: FakeSessionService) -> None:
    """非 admin 转移 → PermissionError 未被端点捕获 → 统一兜底 500。"""
    fake.sessions[SESSION] = make_session()
    fake.transfer_error = PermissionError("Only admin can transfer control")
    client = make_client()
    with override(get_session_service, fake):
        response = client.post(
            f"/api/sessions/{SESSION}/transfer",
            params={"from_user": "user-2", "to_user": "user-3"},
        )

    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "INTERNAL_ERROR", "message": "An internal error occurred"}
    }


def test_join_session_missing_session_500(fake: FakeSessionService) -> None:
    """会话不存在 → 服务抛 ValueError → 统一兜底 500。"""
    fake.join_error = ValueError("Session not found")
    client = make_client()
    with override(get_session_service, fake):
        response = client.post(f"/api/sessions/{SESSION}/join", params={"user_id": "user-2"})

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"