"""
性能监控基础设施
================

提供设备性能数据采集功能：
- CPU 使用率
- 内存使用情况
- 帧率（FPS）
- 当前前台 Activity

使用方式：
    from app.infrastructure.performance import PerformanceSampler

    sampler = PerformanceSampler(adb_driver, device_id)
    async for metrics in sampler.sample():
        print(metrics.cpu_percent)
"""

from .sampler import PerformanceSampler, PerformanceMetrics

__all__ = ["PerformanceSampler", "PerformanceMetrics"]
