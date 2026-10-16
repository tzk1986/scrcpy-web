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
    POST   /api/devices/pair                — 无线调试配对（Android 11+，方案 47 T4）
    POST   /api/devices/scan                — 网段扫描并批量连接（方案 36）
    POST   /api/devices/{device_id}/disconnect — 断开 TCP/IP 连接
    GET    /api/devices/events              — 设备变化事件流（SSE）

所有端点通过 Depends(get_device_service) 注入 DeviceService 实例。
"""

import asyncio
import json
import re
from collections.abc import AsyncIterator
from ipaddress import IPv4Address, IPv4Network, ip_address, ip_network
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response, StreamingResponse

from app.application.device_service import DeviceService, ScanResult
from app.core.exceptions import (
    AdbError,
    DeviceNotFoundError,
    InvalidPairRequestError,
    InvalidScanRangeError,
)
from app.core.launcher import is_shutdown_requested
from app.deps import get_device_service
from app.domain.device import DeviceInfo
from config.settings import device_alias

router = APIRouter(prefix="/api/devices", tags=["devices"])


@router.get("")
async def list_devices(service: DeviceService = Depends(get_device_service)) -> list[DeviceInfo]:
    """
    列出所有通过 ADB 连接的设备。

    返回：
        DeviceInfo 对象列表（JSON 格式）。
    """
    return await service.list_devices()


@router.post("/batch/install")
async def batch_install(
    device_ids: list[str],
    apk_path: str,
    service: DeviceService = Depends(get_device_service),
) -> dict[str, Any]:
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


@router.get("/events")
async def device_events(service: DeviceService = Depends(get_device_service)) -> StreamingResponse:
    """
    设备变化事件流（Server-Sent Events）。

    实时推送设备连接和断开事件。

    事件格式：
        data: {"type": "connected", "device": {...}}
        data: {"type": "disconnected", "device_id": "..."}

    返回：
        SSE 流（Content-Type: text/event-stream）。
    """
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def on_connected(device: DeviceInfo) -> None:
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
                # 走 helper 单源，不依赖回调对象是否已被 service 填充（方案 50）
                "alias": device.alias or device_alias(device.id),
            }
        })

    async def on_disconnected(device_id: str) -> None:
        """设备断开回调"""
        await queue.put({"type": "disconnected", "device_id": device_id})

    # 注册回调
    service.on_device_connected(on_connected)
    service.on_device_disconnected(on_disconnected)

    async def event_stream() -> AsyncIterator[str]:
        """生成 SSE 事件流。

        停机优雅收尾（方案 23 T4 R2）：无限流会挂死 uvicorn 优雅停机
        （等待在途响应结束），故 get 空闲超时轮询停机标志，置位即正常
        return——响应完整收尾、连接随之关闭。
        """
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=0.2)
                except asyncio.TimeoutError:
                    if is_shutdown_requested():
                        return
                    continue
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            # 客户端断开或停机收尾：清理回调
            if on_connected in service._on_device_connected:
                service._on_device_connected.remove(on_connected)
            if on_disconnected in service._on_device_disconnected:
                service._on_device_disconnected.remove(on_disconnected)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 禁用 nginx 缓冲
        }
    )


# 注意：以下 /{device_id} 参数路由必须注册在上述字面路径路由之后，
# 否则 /batch/install、/events 会被当作 device_id 吞掉。
@router.get("/{device_id}")
async def get_device(
    device_id: str, service: DeviceService = Depends(get_device_service)
) -> DeviceInfo:
    """
    获取指定设备的详细信息。

    参数：
        device_id: 设备 ADB 序列号（路径参数）。

    返回：
        DeviceInfo 对象。

    异常：
        DeviceNotFoundError: 设备未连接（映射为 404 DEVICE_NOT_FOUND）。
    """
    device = await service.get_device(device_id)
    if not device:
        raise DeviceNotFoundError(device_id)
    return device


@router.post("/{device_id}/install")
async def install_apk(
    device_id: str,
    apk_path: str,
    service: DeviceService = Depends(get_device_service),
) -> dict[str, Any]:
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


@router.get("/{device_id}/screenshot")
async def screenshot(
    device_id: str,
    format: Literal["png", "raw"] = "png",
    service: DeviceService = Depends(get_device_service),
) -> Response:
    """
    截取设备屏幕截图。

    参数：
        device_id: 设备 ADB 序列号（路径参数）。
        format: png（默认，设备端 PNG 编码）或 raw（方案 29：
            设备端 raw 帧 + gzip 透传，经 Content-Encoding: gzip
            由浏览器透明解压；无 gzip 设备单请求内回退 PNG）。

    返回：
        png：PNG 图片数据（Content-Type: image/png）。
        raw：gzip 压缩的 raw RGBA 帧数据（X-Frame-Format: raw-rgba）；
        链路失败时回退 PNG（X-Frame-Format: png）。
        两类响应均携带 X-Rotation（0-3，方案 38 B6）：前端 raw 链路据此
        画布补偿；PNG 链路已由应用层内联补偿仅 180°（90/270 不旋转，
        screencap 原生输出即逻辑正立），头供一致性参考。
    """
    rotation = await service.get_rotation(device_id)
    if format == "raw":
        try:
            gzip_bytes = await service.screenshot_raw_gzip(device_id)
        except AdbError:
            png_bytes = await service.screenshot(device_id)
            return Response(
                content=png_bytes,
                media_type="image/png",
                headers={"X-Frame-Format": "png", "X-Rotation": str(rotation)},
            )
        return Response(
            content=gzip_bytes,
            media_type="application/octet-stream",
            headers={
                "Content-Encoding": "gzip",
                "X-Frame-Format": "raw-rgba",
                "X-Rotation": str(rotation),
            },
        )
    png_bytes = await service.screenshot(device_id)
    return Response(
        content=png_bytes,
        media_type="image/png",
        headers={"X-Rotation": str(rotation)},
    )


@router.post("/scan")
async def scan_devices(
    cidr: str,
    connect: bool = True,
    # 终评 I-2：越界端口在 Windows 上被 sockaddr 回绕静默扫错端口，接口层拦截
    port: int = Query(5555, ge=1, le=65535),
    service: DeviceService = Depends(get_device_service),
) -> ScanResult:
    """
    扫描网段/区间内 TCP 开放主机并批量连接（方案 36 + 增补 D11）。

    参数：
        cidr: 扫描目标（查询参数），三形态（详见 _parse_scan_targets）：
            单 IP「192.168.8.18」/ CIDR「192.168.8.0/24」（/22-/32，host 位
            非零自动归一化）/ 起止区间「192.168.8.10-100」（简写）或
            「192.168.8.10-192.168.9.20」（完整，含两端，≤1022 台）。
        connect: 是否对开放主机执行 adb 连接（查询参数，默认 true）。
        port: ADB 端口（查询参数，默认 5555）。

    返回：
        ScanResult：开放清单全量（数值序）+ 实际尝试的连接结果（≤64 台）
        + truncated 截断标记；label 为目标原样输入。

    异常：
        InvalidScanRangeError: 目标非法/超上限（422 INVALID_SCAN_RANGE）。
        ScanBusyError: 已有扫描进行中（409 SCAN_BUSY）。
    """
    label, targets = _parse_scan_targets(cidr)
    return await service.scan_and_connect(label, targets, connect=connect, port=port)


_MAX_SCAN_TARGETS = 1022  # 与 CIDR /22 的 hosts() 数量对齐（方案 36 增补 D11）


def _parse_scan_targets(raw: str) -> tuple[str, list[str]]:
    """
    解析扫描目标为 (label, targets)（方案 36 增补 D11）。

    label 取原输入（strip 后）；targets 为数值升序的 IPv4 主机列表。

    三形态：
        - CIDR：「a.b.c.d/nn」，/22-/32，host 位非零归一化后展开 hosts()；
        - 单 IP：「a.b.c.d」，仅该主机；
        - 区间：「ip-末段数字」（同段简写，前缀取自左端）或「ip1-ip2」
          （完整，可跨段）；两端合法 IPv4、end ≥ start、不含 `/`、
          总数 ≤ 1022；简写右端须 0-255 且无前导零（防 int("010") 静默通过）。

    非法/超上限 → InvalidScanRangeError。
    """
    label = raw.strip()
    if not label:
        raise InvalidScanRangeError(raw)
    if "/" in label:
        if "-" in label:
            raise InvalidScanRangeError(raw)
        try:
            network = ip_network(label, strict=False)
        except ValueError:
            raise InvalidScanRangeError(raw)
        if not isinstance(network, IPv4Network) or not 22 <= network.prefixlen <= 32:
            raise InvalidScanRangeError(raw)
        return label, [str(ip) for ip in network.hosts()]
    if "-" in label:
        left, _, right = label.partition("-")
        left, right = left.strip(), right.strip()
        try:
            start = ip_address(left)
        except ValueError:
            raise InvalidScanRangeError(raw)
        if not isinstance(start, IPv4Address):
            raise InvalidScanRangeError(raw)
        if "." not in right:
            # 简写：右端为末段数字，前缀取自左端同段
            if not (right.isdigit() and 0 <= int(right) <= 255
                    and len(right) == len(str(int(right)))):
                raise InvalidScanRangeError(raw)
            end = ip_address(f"{left.rsplit('.', 1)[0]}.{right}")
        else:
            try:
                end = ip_address(right)
            except ValueError:
                raise InvalidScanRangeError(raw)
            if not isinstance(end, IPv4Address):
                raise InvalidScanRangeError(raw)
        if int(end) < int(start):
            raise InvalidScanRangeError(raw)
        count = int(end) - int(start) + 1
        if count > _MAX_SCAN_TARGETS:
            raise InvalidScanRangeError(raw)
        return label, [str(ip_address(int(start) + i)) for i in range(count)]
    try:
        ip = ip_address(label)
    except ValueError:
        raise InvalidScanRangeError(raw)
    if not isinstance(ip, IPv4Address):
        raise InvalidScanRangeError(raw)
    return label, [str(ip)]


@router.post("/connect")
async def connect_device(
    ip: str,
    port: int = 5555,
    service: DeviceService = Depends(get_device_service),
) -> dict[str, Any]:
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


# 配对码校验：仅 ASCII 6 位数字（re.ASCII 防 \d 匹配全角数字，方案 47 T4）
_PAIR_CODE_RE = re.compile(r"\d{6}", re.ASCII)


@router.post("/pair")
async def pair_device(
    ip: str,
    port: int,
    code: str,
    service: DeviceService = Depends(get_device_service),
) -> dict[str, Any]:
    """
    无线调试配对（Android 11+，方案 47 T4）。

    两步式第一步：对手机「使用配对码配对设备」弹窗显示的 ip:port + 6 位
    配对码执行 adb pair；成功后需用设备显示的调试端口另行 connect
    （复用 /connect）。校验在端点层完成，非法参数走 400 结构化错误。

    参数：
        ip: 弹窗显示的配对主机 IP（仅 IPv4）。
        port: 弹窗显示的配对端口（1-65535，随弹窗变化，无默认值）。
        code: 6 位配对码（字符串，保留前导零）。

    返回：
        {"success": True, "paired": "ip:port"}。

    异常：
        InvalidPairRequestError: 参数非法（400，不回显原值）。
        DeviceUnreachableError: 配对端口不可达（400）。
        PairError: 配对失败（400，分类中文文案）。
    """
    try:
        addr = ip_address(ip)
    except ValueError:
        raise InvalidPairRequestError("ip")
    if not isinstance(addr, IPv4Address):
        raise InvalidPairRequestError("ip")
    if not 1 <= port <= 65535:
        raise InvalidPairRequestError("port")
    if not _PAIR_CODE_RE.fullmatch(code):
        raise InvalidPairRequestError("code")
    await service.pair_tcp(ip, port, code)
    return {"success": True, "paired": f"{ip}:{port}"}


@router.post("/{device_id}/disconnect")
async def disconnect_device(
    device_id: str,
    service: DeviceService = Depends(get_device_service),
) -> dict[str, Any]:
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
