"""
调试 HTTP 端点
================

层：接口 → HTTP。

提供调试会话相关的 RESTful API：

    POST   /api/debug/sessions                  — 创建调试会话
    GET    /api/debug/sessions/{session_id}     — 获取会话信息
    GET    /api/debug/sessions/{session_id}/logs — 查询日志
    POST   /api/debug/sessions/{session_id}/shell — 执行 shell 命令
    DELETE /api/debug/sessions/{session_id}     — 关闭会话

调试会话是"无限调试"功能的核心。创建会话后，后端会启动后台任务
持续收集 logcat 日志，并支持实时查询和 shell 命令执行。
"""

from fastapi import APIRouter, Depends

from app.application.debug_service import DebugService
from app.deps import get_debug_service

router = APIRouter(prefix="/api/debug", tags=["debug"])


@router.post("/sessions")
async def create_session(
    device_id: str,
    user_id: str,
    service: DebugService = Depends(get_debug_service),
):
    """
    创建调试会话。

    参数：
        device_id: 目标设备的 ADB 序列号（查询参数）。
        user_id: 创建会话的用户 ID（查询参数）。

    返回：
        {"session_id": "..."}。

    副作用：
        - 在内存和数据库中创建会话记录
        - 启动后台 logcat 收集任务
    """
    session = await service.create_session(device_id, user_id)
    return {"session_id": session.id}


@router.get("/sessions/{session_id}")
async def get_session(
    session_id: str,
    service: DebugService = Depends(get_debug_service),
):
    """
    获取调试会话信息。

    参数：
        session_id: 会话 ID（路径参数）。

    返回：
        会话信息（session_id, device_id, user_id, is_active），
        或 {"error": "Session not found"}。
    """
    session = await service.get_session(session_id)
    if not session:
        return {"error": "Session not found"}
    return {
        "session_id": session.id,
        "device_id": session.device_id,
        "user_id": session.user_id,
        "is_active": session.is_active,
    }


@router.get("/sessions/{session_id}/logs")
async def get_logs(
    session_id: str,
    level: str | None = None,
    tag: str | None = None,
    limit: int = 1000,
    service: DebugService = Depends(get_debug_service),
):
    """
    查询调试日志。

    参数：
        session_id: 会话 ID（路径参数）。
        level: 日志级别过滤（V/D/I/W/E/F），可选。
        tag: 日志标签过滤（子串匹配），可选。
        limit: 最大返回条数，默认 1000。

    返回：
        {"logs": [...]}，每条日志包含 ts, level, pid, tid, tag, message, raw。
    """
    logs = await service.get_logs(session_id, level=level, tag=tag, limit=limit)
    return {"logs": logs}


@router.post("/sessions/{session_id}/shell")
async def exec_shell(
    session_id: str,
    command: str,
    service: DebugService = Depends(get_debug_service),
):
    """
    在设备上执行 shell 命令。

    参数：
        session_id: 会话 ID（路径参数）。
        command: 要执行的 shell 命令（查询参数）。

    返回：
        {"output": "..."}。

    副作用：
        - 命令和输出会记录到 shell_history 表
    """
    output = await service.exec_shell(session_id, command)
    return {"output": output}


@router.delete("/sessions/{session_id}")
async def close_session(
    session_id: str,
    service: DebugService = Depends(get_debug_service),
):
    """
    关闭调试会话。

    参数：
        session_id: 要关闭的会话 ID（路径参数）。

    返回：
        {"success": True}。

    副作用：
        - 取消后台 logcat 收集任务
        - 从内存中移除会话（数据库记录保留）
    """
    await service.close_session(session_id)
    return {"success": True}
