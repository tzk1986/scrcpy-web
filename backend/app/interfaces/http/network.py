"""
网络监控 HTTP 端点
===================

层：接口层。

提供设备网络状态的 RESTful API：
- GET /api/network/{device_id}/stats         当前网络统计
- GET /api/network/{device_id}/connections   活跃连接列表

参考方案文档：方案/14-调试面板其他标签完善.md
"""

from fastapi import APIRouter

from app.core.logging import get_logger
from app.domain.ports import AdbDriver
from app.infrastructure.network.sampler import NetworkSampler

logger = get_logger(__name__)

router = APIRouter(prefix="/api/network", tags=["network"])


def _get_sampler(device_id: str) -> NetworkSampler:
    """创建 NetworkSampler 实例。"""
    from app.deps import get_adb_driver
    return NetworkSampler(get_adb_driver(), device_id)


@router.get("/{device_id}/stats")
async def get_stats(device_id: str):
    """
    获取当前网络统计。

    参数：
        device_id: 设备 ID。

    返回：
        JSON 对象，包含流量、速率、连接数、WiFi 状态。
    """
    sampler = _get_sampler(device_id)
    stats = await sampler.get_stats(device_id)

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
async def get_connections(device_id: str, protocol: str | None = None):
    """
    获取活跃连接列表。

    参数：
        device_id: 设备 ID。
        protocol: 过滤协议（tcp/udp/tcp6），为空则返回全部。

    返回：
        JSON 对象，包含连接列表。
    """
    sampler = _get_sampler(device_id)
    connections = await sampler.get_connections(device_id)

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
