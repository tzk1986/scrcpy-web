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


# ---- 方案 51 C4/C5/C6：控制权移交语义 ----


async def test_transfer_control_rejects_non_participant():
    """C4：目标不是参与者 → PermissionDeniedError，且控制权不变（原静默假成功）。"""
    from app.core.exceptions import PermissionDeniedError

    svc = SessionService()
    session = await svc.create_session("dev1", "alice")
    await svc.join_session(session["id"], "bob", "viewer")

    with pytest.raises(PermissionDeniedError):
        await svc.transfer_control(session["id"], "alice", "ghost")
    assert session["active_controller"] == "alice"


async def test_leave_controller_handover_to_remaining_admin():
    """C5 路径一：控制者离开 → 移交首个剩余 admin。"""
    svc = SessionService()
    session = await svc.create_session("dev1", "alice")
    await svc.join_session(session["id"], "bob", "viewer")
    await svc.join_session(session["id"], "carol", "admin")

    await svc.leave_session(session["id"], "alice")
    assert session["active_controller"] == "carol"


async def test_leave_controller_handover_to_participant_when_no_admin():
    """C5 路径二：无剩余 admin → 移交首个剩余参与者。"""
    svc = SessionService()
    session = await svc.create_session("dev1", "alice")
    await svc.join_session(session["id"], "bob", "viewer")

    await svc.leave_session(session["id"], "alice")
    assert session["active_controller"] == "bob"


async def test_leave_non_controller_keeps_controller():
    """C5 边界：离开者不是控制者 → 控制权不动。"""
    svc = SessionService()
    session = await svc.create_session("dev1", "alice")
    await svc.join_session(session["id"], "bob", "viewer")

    await svc.leave_session(session["id"], "bob")
    assert session["active_controller"] == "alice"


async def test_leave_last_participant_deletes_session():
    """C5 路径三：最后一名参与者离开 → 会话删除（维持既有口径）。"""
    svc = SessionService()
    session = await svc.create_session("dev1", "alice")

    await svc.leave_session(session["id"], "alice")
    assert session["id"] not in svc.sessions


async def test_create_session_idempotent_returns_existing():
    """C6：同 (device,user) 二次 create → 幂等返回既有会话，参与者与角色不重置。"""
    svc = SessionService()
    first = await svc.create_session("dev1", "alice")
    await svc.join_session(first["id"], "bob", "admin")
    await svc.transfer_control(first["id"], "alice", "bob")

    second = await svc.create_session("dev1", "alice")
    assert second is first
    assert second["participants"] == {"alice": "admin", "bob": "admin"}
    assert second["active_controller"] == "bob"
    assert len(svc.sessions) == 1


async def test_create_session_rejects_invalid_identifiers():
    """方案 51 C13：device_id 非法、user_id 空/含 | 或控制字符 → InvalidIdentifierError。"""
    svc = SessionService()
    with pytest.raises(InvalidIdentifierError):
        await svc.create_session("bad id;rm -rf", "alice")   # 非法字符
    with pytest.raises(InvalidIdentifierError):
        await svc.create_session("dev1", "a|b")              # 拼接分隔符
    with pytest.raises(InvalidIdentifierError):
        await svc.create_session("dev1", "a\nb")             # 控制字符
    with pytest.raises(InvalidIdentifierError):
        await svc.create_session("dev1", "")                 # 空 user_id
    assert svc.sessions == {}
