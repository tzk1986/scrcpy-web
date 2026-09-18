"""
性能数据采集器
==============

层：基础设施层。

通过 ADB 命令采集 Android 设备的性能指标：
- CPU 使用率（基于 /proc/stat 差值计算）
- 内存使用情况（MemTotal - MemAvailable）
- 帧率（从 dumpsys gfxinfo 获取）
- 当前前台 Activity

参考：
- Android /proc 文件系统：https://www.kernel.org/doc/html/latest/filesystems/proc.html
- dumpsys 命令：https://developer.android.com/tools/dumpsys
"""

import asyncio
import re
import time
from dataclasses import dataclass
from typing import AsyncIterator

from app.core.exceptions import AdbError
from app.core.logging import get_logger
from app.domain.ports import AdbDriver

logger = get_logger(__name__)


@dataclass
class PerformanceMetrics:
    """性能指标快照。"""

    ts: float                 # Unix 时间戳（秒）
    cpu_percent: float        # CPU 使用率 0-100
    total_memory_mb: float    # 总内存 MB
    used_memory_mb: float     # 已用内存 MB
    fps: float | None         # 帧率（可选）
    jank_count: int           # 卡顿帧数
    current_activity: str     # 当前前台 Activity 组件名
    top_package: str          # 当前前台应用包名


class PerformanceSampler:
    """
    性能数据采集器。

    通过 ADB 命令周期性采集设备的 CPU、内存、帧率等指标。
    使用 async generator 模式，支持流式数据输出。

    采样治理（方案 17 实施项 3）：
        - activity/cpu/mem 并发采集（一轮耗时 = max(单命令) 而非 sum）
        - gfxinfo 最重，每 GFXINFO_INTERVAL 轮采集一次，中间轮沿用 _last_fps
        - 连续 MAX_FAILURES 次采样失败（AdbError/超时）视为设备失联，
          sample() 结束 → 上层监控任务自动清理
        - shell 单命令 SHELL_TIMEOUT 总超时兜底，防 dumpsys 挂起拖死循环

    使用示例：
        sampler = PerformanceSampler(adb, "192.168.1.100")
        async for metrics in sampler.sample(interval=1.0):
            print(f"CPU: {metrics.cpu_percent}%")
    """

    SHELL_TIMEOUT = 8.0       # shell 单命令超时兜底（秒）
    MAX_FAILURES = 5          # 连续采样失败上限，达到后停止该设备采样
    GFXINFO_INTERVAL = 5      # gfxinfo 降频间隔：每 N 轮采集一次

    def __init__(self, adb: AdbDriver, device_id: str):
        """
        初始化采样器。

        参数：
            adb: ADB 驱动实例。
            device_id: 设备 ID（ADB 序列号）。
        """
        self.adb = adb
        self.device_id = device_id
        self._round = 0                    # 采样轮次（gfxinfo 降频用）
        self._prev_cpu_times: tuple[int, int] | None = None  # (total, idle)
        self._prev_frames: int | None = None  # 上次采样的累计帧数
        self._prev_jank: int = 0              # 上次采样的累计卡顿帧数
        self._prev_ts: float | None = None    # 上次采样时间戳
        self._prev_package: str = ""          # 上次采样的应用包名
        self._last_fps: float | None = None   # 上次有效的 FPS 值（降频轮沿用）

    async def sample(self, interval: float = 1.0) -> AsyncIterator[PerformanceMetrics]:
        """
        周期性采集性能指标。

        连续 MAX_FAILURES 次采样失败（设备失联）后结束生成器，
        上层监控任务随之自动清理。成功采样会重置失败计数。

        参数：
            interval: 采样间隔（秒），默认 1 秒。

        产出：
            PerformanceMetrics 对象，包含所有性能指标。
        """
        failures = 0
        while True:
            try:
                metrics = await self._collect_once()
            except Exception as e:
                # getter 已吞解析类异常，到达这里的只有 shell 层失败
                # （AdbError / 超时），或未预期的 bug 异常——统一计数
                failures += 1
                logger.warning("performance_sample_failed", device=self.device_id,
                               error=str(e), failures=failures)
                if failures >= self.MAX_FAILURES:
                    logger.warning("performance_sampling_stopped_device_unreachable",
                                   device=self.device_id, failures=failures)
                    return
            else:
                failures = 0
                yield metrics

            await asyncio.sleep(interval)

    async def _collect_once(self) -> PerformanceMetrics:
        """采集一次完整的性能指标。"""
        now = time.time()
        self._round += 1

        # activity/cpu/mem 互不依赖，并发采集；fps 依赖 activity 的 package，
        # 且 gfxinfo 最重按轮次降频
        (activity, package), cpu, (mem_total, mem_used) = await asyncio.gather(
            self._get_current_activity(),
            self._get_cpu_usage(),
            self._get_memory_usage(),
        )
        if self._round % self.GFXINFO_INTERVAL == 1:
            fps, jank = await self._get_fps_and_jank(now, package)
        else:
            fps, jank = self._last_fps, 0

        return PerformanceMetrics(
            ts=now,
            cpu_percent=cpu,
            total_memory_mb=mem_total,
            used_memory_mb=mem_used,
            fps=fps,
            jank_count=jank,
            current_activity=activity,
            top_package=package,
        )

    async def _shell(self, cmd: str) -> str:
        """shell 调用统一入口：施加总超时兜底，防单命令挂起拖死采样循环。"""
        return await asyncio.wait_for(
            self.adb.shell(self.device_id, cmd), timeout=self.SHELL_TIMEOUT
        )

    async def _get_cpu_usage(self) -> float:
        """
        获取 CPU 使用率（基于 /proc/stat 差值计算）。

        原理：
            /proc/stat 第一行格式：cpu  user nice system idle iowait irq softirq ...
            两次采样差值计算：
                total_diff = sum(所有字段差值)
                idle_diff = idle 差值
                cpu_usage = (total_diff - idle_diff) / total_diff * 100

        返回：
            CPU 使用率百分比（0-100）。
        """
        try:
            output = await self._shell("cat /proc/stat")

            # 解析第一行：cpu  user nice system idle ...
            match = re.search(r"^cpu\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)", output, re.MULTILINE)
            if not match:
                return 0.0

            user, nice, system, idle = map(int, match.groups())
            total = user + nice + system + idle

            if self._prev_cpu_times is None:
                # 首次采样，无法计算差值
                self._prev_cpu_times = (total, idle)
                return 0.0

            prev_total, prev_idle = self._prev_cpu_times
            self._prev_cpu_times = (total, idle)

            total_diff = total - prev_total
            idle_diff = idle - prev_idle

            if total_diff == 0:
                return 0.0

            cpu_percent = (total_diff - idle_diff) / total_diff * 100
            return round(cpu_percent, 1)

        except (AdbError, asyncio.TimeoutError):
            raise  # 通信失败向上传播，由 sample() 统一计数停采
        except Exception as e:
            logger.warning("get_cpu_usage_failed", device=self.device_id, error=str(e))
            return 0.0

    async def _get_memory_usage(self) -> tuple[float, float]:
        """
        获取内存使用情况。

        从 /proc/meminfo 读取 MemTotal 和 MemAvailable。

        返回：
            (total_mb, used_mb) 元组，单位 MB。
        """
        try:
            output = await self._shell("cat /proc/meminfo")

            total_kb = 0
            available_kb = 0

            for line in output.splitlines():
                if line.startswith("MemTotal:"):
                    match = re.search(r"MemTotal:\s+(\d+)", line)
                    if match:
                        total_kb = int(match.group(1))
                elif line.startswith("MemAvailable:"):
                    match = re.search(r"MemAvailable:\s+(\d+)", line)
                    if match:
                        available_kb = int(match.group(1))

            total_mb = total_kb / 1024
            used_mb = (total_kb - available_kb) / 1024

            return round(total_mb, 1), round(used_mb, 1)

        except (AdbError, asyncio.TimeoutError):
            raise  # 通信失败向上传播，由 sample() 统一计数停采
        except Exception as e:
            logger.warning("get_memory_usage_failed", device=self.device_id, error=str(e))
            return 0.0, 0.0

    async def _get_fps_and_jank(self, now: float, package: str) -> tuple[float | None, int]:
        """
        获取帧率和卡顿帧数。

        从 dumpsys gfxinfo 获取当前 Activity 的累计帧数，
        然后与上次采样差值计算实时 FPS。

        参数：
            now: 当前时间戳（秒）。
            package: 当前前台应用包名（由 _collect_once 消除重复查询后传入）。

        返回：
            (fps, jank_delta) 元组。
            fps 可能为 None（首次采样或无法获取时）。
            jank_delta 是本周期内的卡顿帧增量。
        """
        try:
            if not package:
                return None, 0

            # 获取该应用 gfxinfo（不用 grep，避免二进制输出问题）
            output = await self._shell(f"dumpsys gfxinfo {package}")

            # 解析累计帧数
            frames_match = re.search(r"Total frames rendered:\s*(\d+)", output)
            jank_match = re.search(r"Janky frames:\s*(\d+)", output)

            if not frames_match:
                return None, 0

            total_frames = int(frames_match.group(1))
            total_jank = int(jank_match.group(1)) if jank_match else 0

            # 计算实时 FPS 和卡顿增量
            fps: float | None = None
            jank_delta = 0
            if self._prev_frames is not None and self._prev_ts is not None:
                elapsed = now - self._prev_ts
                if elapsed > 0:
                    frame_delta = total_frames - self._prev_frames
                    # 检测应用切换（包名变化）或计数器重置
                    if self._prev_package != package:
                        # 应用切换，重置基准，返回上次有效 FPS 作为过渡
                        fps = self._last_fps
                    elif frame_delta < 0 or frame_delta > 10000:
                        # 计数器异常，重置基准
                        fps = self._last_fps
                    else:
                        fps = round(frame_delta / elapsed, 1)
                        self._last_fps = fps  # 记录有效 FPS
                        jank_delta = max(0, total_jank - self._prev_jank)

            self._prev_frames = total_frames
            self._prev_jank = total_jank
            self._prev_ts = now
            self._prev_package = package

            return fps, jank_delta

        except (AdbError, asyncio.TimeoutError):
            raise  # 通信失败向上传播，由 sample() 统一计数停采
        except Exception as e:
            logger.warning("get_fps_failed", device=self.device_id, error=str(e))
            return None, 0

    async def _get_current_activity(self) -> tuple[str, str]:
        """
        获取当前前台 Activity。

        从 dumpsys activity activities 获取 mResumedActivity。

        返回：
            (activity_name, package_name) 元组。
        """
        try:
            output = await self._shell(
                "dumpsys activity activities | grep mResumedActivity"
            )

            # 示例输出：
            # mResumedActivity: ActivityRecord{abc123 u0 com.example.app/.MainActivity t123}
            match = re.search(r"mResumedActivity:.*?(\S+)/(\S+)", output)
            if match:
                package = match.group(1)
                activity = match.group(2)
                return f"{package}/{activity}", package

            return "", ""

        except (AdbError, asyncio.TimeoutError):
            raise  # 通信失败向上传播，由 sample() 统一计数停采
        except Exception as e:
            logger.warning("get_current_activity_failed", device=self.device_id, error=str(e))
            return "", ""
