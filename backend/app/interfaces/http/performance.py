"""
性能监控 HTTP 端点
==================

层：接口层。

提供设备性能指标的 RESTful API：
- GET /api/perf/{device_id}/metrics  查询历史指标
- POST /api/perf/{device_id}/start   启动监控
- POST /api/perf/{device_id}/stop    停止监控

参考方案文档：方案/14-调试面板其他标签完善.md
"""

from fastapi import APIRouter, Depends, HTTPException

from app.application.performance_service import PerformanceService
from app.deps import get_performance_service
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/perf", tags=["performance"])


@router.get("/{device_id}/metrics")
async def get_metrics(
    device_id: str,
    limit: int = 100,
    service: PerformanceService = Depends(get_performance_service),
):
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
    if limit < 1 or limit > 3600:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 3600")

    metrics = await service.get_metrics(device_id, limit)
    return {"metrics": metrics, "count": len(metrics)}


@router.post("/{device_id}/start")
async def start_monitoring(
    device_id: str,
    interval: float = 1.0,
    service: PerformanceService = Depends(get_performance_service),
):
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
):
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
