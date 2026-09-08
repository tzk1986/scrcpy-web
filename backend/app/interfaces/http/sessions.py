"""
协作会话 HTTP 端点
====================

层：接口 → HTTP。

提供多人协作调试的会话管理 API：

    POST /api/sessions                     — 创建协作会话
    GET  /api/sessions/{session_id}        — 获取会话信息
    POST /api/sessions/{session_id}/join   — 加入会话
    POST /api/sessions/{session_id}/leave  — 离开会话
    POST /api/sessions/{session_id}/transfer — 转移控制权

协作会话允许多个用户同时观察/控制同一台设备。
每个会话有一个 owner（创建者）和一个 active_controller（当前控制者）。
参与者角色：admin（管理员）或 viewer（观察者）。
"""

from fastapi import APIRouter, Depends

from app.application.session_service import SessionService
from app.deps import get_session_service

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


@router.post("")
async def create_session(
    device_id: str,
    user_id: str,
    service: SessionService = Depends(get_session_service),
):
    """
    创建协作会话。

    参数：
        device_id: 目标设备的 ADB 序列号（查询参数）。
        user_id: 创建者的用户 ID（查询参数）。

    返回：
        {"session_id": "..."}。
    """
    session = await service.create_session(device_id, user_id)
    return {"session_id": session["id"]}


@router.get("/{session_id}")
async def get_session(
    session_id: str,
    service: SessionService = Depends(get_session_service),
):
    """
    获取协作会话信息。

    参数：
        session_id: 会话 ID（路径参数）。

    返回：
        会话详情（包含 participants 和 active_controller），
        或 {"error": "Session not found"}。
    """
    session = await service.get_session(session_id)
    if not session:
        return {"error": "Session not found"}
    return session


@router.post("/{session_id}/join")
async def join_session(
    session_id: str,
    user_id: str,
    permission: str = "viewer",
    service: SessionService = Depends(get_session_service),
):
    """
    加入协作会话。

    参数：
        session_id: 会话 ID（路径参数）。
        user_id: 加入者的用户 ID（查询参数）。
        permission: 角色权限，"admin" 或 "viewer"（默认）。

    返回：
        {"success": True}。
    """
    await service.join_session(session_id, user_id, permission)
    return {"success": True}


@router.post("/{session_id}/leave")
async def leave_session(
    session_id: str,
    user_id: str,
    service: SessionService = Depends(get_session_service),
):
    """
    离开协作会话。

    参数：
        session_id: 会话 ID（路径参数）。
        user_id: 离开者的用户 ID（查询参数）。

    返回：
        {"success": True}。

    注意：
        如果会话中没有剩余参与者，会话会被自动删除。
    """
    await service.leave_session(session_id, user_id)
    return {"success": True}


@router.post("/{session_id}/transfer")
async def transfer_control(
    session_id: str,
    from_user: str,
    to_user: str,
    service: SessionService = Depends(get_session_service),
):
    """
    转移设备控制权。

    参数：
        session_id: 会话 ID（路径参数）。
        from_user: 当前控制者（必须是 admin）。
        to_user: 接收控制权的用户。

    返回：
        {"success": True}。

    异常：
        PermissionError: 如果 from_user 不是 admin。
    """
    await service.transfer_control(session_id, from_user, to_user)
    return {"success": True}
