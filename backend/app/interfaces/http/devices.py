"""
设备管理 HTTP 端点
====================

层：接口 → HTTP。

提供设备相关的 RESTful API：

    GET    /api/devices              — 列出所有连接的设备
    GET    /api/devices/{device_id}  — 获取指定设备信息
    POST   /api/devices/{device_id}/install — 安装 APK
    POST   /api/devices/batch/install       — 批量安装 APK
    GET    /api/devices/{device_id}/screenshot — 截图

所有端点通过 Depends(get_device_service) 注入 DeviceService 实例。
"""

from fastapi import APIRouter, Depends
from fastapi.responses import Response

from app.application.device_service import DeviceService
from app.deps import get_device_service

router = APIRouter(prefix="/api/devices", tags=["devices"])


@router.get("")
async def list_devices(service: DeviceService = Depends(get_device_service)):
    """
    列出所有通过 ADB 连接的设备。

    返回：
        DeviceInfo 对象列表（JSON 格式）。
    """
    return await service.list_devices()


@router.get("/{device_id}")
async def get_device(device_id: str, service: DeviceService = Depends(get_device_service)):
    """
    获取指定设备的详细信息。

    参数：
        device_id: 设备 ADB 序列号（路径参数）。

    返回：
        DeviceInfo 对象，或 {"error": "Device not found"}。
    """
    device = await service.get_device(device_id)
    if not device:
        return {"error": "Device not found"}
    return device


@router.post("/{device_id}/install")
async def install_apk(
    device_id: str,
    apk_path: str,
    service: DeviceService = Depends(get_device_service),
):
    """
    在指定设备上安装 APK。

    参数：
        device_id: 设备 ADB 序列号（路径参数）。
        apk_path: APK 文件路径（查询参数）。

    返回：
        {"success": True, "result": "..."}。
    """
    result = await service.install_apk(device_id, apk_path)
    return {"success": True, "result": result}


@router.post("/batch/install")
async def batch_install(
    device_ids: list[str],
    apk_path: str,
    service: DeviceService = Depends(get_device_service),
):
    """
    在多个设备上批量安装同一个 APK。

    参数：
        device_ids: 设备 ADB 序列号列表（请求体）。
        apk_path: APK 文件路径（查询参数）。

    返回：
        {"success": True, "results": ["device1: success", "device2: failed - ..."]}。
    """
    results = await service.batch_install(device_ids, apk_path)
    return {"success": True, "results": results}


@router.get("/{device_id}/screenshot")
async def screenshot(device_id: str, service: DeviceService = Depends(get_device_service)):
    """
    截取设备屏幕截图。

    参数：
        device_id: 设备 ADB 序列号（路径参数）。

    返回：
        PNG 图片数据（Content-Type: image/png）。
    """
    png_bytes = await service.screenshot(device_id)
    return Response(content=png_bytes, media_type="image/png")
