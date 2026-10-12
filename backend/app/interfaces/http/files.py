"""
设备文件管理 HTTP 端点
========================

层：接口 → HTTP。

    GET  /api/devices/{device_id}/files?path=…          — 目录列表（读全盘）
    GET  /api/devices/{device_id}/file/download?paths=… — 下载（单=原文件，多=zip）
    POST /api/devices/{device_id}/file/upload?dir=…     — multipart 上传（单请求单文件）
    POST /api/devices/{device_id}/file/rename           — {path, new_name}
    POST /api/devices/{device_id}/file/delete           — {paths}（写白名单内）
    GET  /api/devices/{device_id}/file-jobs/{job_id}    — 任务状态

安全边界在应用层（FileService：白名单/互斥/净化）；本层只做路由、
序列化与响应装配。下载暂存清理挂 FileResponse background——响应发送
完成后执行；客户端中断可能跳过 background，服务层 15 分钟孤儿清扫兜底
（方案 39 §七已知限制）。X-File-Job 头回传任务 ID 供前端进度关联。

挂 /api/devices 前缀子路由：本族路径均为多段，不与单段 /{device_id}
参数路由冲突（devices.py:143 坑仅涉单段字面路径）。
"""

from typing import Any

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from app.application.file_service import FileService
from app.deps import get_file_service
from app.domain.files import FileEntry, TransferJob

router = APIRouter(prefix="/api/devices", tags=["files"])


class RenameRequest(BaseModel):
    """重命名请求体（方案 39 §四契约）。"""

    path: str
    new_name: str


class DeleteRequest(BaseModel):
    """删除请求体（批量，逐项执行结果汇总）。"""

    paths: list[str]


def _entry_public(e: FileEntry) -> dict[str, Any]:
    """FileEntry → JSON 形状（字段与前端 FileEntry 接口一致）。"""
    return {
        "name": e.name,
        "is_dir": e.is_dir,
        "is_symlink": e.is_symlink,
        "size": e.size,
        "mtime": e.mtime,
        "permissions": e.permissions,
        "link_target": e.link_target,
    }


def _job_public(job: TransferJob) -> dict[str, Any]:
    """TransferJob → JSON 公开形状（内部字段不落响应）。"""
    return {
        "id": job.id,
        "device_id": job.device_id,
        "direction": job.direction,
        "paths": job.paths,
        "total_size": job.total_size,
        "status": job.status,
        "substage": job.substage,
        "error": job.error,
    }


@router.get("/{device_id}/files")
async def list_files(
    device_id: str,
    path: str = Query("/sdcard"),
    service: FileService = Depends(get_file_service),
) -> list[dict[str, Any]]:
    """列目录（默认 /sdcard）。不存在 404 PATH_NOT_FOUND、无权限 403。"""
    entries = await service.list_dir(device_id, path)
    return [_entry_public(e) for e in entries]


@router.get("/{device_id}/file/download")
async def download_files(
    device_id: str,
    paths: list[str] = Query(..., min_length=1),
    service: FileService = Depends(get_file_service),
) -> FileResponse:
    """下载：单文件原样流、多选后端 zip。X-File-Job 头携带任务 ID。

    互斥中 → 409 TRANSFER_BUSY；目录 → 400 NOT_A_FILE。
    """
    job, artifact, filename = await service.prepare_download(device_id, paths)
    return FileResponse(
        artifact,
        filename=filename,
        media_type="application/octet-stream",
        background=BackgroundTask(service.finalize_download, job.id),
        headers={"X-File-Job": job.id},
    )


@router.post("/{device_id}/file/upload")
async def upload_file(
    device_id: str,
    file: UploadFile = File(...),
    dir_path: str = Query("/sdcard", alias="dir"),
    service: FileService = Depends(get_file_service),
) -> dict[str, Any]:
    """项目首个 multipart 端点（python-multipart 已在依赖，零新依赖）。

    单请求单文件——多文件由前端顺序逐个（互斥即单飞）。字节流经
    file.read 注入应用层（application 不 import fastapi）。
    """
    job = await service.upload(device_id, dir_path, file.filename or "", file.read)
    return _job_public(job)


@router.post("/{device_id}/file/rename")
async def rename_file(
    device_id: str,
    body: RenameRequest,
    service: FileService = Depends(get_file_service),
) -> dict[str, bool]:
    """同目录重命名：白名单外 403；目标已存在 409 TARGET_EXISTS。"""
    await service.rename(device_id, body.path, body.new_name)
    return {"ok": True}


@router.post("/{device_id}/file/delete")
async def delete_files(
    device_id: str,
    body: DeleteRequest,
    service: FileService = Depends(get_file_service),
) -> dict[str, Any]:
    """批量删除：任一白名单外路径整体 403（预检），再逐项执行汇总。"""
    results = await service.delete(device_id, body.paths)
    return {"results": results}


@router.get("/{device_id}/file-jobs/{job_id}")
async def get_file_job(
    device_id: str,
    job_id: str,
    service: FileService = Depends(get_file_service),
) -> dict[str, Any]:
    """任务状态；跨设备探测与过期条目统一 404 JOB_NOT_FOUND。"""
    return _job_public(service.get_job(device_id, job_id))
