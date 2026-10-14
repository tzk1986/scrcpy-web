"""
录屏 HTTP 端点
================

层：接口 → HTTP。

提供设备端 screenrecord 录屏相关的 RESTful API（方案 37 B4/B5）：

    POST /api/recording/{device_id}/start — 启动录屏（单设备互斥，冲突 409）
    POST /api/recording/{device_id}/stop  — 停止并收尾（终态 done/failed）
    GET  /api/recording/{device_id}       — 查询当前会话（无则 404 NO_RECORDING）
                                            （done 产物已被清理 → 收敛 failed 并 404，方案 42 §4）
    GET  /api/recording                   — 进行态会话列表（供前端刷新恢复状态；不带尾斜杠）
    GET  /api/recording/{device_id}/file  — 下载本地产物 MP4（containment 校验）
    GET  /api/recording/files             — 历史产物列表（mtime 降序，方案 46 §3.4）
    GET  /api/recording/files/{filename}  — 按文件名下载历史产物（白名单 + containment）
    DELETE /api/recording/files/{filename} — 删除历史产物（unlink 失败 500）

所有端点通过 Depends(get_recording_service) 注入 RecordingService 单例。

列表端点仅返回进行态会话：会话表 key=device_id、终态会话会被下一次
start 覆盖，历史会话以落盘文件为准（方案 37 B3 评审关注点①）。

file 端点只放行 done 态会话的 local_path，且解析后必须落在录屏落盘目录
内（目录解析复用录制服务 B3 的逻辑，防路径穿越读出目录外文件）。
"""

import re
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

from app.application.recording_service import RecordingService
from app.core.exceptions import RecordingDeleteError, RecordingNotFoundError
from app.core.logging import get_logger
from app.deps import get_recording_service
from app.domain.recording import RecordingSession, RecordingState

router = APIRouter(prefix="/api/recording", tags=["recording"])

logger = get_logger(__name__)

# 终态集合：GET 列表只回进行态（历史以落盘文件为准）
_TERMINAL_STATES = (RecordingState.DONE.value, RecordingState.FAILED.value)

# 历史产物文件名白名单（与 glob 同源 + 合法命名字符集；列表/下载/删除三处共用，
# 保证「列表可见即可下载/删除」一致，防幽灵行，方案 46 Review Focus ③）
_PRODUCT_NAME_RE = re.compile(r"^openscrcpy_rec_[A-Za-z0-9._-]+\.mp4$")


def _validated_product_path(service: RecordingService, filename: str) -> Path:
    """白名单 + containment + 存在性校验，返回解析后的绝对路径。

    任一不满足 → RecordingNotFoundError（404 NO_RECORDING，不泄露目录结构，
    与 {device_id}/file 端点同语义）。
    """
    if not _PRODUCT_NAME_RE.fullmatch(filename):
        raise RecordingNotFoundError(filename)
    records_dir = service.resolve_dir().resolve()
    target = (records_dir / filename).resolve()
    if target.name != filename:
        # Windows NTFS 大小写不敏感查找：大小写变体名（如大写 hex）
        # 会命中真文件返回 200/误删；命名只认小写 hex 段（parse_product_name
        # 语义），显式拒绝（方案 46 T2，Review Focus ③）。
        raise RecordingNotFoundError(filename)
    if not target.is_relative_to(records_dir) or not target.is_file():
        raise RecordingNotFoundError(filename)
    return target


def _session_body(session: RecordingSession) -> dict[str, Any]:
    """会话响应体：dataclass → JSON；done 会话附 file_size（方案 42 §4 面板信息行）。"""
    body: dict[str, Any] = {"session": asdict(session)}
    if session.state == RecordingState.DONE.value and session.local_path:
        try:
            body["file_size"] = Path(session.local_path).stat().st_size
        except OSError:
            body["file_size"] = None
    return body


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


@router.get("/files")
async def list_recording_files(
    service: RecordingService = Depends(get_recording_service),
) -> dict[str, list[dict[str, Any]]]:
    """
    历史录屏产物列表（扫盘为准，方案 46 §3.4）。

    返回：
        {"files": [{filename, device, session_id, size_bytes, created_at}, ...]}
        按 mtime 降序；目录不存在 → 空列表；白名单外文件不出现。
    """
    directory = service.resolve_dir()
    entries: list[tuple[float, dict[str, Any]]] = []
    if directory.is_dir():
        for path in directory.glob(RecordingService.LOCAL_FILE_GLOB):
            if not _PRODUCT_NAME_RE.fullmatch(path.name):
                continue
            try:
                st = path.stat()
            except OSError:  # 扫描间隙被清理：跳过
                continue
            device, session_id = RecordingService.parse_product_name(path.name)
            entries.append(
                (
                    st.st_mtime,
                    {
                        "filename": path.name,
                        "device": device,
                        "session_id": session_id,
                        "size_bytes": st.st_size,
                        "created_at": datetime.fromtimestamp(
                            st.st_mtime, tz=timezone.utc
                        ).isoformat(),
                    },
                )
            )
    entries.sort(key=lambda e: e[0], reverse=True)
    return {"files": [e[1] for e in entries]}


# {filename:path}（非默认 [^/]+）：TestClient 会把 %2F 预解码为 /，多出路径段
# 时默认参数会漏到 SPA catch-all（404/405 而非 NO_RECORDING）。用 :path 捕获含斜杠的
# 变体请求，让「解码后含 / → 白名单拒」在任意解码行为下都走 NO_RECORDING 404
# 同一语义（方案 46 Review Focus ③）。
@router.get("/files/{filename:path}")
async def get_recording_file_by_name(
    filename: str,
    service: RecordingService = Depends(get_recording_service),
) -> FileResponse:
    """
    按文件名下载/播放历史产物（方案 46 §3.4）。

    放行：白名单 + 解析后落在录屏目录内 + 文件存在；否则 404 NO_RECORDING。
    """
    return FileResponse(_validated_product_path(service, filename), media_type="video/mp4")


@router.delete("/files/{filename:path}")
async def delete_recording_file(
    filename: str,
    service: RecordingService = Depends(get_recording_service),
) -> dict[str, bool]:
    """
    删除单个历史产物（方案 46 拍板项 1：进第一版，与双限自动清理并存）。

    校验同下载；unlink 遇 OS 错误（Windows 占用）→ 500 RECORDING_DELETE_FAILED
    响亮失败不假成功（Review Focus ④）。
    """
    target = _validated_product_path(service, filename)
    try:
        target.unlink()
    except OSError as exc:
        logger.warning("recording_file_delete_failed", filename=filename, error=str(exc))
        raise RecordingDeleteError(filename) from exc
    logger.info("recording_file_deleted", filename=filename)
    return {"deleted": True}


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
    if session.state == RecordingState.DONE.value and (
        not session.local_path or not Path(session.local_path).is_file()
    ):
        # 产物已被清理（双限/关服清盘）而会话滞留 done：收敛 failed 并 404——
        # 前端据此静默复位 idle，消除「有面板无文件」假象（方案 42 §4）
        session.state = RecordingState.FAILED.value
        raise RecordingNotFoundError(device_id)
    return _session_body(session)


@router.get("/{device_id}/file")
async def get_recording_file(
    device_id: str,
    service: RecordingService = Depends(get_recording_service),
) -> FileResponse:
    """
    下载设备录屏的本地产物 MP4（前端 <video> 预览与下载链接共用）。

    放行条件：会话 done 且 local_path 非空、解析后路径落在录屏落盘
    目录内（recording_service.resolve_dir 同源解析）、文件真实存在。
    任一不满足 → 404 NO_RECORDING（不泄露目录结构）。

    返回：
        FileResponse: video/mp4 文件流。

    异常：
        RecordingNotFoundError: 无会话 / 未收尾 / 无产物 / 越界 / 文件已清。
    """
    session = service.get(device_id)
    if (
        session is None
        or session.state != RecordingState.DONE.value
        or not session.local_path
    ):
        raise RecordingNotFoundError(device_id)
    local_file = Path(session.local_path).resolve()
    records_dir = service.resolve_dir().resolve()
    if not local_file.is_relative_to(records_dir) or not local_file.is_file():
        raise RecordingNotFoundError(device_id)
    return FileResponse(local_file, media_type="video/mp4")