"""
性能录制状态机测试（PerformanceService，方案 18 §3.6 B3）
========================================================

覆盖录制状态机各转移（§3.6 表逐行，perf 侧）：
    - record_start（监控未运行）→ 拉起采样循环
    - record_start 幂等（已录制返回现状态，不重复挂录制器）
    - 录制批写落盘（后台 MetricRecorder 批量写）
    - record_stop → 停采 + final flush + reason=stopped + rows 回显
    - 设备失联（sample 自然结束）→ stopped(device_lost)（缓冲释放）
    - metrics.recording 热重载 true→false → 停录停采 stopped(config)
    - metrics.recording=false 时 record_start → RecordingDisabledError
    - cleanup → stopped(shutdown)
    - 重启（新实例同仓储）→ recording:false、聚合行数保留（S13）
    - export_cached 查询前 flush 批写缓冲（S14 边界）

采样间隔经 start_monitoring(interval) 传入（不读配置），用例先以
interval=0.001 显式启动监控再开启录制，测试不依赖真实等待。
（interval=0 会形成 sleep(0) busy 循环，触发 Windows ProactorEventLoop
的 sleep 快进陷阱，见 wait_until 说明。）
"""

import asyncio
import json
import time

import pytest

from app.application.performance_service import PerformanceService
from app.core.config import settings
from app.core.exceptions import AdbError, RecordingDisabledError
from app.domain.runtime import CpuProcessInfo
from app.domain.runtime import PerformanceMetrics
from app.application.metric_recorder import MetricRecorder

DEV = "perf-rec-dev"

# PerformanceSampler 所需 shell 输出
PROC_STAT = "cpu  2000 100 50 3800 10 5 0 0 0 0\n"
MEMINFO = "MemTotal:        2097152 kB\nMemAvailable:    1048576 kB\n"
ACTIVITY = "mResumedActivity: ActivityRecord{a1b2c3 u0 com.example.app/.MainActivity t42}\n"
GFXINFO = "Total frames rendered: 1000\nJanky frames: 5\n"


def std_outputs() -> dict[str, str]:
    return {
        "cat /proc/stat": PROC_STAT,
        "cat /proc/meminfo": MEMINFO,
        "dumpsys activity activities": ACTIVITY,
        "dumpsys gfxinfo com.example.app": GFXINFO,
    }


class FakeAdb:
    """按命令前缀返回固定输出；never_fail=False 时全失败（模拟失联）。"""

    def __init__(self, outputs: dict[str, str] | None = None):
        self.outputs = dict(outputs or {})
        self.never_fail = True
        self.calls = 0

    async def shell(self, device_id: str, cmd: str) -> str:
        self.calls += 1
        if not self.never_fail:
            raise AdbError("ADB command failed: device offline")
        for prefix, out in sorted(self.outputs.items(), key=lambda kv: -len(kv[0])):
            if cmd.startswith(prefix):
                return out
        return ""


class FakeMetricsRepo:
    """内存版指标仓储：捕获批量写入，支持 stats 聚合与区间查询。"""

    def __init__(self) -> None:
        self.perf_rows: list[tuple[object, ...]] = []
        self.network_rows: list[tuple[object, ...]] = []

    async def save_perf_samples_bulk(self, rows: list[tuple[object, ...]]) -> None:
        self.perf_rows.extend(rows)

    async def save_network_samples_bulk(self, rows: list[tuple[object, ...]]) -> None:
        self.network_rows.extend(rows)

    async def perf_sample_stats(self, device_id: str) -> tuple[int, float | None, float | None]:
        rows = [r for r in self.perf_rows if r[0] == device_id]
        if not rows:
            return (0, None, None)
        tss = [r[1] for r in rows]
        return (len(rows), min(tss), max(tss))

    async def network_sample_stats(self, device_id: str) -> tuple[int, float | None, float | None]:
        return (0, None, None)

    async def query_perf_samples(
        self,
        device_id: str,
        from_ts: float | None = None,
        to_ts: float | None = None,
        limit: int = 50000,
    ) -> list[dict[str, object]]:
        rows = [r for r in self.perf_rows if r[0] == device_id]
        if from_ts is not None:
            rows = [r for r in rows if r[1] >= from_ts]
        if to_ts is not None:
            rows = [r for r in rows if r[1] <= to_ts]
        cols = ("device_id", "ts", "cpu_percent", "total_memory_mb", "used_memory_mb",
                "fps", "jank_count", "current_activity", "top_package",
                "cpu_processes", "battery_temp", "battery_voltage", "battery_health")
        # 与真实仓储语义对齐：短行（旧 9 元组缓存行）尾部补 None
        # （SQLite ALTER TABLE ADD COLUMN 后旧行新列为 NULL）
        out: list[dict[str, object]] = []
        for r in sorted(rows, key=lambda r: r[1])[:limit]:
            padded = list(r) + [None] * (len(cols) - len(r))
            out.append(dict(zip(cols, padded)))
        return out


async def wait_until(predicate, timeout: float = 3.0) -> None:
    """真时轮询等待条件成立（避免依赖任务调度时序的竞态）。

    Windows ProactorEventLoop 上存在 busy 采样任务（interval≈0 的
    sleep 循环）时，asyncio.sleep(delay) 的定时器等待会被快进（每次
    _run_once 即返回），sleep(0.01)×150 实测仅 9ms 真时——定时器式
    等待语义失效、0.1s 批写 flush 等不到。改用 sleep(0) 让渡 +
    time.monotonic 把关，等待时长由真时保证。
    """
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("wait_until timeout")
        await asyncio.sleep(0)


@pytest.fixture
def recording_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """确保录制总开关打开（防护其他用例 monkeypatch 泄漏）。"""
    monkeypatch.setattr(settings().metrics, "recording", True)


def make_marker(ts: float = 42.0) -> PerformanceMetrics:
    return PerformanceMetrics(
        ts=ts, cpu_percent=1.0, total_memory_mb=2048.0, used_memory_mb=1024.0,
        fps=60.0, jank_count=0, current_activity="com.example/.MainActivity",
        top_package="com.example",
    )


async def test_record_start_raises_sampling_loop(recording_enabled) -> None:
    """监控未运行时 record_start 拉起采样循环（§3.6 第一行）。"""
    repo = FakeMetricsRepo()
    service = PerformanceService(FakeAdb(std_outputs()), metrics_repo=repo)

    status = await service.record_start(DEV)
    try:
        assert status["recording"] is True
        assert DEV in service._tasks
        assert DEV in service._buffers
        # 采样循环首轮立即产出，后台批写 0.1s 落库
        await wait_until(lambda: repo.perf_rows, timeout=2)
    finally:
        await service.record_stop(DEV)


async def test_record_start_idempotent(recording_enabled) -> None:
    """已录制时 record_start 返回现状态，不重复挂录制器。"""
    service = PerformanceService(FakeAdb(std_outputs()), metrics_repo=FakeMetricsRepo())

    first = await service.record_start(DEV)
    recorder = service._recorders[DEV]
    second = await service.record_start(DEV)

    try:
        assert first["recording"] is True
        assert second["recording"] is True
        assert service._recorders[DEV] is recorder
        assert len(service._recorders) == 1
    finally:
        await service.record_stop(DEV)


async def test_recording_batches_persist_and_stop_final_flush(
    recording_enabled
) -> None:
    """录制批写落盘；record_stop → final flush 无丢失 + 停采释放。"""
    repo = FakeMetricsRepo()
    service = PerformanceService(FakeAdb(std_outputs()), metrics_repo=repo)

    await service.start_monitoring(DEV, interval=0.001)
    await service.record_start(DEV)
    await wait_until(lambda: len(repo.perf_rows) >= 5, timeout=1.5)

    stopped = await service.record_stop(DEV)

    assert stopped["recording"] is False
    assert stopped["reason"] == "stopped"
    assert stopped["rows"] >= 5
    # final flush 后落库行数等于回显行数（无丢失，S14）
    assert len(repo.perf_rows) == stopped["rows"]
    assert DEV not in service._tasks
    assert DEV not in service._buffers
    assert DEV not in service._recorders
    assert DEV not in service._samplers


async def test_device_loss_finalizes_recording_device_lost(
    recording_enabled
) -> None:
    """设备失联 → sample 自然结束 → stopped(device_lost) 且缓冲释放。"""
    adb = FakeAdb(std_outputs())
    repo = FakeMetricsRepo()
    service = PerformanceService(adb, metrics_repo=repo)

    await service.start_monitoring(DEV, interval=0.001)
    await service.record_start(DEV)
    await wait_until(lambda: repo.perf_rows, timeout=1.5)

    adb.never_fail = False  # 设备失联：MAX_FAILURES 次连续失败后结束
    await wait_until(lambda: DEV not in service._tasks, timeout=3.0)

    status = await service.record_status(DEV)
    assert status["recording"] is False
    assert status["reason"] == "device_lost"
    assert status["rows"] > 0
    assert DEV not in service._buffers  # B1：失联同样释放暂存


async def test_hot_reload_disable_stops_recording_config(
    recording_enabled, monkeypatch: pytest.MonkeyPatch
) -> None:
    """录制中热重载 recording true→false → 停录停采 stopped(config)。"""
    repo = FakeMetricsRepo()
    service = PerformanceService(FakeAdb(std_outputs()), metrics_repo=repo)

    await service.start_monitoring(DEV, interval=0.001)
    await service.record_start(DEV)
    await wait_until(lambda: repo.perf_rows, timeout=1.5)

    monkeypatch.setattr(settings().metrics, "recording", False)
    await wait_until(lambda: DEV not in service._tasks, timeout=2.0)

    status = await service.record_status(DEV)
    assert status["recording"] is False
    assert status["reason"] == "config"
    assert DEV not in service._buffers


async def test_record_start_disabled_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """metrics.recording=false 时 record_start 抛 RecordingDisabledError（不静默）。"""
    monkeypatch.setattr(settings().metrics, "recording", False)
    service = PerformanceService(FakeAdb(std_outputs()), metrics_repo=FakeMetricsRepo())

    with pytest.raises(RecordingDisabledError):
        await service.record_start(DEV)

    assert DEV not in service._tasks
    assert DEV not in service._recorders
    assert (await service.record_status(DEV))["recording"] is False


async def test_record_status_flushes_buffer(recording_enabled) -> None:
    """录制中 status 先 flush 批写缓冲，行数与区间口径准确。"""
    repo = FakeMetricsRepo()
    service = PerformanceService(FakeAdb(std_outputs()), metrics_repo=repo)

    await service.start_monitoring(DEV, interval=0.001)
    await service.record_start(DEV)
    await wait_until(lambda: repo.perf_rows, timeout=1.5)

    status = await service.record_status(DEV)

    assert status["recording"] is True
    assert status["reason"] is None
    assert status["rows"] == len(repo.perf_rows)
    assert status["oldest_ts"] is not None
    assert status["newest_ts"] is not None
    assert status["newest_ts"] >= status["oldest_ts"]
    await service.record_stop(DEV)


async def test_cleanup_stops_recording_shutdown(recording_enabled) -> None:
    """服务关闭 cleanup → stopped(shutdown) + 缓冲释放。"""
    repo = FakeMetricsRepo()
    service = PerformanceService(FakeAdb(std_outputs()), metrics_repo=repo)

    await service.start_monitoring(DEV, interval=0.001)
    await service.record_start(DEV)
    await wait_until(lambda: repo.perf_rows, timeout=1.5)

    await service.cleanup()

    assert DEV not in service._tasks
    assert DEV not in service._buffers
    assert DEV not in service._recorders
    status = await service.record_status(DEV)
    assert status["recording"] is False
    assert status["reason"] == "shutdown"
    assert status["rows"] > 0


async def test_restart_reports_not_recording_with_aggregate_rows(
    recording_enabled
) -> None:
    """进程重启（新实例同仓储）→ recording:false、聚合行数保留（S13）。"""
    repo = FakeMetricsRepo()
    first = PerformanceService(FakeAdb(std_outputs()), metrics_repo=repo)
    await first.start_monitoring(DEV, interval=0.001)
    await first.record_start(DEV)
    await wait_until(lambda: len(repo.perf_rows) >= 3, timeout=1.5)
    await first.record_stop(DEV)

    restarted = PerformanceService(FakeAdb(std_outputs()), metrics_repo=repo)
    status = await restarted.record_status(DEV)

    assert status["recording"] is False
    assert status["rows"] == len(repo.perf_rows) >= 3


async def test_export_cached_flushes_pending_batch(recording_enabled) -> None:
    """source=cache 查询前 flush 批写缓冲：未落盘点也被导出（S14）。"""
    repo = FakeMetricsRepo()
    service = PerformanceService(FakeAdb(std_outputs()), metrics_repo=repo)

    await service.start_monitoring(DEV, interval=0.001)
    await service.record_start(DEV)
    await wait_until(lambda: service._buffers, timeout=1.5)

    # 直接向录制器注入一个未到批的点（模拟刚采样未落库）
    service._recorders[DEV].submit_perf(DEV, make_marker(ts=42.0))

    rows = await service.export_cached(DEV)

    assert any(r["ts"] == 42.0 for r in rows)  # flush 后查询可见
    await service.record_stop(DEV)


async def test_export_cached_range_filter(recording_enabled) -> None:
    """区间过滤与 flush 挂接后查询参数透传仓储。"""
    repo = FakeMetricsRepo()
    service = PerformanceService(FakeAdb(std_outputs()), metrics_repo=repo)

    await service.start_monitoring(DEV, interval=0.001)
    await service.record_start(DEV)
    await wait_until(lambda: len(repo.perf_rows) >= 3, timeout=1.5)
    current = [r[1] for r in repo.perf_rows]
    lo, hi = min(current), max(current)

    rows = await service.export_cached(DEV, from_ts=lo, to_ts=hi, limit=2)

    assert 1 <= len(rows) <= 2
    assert all(lo <= r["ts"] <= hi for r in rows)
    await service.record_stop(DEV)


# ---------------------------------------------------------------------------
# 方案 37 D4：缓存链路扩列（录制器序列化 + export_cached 回读）
# ---------------------------------------------------------------------------

def make_cached_row(
    ts: float = 1.0,
    cpu_processes: object = '[{"proc": "init", "user": 0.5, "kernel": 0.1}]',
    battery_temp: object = 31.0,
    battery_voltage: object = 4200,
    battery_health: object = "GOOD",
) -> tuple[object, ...]:
    """模拟 SQLite 缓存行：cpu_processes 为 JSON 串（TEXT 列存法）。"""
    return (DEV, ts, 1.0, 2048.0, 1024.0, 60.0, 0,
            "com.example/.MainActivity", "com.example",
            cpu_processes, battery_temp, battery_voltage, battery_health)


async def test_submit_perf_serializes_new_fields() -> None:
    """submit_perf 落库前 cpu_processes 序列化为 JSON 数组串；电池直存。"""
    repo = FakeMetricsRepo()
    recorder = MetricRecorder(repo)
    m = PerformanceMetrics(
        ts=42.0, cpu_percent=1.0, total_memory_mb=2048.0, used_memory_mb=1024.0,
        fps=60.0, jank_count=0, current_activity="com.example/.MainActivity",
        top_package="com.example",
        cpu_processes=[CpuProcessInfo("init", 0.5, 0.1),
                       CpuProcessInfo("kworker/0:1", 1.2, 0.3)],
        battery_temp=31.0, battery_voltage=4200, battery_health="GOOD",
    )
    recorder.submit_perf(DEV, m)
    await recorder.flush()

    row = repo.perf_rows[0]
    assert len(row) == 13
    # 旧 9 元组前缀零变化
    assert row[:9] == (DEV, 42.0, 1.0, 2048.0, 1024.0, 60.0, 0,
                       "com.example/.MainActivity", "com.example")
    assert row[9] == json.dumps([
        {"proc": "init", "user": 0.5, "kernel": 0.1},
        {"proc": "kworker/0:1", "user": 1.2, "kernel": 0.3},
    ])
    assert row[10] == 31.0
    assert row[11] == 4200
    assert row[12] == "GOOD"


async def test_submit_perf_empty_processes_and_none_battery() -> None:
    """空进程表 → "[]"；battery None 也直存（NULL 诚实空值）。"""
    repo = FakeMetricsRepo()
    recorder = MetricRecorder(repo)
    recorder.submit_perf(DEV, make_marker(ts=7.0))
    await recorder.flush()

    row = repo.perf_rows[0]
    assert row[9] == "[]"
    assert row[10] is None
    assert row[11] is None
    assert row[12] == "UNKNOWN"


async def test_export_cached_parses_cpu_processes_json_to_list() -> None:
    """cache 源 cpu_processes JSON 串 → 与 buffer 源同形态的 list；电池直通。"""
    repo = FakeMetricsRepo()
    repo.perf_rows.append(make_cached_row())
    service = PerformanceService(FakeAdb(std_outputs()), metrics_repo=repo)

    rows = await service.export_cached(DEV)

    assert rows[0]["cpu_processes"] == [{"proc": "init", "user": 0.5, "kernel": 0.1}]
    assert rows[0]["battery_temp"] == 31.0
    assert rows[0]["battery_voltage"] == 4200
    assert rows[0]["battery_health"] == "GOOD"


async def test_export_cached_bad_cpu_processes_data_tolerated() -> None:
    """坏 JSON / JSON 非数组 / None / 非 str / 旧 9 元组行 → [] 容错，不炸导出。"""
    repo = FakeMetricsRepo()
    repo.perf_rows.append(make_cached_row(ts=1.0, cpu_processes="not-json"))
    repo.perf_rows.append(make_cached_row(ts=2.0, cpu_processes='{"proc": 1}'))
    repo.perf_rows.append(make_cached_row(ts=3.0, cpu_processes=None))
    repo.perf_rows.append(make_cached_row(ts=4.0, cpu_processes=123))
    repo.perf_rows.append((DEV, 5.0, 1.0, 2048.0, 1024.0, 60.0, 0, "a", "p"))  # 旧 9 元组
    service = PerformanceService(FakeAdb(std_outputs()), metrics_repo=repo)

    rows = await service.export_cached(DEV)

    assert [r["ts"] for r in rows] == [1.0, 2.0, 3.0, 4.0, 5.0]
    assert all(r["cpu_processes"] == [] for r in rows)
    # 旧行新列 NULL（补 None 后）→ JSON 导出为 null 的诚实空值
    assert rows[4]["battery_temp"] is None
    assert rows[4]["battery_health"] is None
