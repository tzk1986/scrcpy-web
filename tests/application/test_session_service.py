"""
协作会话服务治理测试（A1 L2）
================================

覆盖安全审查 A1 排队项 L2 的三块：
    - session_id 改确定性 sha256 前缀：不含可枚举的明文拼接、
      「a_b + c」与「a + b_c」不再碰撞同 ID
    - join permission 白名单：仅 admin/viewer，其余 400 结构化错误
    - 活跃会话数上限：达限后 create 被拒（503 SessionLimitError）
"""

import re

import pytest

from app.application.session_service import _MAX_COLLAB_SESSIONS, SessionService
from app.core.exceptions import InvalidIdentifierError, SessionLimitError


async def test_create_session_id_is_deterministic_hash():
    """id 为 12 位十六进制（确定性派生），不含 device/user 明文；同参重建同 id。"""
    svc = SessionService()
    s1 = await svc.create_session("192.168.8.18:5555", "alice")
    s2 = await svc.create_session("192.168.8.18:5555", "alice")

    assert re.fullmatch(r"[0-9a-f]{12}", s1["id"])
    assert "192.168.8.18" not in s1["id"] and "alice" not in s1["id"]
    assert s1["id"] == s2["id"]


async def test_create_session_id_no_concat_ambiguity():
    """A1 L2：旧 f"{device}_{user}" 下 ("a_b","c") 与 ("a","b_c") 同 ID——哈希消歧。"""
    svc = SessionService()
    s1 = await svc.create_session("a_b", "c")
    s2 = await svc.create_session("a", "b_c")

    assert s1["id"] != s2["id"]


async def test_join_permission_whitelist():
    """permission 仅受理 admin/viewer；其余 → InvalidIdentifierError（不回显原值）。"""
    svc = SessionService()
    session = await svc.create_session("dev1", "alice")

    await svc.join_session(session["id"], "bob", "viewer")
    await svc.join_session(session["id"], "carol", "admin")
    assert session["participants"]["bob"] == "viewer"
    assert session["participants"]["carol"] == "admin"

    with pytest.raises(InvalidIdentifierError):
        await svc.join_session(session["id"], "mallory", "superuser")
    assert "mallory" not in session["participants"]


async def test_create_session_active_limit():
    """活跃协作会话达上限 → SessionLimitError，不再新建。"""
    svc = SessionService()
    for i in range(_MAX_COLLAB_SESSIONS):
        svc.sessions[f"fill-{i}"] = {"id": f"fill-{i}", "participants": {}}

    with pytest.raises(SessionLimitError):
        await svc.create_session("dev1", "alice")
    assert len(svc.sessions) == _MAX_COLLAB_SESSIONS
