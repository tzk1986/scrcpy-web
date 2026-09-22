"""
性能监控 HTTP 端点
==================

层：接口层。

提供设备性能指标的 RESTful API：
- GET /api/perf/{device_id}/metrics       查询历史指标（内存暂存）
- POST /api/perf/{device_id}/start        启动监控
- POST /api/perf/{device_id}/stop         停止监控
- GET /api/perf/{device_id}/export        导出（buffer/cache × csv/json）
- POST /api/perf/{device_id}/record/start 开启录制（缓存态落盘）
- POST /api/perf/{device_id}/record/stop  停止录制
- GET /api/perf/{device_id}/record/status 录制状态

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

from app.application.performance_service import PerformanceService
from app.core.config import settings
from app.core.logging import get_logger
from app.deps import get_performance_service

logger = get_logger(__name__)

router = APIRouter(prefix="/api/perf", tags=["performance"])

# 导出 CSV 列顺序（方案 18 Step 4）
_CSV_HEADER = [
    "ts", "ts_iso", "cpu_percent", "total_memory_mb", "used_memory_mb",
    "fps", "jank_count", "current_activity", "top_package",
]
_EXPORT_LIMIT_DEFAULT = 50000
_EXPORT_LIMIT_MAX = 200000


def _ts_iso(ts: float) -> str:
    """epoch 秒 → 本地时区 ISO8601（人工核对用）。"""
    return datetime.fromtimestamp(ts).isoformat()


@router.get("/{device_id}/metrics")
async def get_metrics(
    device_id: str,
    limit: int = 100,
    service: PerformanceService = Depends(get_performance_service),
) -> dict[str, Any]:
    """
    获取设备历史性能指标。

    参数：
        device_id: 设备 ID（URL 路径参数）。
        limit: 返回的最大条数（查询参数，默认 100）。
        service: 性能监控服务（依赖注入）。

    返回：
        JSON 对象，包含 metrics 列表。

    示例：
        GET /api/perf/192.168.1.100/metrics?limit=50
        {
            "metrics": [
                {
                    "ts": 1726045200.123,
                    "cpu_percent": 23.5,
                    "total_memory_mb": 4096,
                    "used_memory_mb": 2048,
                    "fps": 60,
                    "jank_count": 0,
                    "current_activity": "com.example/.MainActivity",
                    "top_package": "com.example"
                }
            ]
        }
    """
    if limit < 1 or limit > settings().metrics.buffer_size:
        raise HTTPException(
            status_code=400,
            detail=f"limit must be between 1 and {settings().metrics.buffer_size}",
        )

    metrics = await service.get_metrics(device_id, limit)
    return {"metrics": metrics, "count": len(metrics)}


@router.post("/{device_id}/start")
async def start_monitoring(
    device_id: str,
    interval: float = 1.0,
    service: PerformanceService = Depends(get_performance_service),
) -> dict[str, Any]:
    """
    启动设备性能监控。

    参数：
        device_id: 设备 ID。
        interval: 采样间隔（秒，默认 1.0）。
        service: 性能监控服务。

    返回：
        JSON 对象，包含成功状态。
    """
    if interval < 0.1 or interval > 60:
        raise HTTPException(status_code=400, detail="interval must be between 0.1 and 60")

    await service.start_monitoring(device_id, interval)
    logger.info("performance_monitoring_api_start", device=device_id, interval=interval)

    return {"success": True, "device_id": device_id, "interval": interval}


@router.post("/{device_id}/stop")
async def stop_monitoring(
    device_id: str,
    service: PerformanceService = Depends(get_performance_service),
) -> dict[str, Any]:
    """
    停止设备性能监控。

    参数：
        device_id: 设备 ID。
        service: 性能监控服务。

    返回：
        JSON 对象，包含成功状态。
    """
    await service.stop_monitoring(device_id)
    logger.info("performance_monitoring_api_stop", device=device_id)

    return {"success": True, "device_id": device_id}


@router.get("/{device_id}/export")
async def export_metrics(
    device_id: str,
    format: str = "csv",
    source: str = "buffer",
    from_: float | None = Query(None, alias="from"),
    to: float | None = None,
    limit: int = _EXPORT_LIMIT_DEFAULT,
    service: PerformanceService = Depends(get_performance_service),
) -> StreamingResponse:
    """
    导出设备性能指标为文件下载（交付态，流式不留临时文件，方案 18 Step 4）。

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
        rows = await service.get_metrics(device_id, limit)
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
                row.get("cpu_percent", ""),
                row.get("total_memory_mb", ""),
                row.get("used_memory_mb", ""),
                row.get("fps", ""),
                row.get("jank_count", ""),
                row.get("current_activity", ""),
                row.get("top_package", ""),
            ])
        content = output.getvalue()
        media_type = "text/csv"
        filename = f"perf_{safe_device}_{ts}.csv"
    else:
        content = json.dumps(rows, ensure_ascii=False)
        media_type = "application/json"
        filename = f"perf_{safe_device}_{ts}.json"

    return StreamingResponse(
        io.BytesIO(content.encode("utf-8")),
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            # 导出元数据（方案 18 O2）：前端回显行数与区间（rows 已按 ts 升序）
            "X-Export-Count": str(len(rows)),
            "X-Export-Oldest-Ts": str(rows[0]["ts"]),
            "X-Export-Newest-Ts": str(rows[-1]["ts"]),
        },
    )


@router.post("/{device_id}/record/start")
async def record_start(
    device_id: str,
    service: PerformanceService = Depends(get_performance_service),
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
    service: PerformanceService = Depends(get_performance_service),
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
    service: PerformanceService = Depends(get_performance_service),
) -> dict[str, Any]:
    """
    录制状态。

    返回：
        {recording, reason, rows, oldest_ts, newest_ts}；
        rows/oldest_ts/newest_ts 为跨批次聚合值。
    """
    return await service.record_status(device_id)
