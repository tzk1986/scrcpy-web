"""
录屏 HTTP 端点
================

层：接口 → HTTP。

提供设备端 screenrecord 录屏相关的 RESTful API（方案 37 B4）：

    POST /api/recording/{device_id}/start — 启动录屏（单设备互斥，冲突 409）
    POST /api/recording/{device_id}/stop  — 停止并收尾（终态 done/failed）
    GET  /api/recording/{device_id}       — 查询当前会话（无则 404 NO_RECORDING）
    GET  /api/recording                   — 进行态会话列表（供前端刷新恢复状态；不带尾斜杠）

所有端点通过 Depends(get_recording_service) 注入 RecordingService 单例。

列表端点仅返回进行态会话：会话表 key=device_id、终态会话会被下一次
start 覆盖，历史会话以落盘文件为准（方案 37 B3 评审关注点①）。
"""

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends

from app.application.recording_service import RecordingService
from app.core.exceptions import RecordingNotFoundError
from app.deps import get_recording_service
from app.domain.recording import RecordingSession, RecordingState

router = APIRouter(prefix="/api/recording", tags=["recording"])

# 终态集合：GET 列表只回进行态（历史以落盘文件为准）
_TERMINAL_STATES = (RecordingState.DONE.value, RecordingState.FAILED.value)


def _session_body(session: RecordingSession) -> dict[str, Any]:
    """会话响应体：dataclass → JSON。"""
    return {"session": asdict(session)}


@router.get("")
async def list_recordings(
    service: RecordingService = Depends(get_recording_service),
) -> dict[str, list[dict[str, Any]]]:
    """
    列出所有进行态录屏会话（state 不在 done/failed）。

    返回：
        {"recordings": [会话, ...]}，终态会话不出现。
    """
    active = [s for s in service.list_all() if s.state not in _TERMINAL_STATES]
    return {"recordings": [asdict(s) for s in active]}


@router.post("/{device_id}/start")
async def start_recording(
    device_id: str,
    service: RecordingService = Depends(get_recording_service),
) -> dict[str, Any]:
    """
    启动设备端录屏。

    返回：
        {"session": {...}}（state=recording）。

    异常：
        ConflictError: 该设备已有进行态会话（409 RECORDING_IN_PROGRESS）。
    """
    session = await service.start(device_id)
    return _session_body(session)


@router.post("/{device_id}/stop")
async def stop_recording(
    device_id: str,
    service: RecordingService = Depends(get_recording_service),
) -> dict[str, Any]:
    """
    停止录屏并收尾（终态 done/failed）。

    返回：
        {"session": {...}}（终态会话）。

    异常：
        RecordingNotFoundError: 无会话（404 NO_RECORDING）。
    """
    session = await service.stop(device_id)
    if session is None:
        raise RecordingNotFoundError(device_id)
    return _session_body(session)


@router.get("/{device_id}")
async def get_recording(
    device_id: str,
    service: RecordingService = Depends(get_recording_service),
) -> dict[str, Any]:
    """
    查询指定设备的当前录屏会话。

    返回：
        {"session": {...}}。

    异常：
        RecordingNotFoundError: 无会话（404 NO_RECORDING）。
    """
    session = service.get(device_id)
    if session is None:
        raise RecordingNotFoundError(device_id)
    return _session_body(session)