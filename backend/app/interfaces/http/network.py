"""
网络监控 HTTP 端点
===================

层：接口层。

提供设备网络状态的 RESTful API：
- GET /api/network/{device_id}/stats         当前网络统计（缓冲新鲜点复用/冷启动内联采样）
- GET /api/network/{device_id}/connections   活跃连接列表
- GET /api/network/{device_id}/export        导出（buffer/cache × csv/json）
- POST /api/network/{device_id}/record/start 开启录制（缓存态落盘）
- POST /api/network/{device_id}/record/stop  停止录制
- GET /api/network/{device_id}/record/status 录制状态

采样任务、内存缓冲、失联判定与空闲停采语义全部位于
NetworkService（应用层），本层无状态（原模块级采样器缓存的
语义已随方案 18 Step 1 上移）。

参考方案文档：方案/14-调试面板其他标签完善.md、方案/18-Perf与Network采样数据持久化与导出.md
"""

import csv
import io
import json
import time
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.application.network_service import NetworkService
from app.deps import get_network_service

router = APIRouter(prefix="/api/network", tags=["network"])

# 导出 CSV 列顺序（方案 18 Step 4）
_CSV_HEADER = [
    "ts", "ts_iso", "rx_bytes", "tx_bytes", "rx_rate_kbps",
    "tx_rate_kbps", "active_connections", "wifi_connected", "wifi_ssid",
]
_EXPORT_LIMIT_DEFAULT = 50000
_EXPORT_LIMIT_MAX = 200000


def _ts_iso(ts: float) -> str:
    """epoch 秒 → 本地时区 ISO8601（人工核对用）。"""
    return datetime.fromtimestamp(ts).isoformat()


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


@router.get("/{device_id}/export")
async def export_stats(
    device_id: str,
    format: str = "csv",
    source: str = "buffer",
    from_: float | None = Query(None, alias="from"),
    to: float | None = None,
    limit: int = _EXPORT_LIMIT_DEFAULT,
    service: NetworkService = Depends(get_network_service),
) -> StreamingResponse:
    """
    导出设备网络统计为文件下载（交付态，流式不留临时文件，方案 18 Step 4）。

    参数：
        device_id: 设备 ID（URL 路径参数）。
        format: csv 或 json，默认 csv。
        source: buffer（内存暂存快照，忽略 from/to）或 cache（缓存态区间）。
        from/to: source=cache 时的 ts 区间（epoch 秒，含边界）。
        limit: 最大导出条数，默认 50000，上界 200000。

    返回：
        文件下载响应（Content-Disposition: attachment）。
        空数据返回 404，错误码 NO_DATA。
    """
    if format not in ("csv", "json"):
        raise HTTPException(status_code=400, detail="format must be csv or json")
    if source not in ("buffer", "cache"):
        raise HTTPException(status_code=400, detail="source must be buffer or cache")
    if limit < 1 or limit > _EXPORT_LIMIT_MAX:
        raise HTTPException(
            status_code=400,
            detail=f"limit must be between 1 and {_EXPORT_LIMIT_MAX}",
        )

    if source == "buffer":
        rows = service.get_buffer_snapshot(device_id, limit)
    else:
        rows = await service.export_cached(device_id, from_ts=from_, to_ts=to, limit=limit)

    if not rows:
        raise HTTPException(status_code=404, detail="NO_DATA")

    # 文件名净化：device_id 含 ':'（如 192.168.8.18:5555），直接拼入
    # 在 Windows 上非法（方案 18 §3.3）
    safe_device = device_id.replace(":", "_")
    ts = int(time.time())

    if format == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(_CSV_HEADER)
        for row in rows:
            writer.writerow([
                row.get("ts", ""),
                _ts_iso(row["ts"]),
                row.get("rx_bytes", ""),
                row.get("tx_bytes", ""),
                row.get("rx_rate_kbps", ""),
                row.get("tx_rate_kbps", ""),
                row.get("active_connections", ""),
                row.get("wifi_connected", ""),
                row.get("wifi_ssid", ""),
            ])
        content = output.getvalue()
        media_type = "text/csv"
        filename = f"network_{safe_device}_{ts}.csv"
    else:
        content = json.dumps(rows, ensure_ascii=False)
        media_type = "application/json"
        filename = f"network_{safe_device}_{ts}.json"

    return StreamingResponse(
        io.BytesIO(content.encode("utf-8")),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/{device_id}/record/start")
async def record_start(
    device_id: str,
    service: NetworkService = Depends(get_network_service),
) -> dict[str, Any]:
    """
    开启录制（幂等）。metrics.recording=false 时返回 400 RECORDING_DISABLED。

    返回：
        录制状态（同 record/status）。
    """
    return await service.record_start(device_id)


@router.post("/{device_id}/record/stop")
async def record_stop(
    device_id: str,
    service: NetworkService = Depends(get_network_service),
) -> dict[str, Any]:
    """
    停止录制并停采（幂等）。

    返回：
        {recording, reason, rows}，rows 为本次录制落盘行数。
    """
    return await service.record_stop(device_id)


@router.get("/{device_id}/record/status")
async def record_status(
    device_id: str,
    service: NetworkService = Depends(get_network_service),
) -> dict[str, Any]:
    """
    录制状态。

    返回：
        {recording, reason, rows, oldest_ts, newest_ts}；
        rows/oldest_ts/newest_ts 为跨批次聚合值。
    """
    return await service.record_status(device_id)