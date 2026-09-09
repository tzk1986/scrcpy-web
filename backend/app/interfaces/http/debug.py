"""
调试 HTTP 端点
================

层：接口 → HTTP。

提供调试会话相关的 RESTful API：

    POST   /api/debug/sessions                  — 创建调试会话
    GET    /api/debug/sessions/{session_id}     — 获取会话信息
    GET    /api/debug/sessions/{session_id}/logs — 查询日志
    GET    /api/debug/sessions/{session_id}/logs/export — 导出日志
    POST   /api/debug/sessions/{session_id}/shell — 执行 shell 命令
    DELETE /api/debug/sessions/{session_id}     — 关闭会话

调试会话是"无限调试"功能的核心。创建会话后，后端会启动后台任务
持续收集 logcat 日志，并支持实时查询和 shell 命令执行。
"""

import csv
import io
import json

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

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


@router.get("/sessions/{session_id}/logs/export")
async def export_logs(
    session_id: str,
    level: str | None = None,
    tag: str | None = None,
    format: str = "json",
    limit: int = 50000,
    service: DebugService = Depends(get_debug_service),
):
    """
    导出调试日志为文件。

    参数：
        session_id: 会话 ID（路径参数）。
        level: 日志级别过滤，可选。
        tag: 日志标签过滤，可选。
        format: 导出格式，json 或 csv，默认 json。
        limit: 最大导出条数，默认 50000。

    返回：
        文件下载响应（Content-Disposition: attachment）。
    """
    logs = await service.get_logs(session_id, level=level, tag=tag, limit=limit)

    if format == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["timestamp", "level", "pid", "tid", "tag", "message"])
        for log in logs:
            writer.writerow([
                log.get("ts", ""),
                log.get("level", ""),
                log.get("pid", ""),
                log.get("tid", ""),
                log.get("tag", ""),
                log.get("message", ""),
            ])
        content = output.getvalue()
        media_type = "text/csv"
        filename = f"logs_{session_id}.csv"
    else:
        content = json.dumps(logs, indent=2, ensure_ascii=False)
        media_type = "application/json"
        filename = f"logs_{session_id}.json"

    return StreamingResponse(
        io.BytesIO(content.encode("utf-8")),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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


@router.post("/cleanup")
async def run_cleanup(
    service: DebugService = Depends(get_debug_service),
):
    """
    手动触发日志清理。

    执行以下清理操作：
      1. 删除超过保留期（默认 7 天）的日志
      2. 删除超过保留期（默认 30 天）的 shell 历史
      3. 如果数据库超过大小限制（默认 1GB），删除最旧日志

    返回：
        清理统计信息（删除数量、数据库大小等）。
    """
    result = await service.run_cleanup()
    return result


@router.delete("/sessions/{session_id}/logs")
async def cleanup_session_logs(
    session_id: str,
    service: DebugService = Depends(get_debug_service),
):
    """
    清理指定会话的所有日志。

    参数：
        session_id: 要清理的会话 ID（路径参数）。

    返回：
        {"deleted": 删除的日志条数}。
    """
    deleted = await service.cleanup_session(session_id)
    return {"deleted": deleted}


@router.get("/stats")
async def get_stats(
    service: DebugService = Depends(get_debug_service),
):
    """
    获取调试系统统计信息。

    返回：
        数据库大小、活跃会话数、订阅者数等。
    """
    return await service.get_db_stats()
