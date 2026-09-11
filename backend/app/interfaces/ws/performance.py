"""
性能监控 WebSocket 端点
=======================

层：接口层。

提供实时性能指标推送：
- WS /ws/perf/{device_id}  实时推送性能指标

消息格式：
    服务端 → 客户端：
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

参考方案文档：方案/14-调试面板其他标签完善.md
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends

from app.application.performance_service import PerformanceService
from app.deps import get_performance_service
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["performance"])


@router.websocket("/ws/perf/{device_id}")
async def stream_metrics(
    websocket: WebSocket,
    device_id: str,
    service: PerformanceService = Depends(get_performance_service),
):
    """
    实时推送设备性能指标。

    流程：
        1. 接受 WebSocket 连接
        2. 自动启动性能监控（如果未启动）
        3. 持续推送指标数据（每秒 1 次）
        4. 客户端断开时清理

    参数：
        websocket: WebSocket 连接。
        device_id: 设备 ID（URL 路径参数）。
        service: 性能监控服务（依赖注入）。
    """
    await websocket.accept()
    logger.info("performance_ws_connected", device=device_id)

    try:
        # 实时推送指标
        async for metrics in service.stream_metrics(device_id):
            data = {
                "ts": metrics.ts,
                "cpu_percent": metrics.cpu_percent,
                "total_memory_mb": metrics.total_memory_mb,
                "used_memory_mb": metrics.used_memory_mb,
                "fps": metrics.fps,
                "jank_count": metrics.jank_count,
                "current_activity": metrics.current_activity,
                "top_package": metrics.top_package,
            }
            await websocket.send_json(data)

    except WebSocketDisconnect:
        logger.info("performance_ws_disconnected", device=device_id)
    except Exception as e:
        logger.error("performance_ws_error", device=device_id, error=str(e))
        try:
            await websocket.close(code=1011, reason=str(e))
        except Exception:
            pass
