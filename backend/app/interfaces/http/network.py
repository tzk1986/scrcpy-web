"""
网络监控 HTTP 端点
===================

层：接口层。

提供设备网络状态的 RESTful API：
- GET /api/network/{device_id}/stats         当前网络统计（缓冲新鲜点复用/冷启动内联采样）
- GET /api/network/{device_id}/connections   活跃连接列表

采样任务、内存缓冲、失联判定与空闲停采语义全部位于
NetworkService（应用层），本层无状态（原模块级采样器缓存的
语义已随方案 18 Step 1 上移）。

参考方案文档：方案/14-调试面板其他标签完善.md、方案/18-Perf与Network采样数据持久化与导出.md
"""

from typing import Any

from fastapi import APIRouter, Depends

from app.application.network_service import NetworkService
from app.deps import get_network_service

router = APIRouter(prefix="/api/network", tags=["network"])


@router.get("/{device_id}/stats")
async def get_stats(
    device_id: str,
    service: NetworkService = Depends(get_network_service),
) -> dict[str, Any]:
    """
    获取当前网络统计。

    参数：
        device_id: 设备 ID（URL 路径参数）。
        service: 网络监控服务（依赖注入）。

    返回：
        JSON 对象，包含流量、速率、连接数、WiFi 状态。
        ts 为最近一次采样时刻（采样间隔口径，非请求时刻）。
    """
    stats = await service.get_stats(device_id)

    return {
        "ts": stats.ts,
        "rx_bytes": stats.rx_bytes,
        "tx_bytes": stats.tx_bytes,
        "rx_rate_kbps": stats.rx_rate_kbps,
        "tx_rate_kbps": stats.tx_rate_kbps,
        "active_connections": stats.active_connections,
        "wifi_connected": stats.wifi_connected,
        "wifi_ssid": stats.wifi_ssid,
    }


@router.get("/{device_id}/connections")
async def get_connections(
    device_id: str,
    protocol: str | None = None,
    service: NetworkService = Depends(get_network_service),
) -> dict[str, Any]:
    """
    获取活跃连接列表。

    参数：
        device_id: 设备 ID（URL 路径参数）。
        protocol: 过滤协议（tcp/udp/tcp6），为空则返回全部。
        service: 网络监控服务（依赖注入）。

    返回：
        JSON 对象，包含连接列表。
    """
    connections = await service.get_connections(device_id)

    # 过滤协议
    if protocol:
        connections = [c for c in connections if c.protocol == protocol]

    return {
        "connections": [
            {
                "protocol": c.protocol,
                "local_addr": c.local_addr,
                "local_port": c.local_port,
                "remote_addr": c.remote_addr,
                "remote_port": c.remote_port,
                "state": c.state,
                "uid": c.uid,
            }
            for c in connections
        ],
        "total": len(connections),
    }