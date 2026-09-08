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
    POST   /api/devices/connect             — 通过 TCP/IP 连接设备
    POST   /api/devices/{device_id}/disconnect — 断开 TCP/IP 连接
    GET    /api/devices/events              — 设备变化事件流（SSE）

所有端点通过 Depends(get_device_service) 注入 DeviceService 实例。
"""

import asyncio
import json

from fastapi import APIRouter, Depends
from fastapi.responses import Response, StreamingResponse

from app.application.device_service import DeviceService
from app.deps import get_device_service
from app.domain.device import DeviceInfo

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


@router.post("/connect")
async def connect_device(
    ip: str,
    port: int = 5555,
    service: DeviceService = Depends(get_device_service),
):
    """
    通过 TCP/IP 连接到设备。

    用于无线调试或 USB 连接不稳定时的备用方案。

    参数：
        ip: 设备 IP 地址（查询参数）。
        port: ADB 端口（查询参数，默认 5555）。

    返回：
        {"success": True, "device_id": "ip:port"}。
    """
    device_id = await service.connect_tcp(ip, port)
    return {"success": True, "device_id": device_id}


@router.post("/{device_id}/disconnect")
async def disconnect_device(
    device_id: str,
    service: DeviceService = Depends(get_device_service),
):
    """
    断开设备的 TCP/IP 连接。

    参数：
        device_id: 设备 ID（格式为 "ip:port"，路径参数）。

    返回：
        {"success": True}。
    """
    # 从 device_id 解析 ip 和 port
    if ":" in device_id:
        parts = device_id.split(":")
        ip = parts[0]
        port = int(parts[1]) if len(parts) > 1 else 5555
        await service.disconnect_tcp(ip, port)
    return {"success": True}


@router.get("/events")
async def device_events(service: DeviceService = Depends(get_device_service)):
    """
    设备变化事件流（Server-Sent Events）。

    实时推送设备连接和断开事件。

    事件格式：
        data: {"type": "connected", "device": {...}}
        data: {"type": "disconnected", "device_id": "..."}

    返回：
        SSE 流（Content-Type: text/event-stream）。
    """
    queue = asyncio.Queue()

    async def on_connected(device: DeviceInfo):
        """设备连接回调"""
        await queue.put({
            "type": "connected",
            "device": {
                "id": device.id,
                "model": device.model,
                "os_version": device.os_version,
                "resolution": list(device.resolution),
                "battery": device.battery,
                "status": device.status,
            }
        })

    async def on_disconnected(device_id: str):
        """设备断开回调"""
        await queue.put({"type": "disconnected", "device_id": device_id})

    # 注册回调
    service.on_device_connected(on_connected)
    service.on_device_disconnected(on_disconnected)

    async def event_stream():
        """生成 SSE 事件流"""
        try:
            while True:
                event = await queue.get()
                yield f"data: {json.dumps(event)}\n\n"
        except asyncio.CancelledError:
            # 客户端断开连接时清理回调
            if on_connected in service._on_device_connected:
                service._on_device_connected.remove(on_connected)
            if on_disconnected in service._on_device_disconnected:
                service._on_device_disconnected.remove(on_disconnected)
            raise

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 禁用 nginx 缓冲
        }
    )
