"""
网络录制状态机测试（NetworkService，方案 18 §3.6 B3）
====================================================

覆盖录制状态机各转移（§3.6 表逐行）：
    - record_start（监控未运行）→ 拉起采样循环
    - record_start 幂等（已录制返回现状态，不重复挂录制器）
    - 录制批写落盘（后台 MetricRecorder 批量写）
    - record_stop → 停采 + final flush + reason=stopped + rows 回显
    - 设备失联 → stopped(device_lost)（缓冲释放）
    - metrics.recording 热重载 true→false → 停录停采 stopped(config)
    - metrics.recording=false 时 record_start → RecordingDisabledError
    - cleanup → stopped(shutdown)
    - 重启（新实例同仓储）→ recording:false、聚合行数保留
    - export_cached 查询前 flush 批写缓冲（S14 边界）
"""

import asyncio
import time

import pytest

from app.application.network_service import NetworkService
from app.core.config import settings
from app.core.exceptions import AdbError, RecordingDisabledError
from app.infrastructure.network.sampler import NetworkStats

DEV = "net-rec-dev"

# 采样循环所需的 /proc 输出（tcp/tcp6/udp 均为空表 → 0 连接）
DEV_LINE = " wlan0: 500000 12 0 0 0 0 0 0 600000 13 0 0 0 0 0 0\n"
WIFI_LINE = "Wi-Fi is enabled\nmWifiInfo SSID: MyNet, BSSID: aa:bb:cc:dd:ee:ff\n"


def std_outputs() -> dict[str, str]:
    return {
        "cat /proc/net/dev": DEV_LINE,
        "cat /proc/net/tcp": "",
        "cat /proc/net/tcp6": "",
        "cat /proc/net/udp": "",
        "dumpsys wifi": WIFI_LINE,
    }


class FakeAdb:
    """按命令前缀返回固定输出；failures 注入失联。"""

    def __init__(self, outputs: dict[str, str] | None = None):
        self.outputs = dict(outputs or {})
        self.never_fail = True

    async def shell(self, device_id: str, cmd: str) -> str:
        if not self.never_fail and cmd.startswith("cat /proc/net/dev"):
            raise AdbError("ADB command failed: device offline")
        for prefix, out in sorted(self.outputs.items(), key=lambda kv: -len(kv[0])):
            if cmd.startswith(prefix):
                return out
        return ""


def make_stats(**overrides: object) -> NetworkStats:
    fields: dict[str, object] = {
        "ts": time.time(),
        "rx_bytes": 500000,
        "tx_bytes": 600000,
        "rx_rate_kbps": 0.0,
        "tx_rate_kbps": 0.0,
        "active_connections": 0,
        "wifi_connected": True,
        "wifi_ssid": "MyNet",
    }
    fields.update(overrides)
    return NetworkStats(**fields)


class FakeMetricsRepo:
    """内存版指标仓储：捕获批量写入，支持 stats 聚合与区间查询。"""

    def __init__(self) -> None:
        self.perf_rows: list[tuple[object, ...]] = []
        self.network_rows: list[tuple[object, ...]] = []

    async def save_perf_samples_bulk(self, rows: list[tuple[object, ...]]) -> None:
        self.perf_rows.extend(rows)

    async def save_network_samples_bulk(self, rows: list[tuple[object, ...]]) -> None:
        self.network_rows.extend(rows)

    async def network_sample_stats(self, device_id: str) -> tuple[int, float | None, float | None]:
        rows = [r for r in self.network_rows if r[0] == device_id]
        if not rows:
            return (0, None, None)
        tss = [r[1] for r in rows]
        return (len(rows), min(tss), max(tss))

    async def perf_sample_stats(self, device_id: str) -> tuple[int, float | None, float | None]:
        return (0, None, None)

    async def query_network_samples(
        self,
        device_id: str,
        from_ts: float | None = None,
        to_ts: float | None = None,
        limit: int = 50000,
    ) -> list[dict[str, object]]:
        rows = [r for r in self.network_rows if r[0] == device_id]
        if from_ts is not None:
            rows = [r for r in rows if r[1] >= from_ts]
        if to_ts is not None:
            rows = [r for r in rows if r[1] <= to_ts]
        cols = ("device_id", "ts", "rx_bytes", "tx_bytes", "rx_rate_kbps",
                "tx_rate_kbps", "active_connections", "wifi_connected", "wifi_ssid")
        return [dict(zip(cols, r)) for r in sorted(rows, key=lambda r: r[1])[:limit]]


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
def fast_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """采样循环间隔压缩，测试不依赖真实等待（0 会触发 ProactorEventLoop
    sleep 快进陷阱，见 wait_until 说明）。"""
    monkeypatch.setattr(settings().metrics, "network_interval", 0.001)


@pytest.fixture
def recording_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """确保录制总开关打开（默认配置即 true，防护其他用例 monkeypatch 泄漏）。"""
    monkeypatch.setattr(settings().metrics, "recording", True)


async def test_record_start_raises_sampling_loop(recording_enabled, fast_loop) -> None:
    """监控未运行时 record_start 拉起采样循环（§3.6 第一行）。"""
    adb = FakeAdb(std_outputs())
    repo = FakeMetricsRepo()
    service = NetworkService(adb, metrics_repo=repo)

    status = await service.record_start(DEV)
    try:
        assert status["recording"] is True
        assert DEV in service._tasks
        await wait_until(lambda: repo.network_rows, timeout=1.5)
    finally:
        await service.record_stop(DEV)


async def test_record_start_idempotent(recording_enabled, fast_loop) -> None:
    """已录制时 record_start 返回现状态，不重复挂录制器。"""
    service = NetworkService(FakeAdb(std_outputs()), metrics_repo=FakeMetricsRepo())

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
    recording_enabled, fast_loop
) -> None:
    """录制批写落盘；record_stop → final flush 无丢失 + 停采释放。"""
    repo = FakeMetricsRepo()
    service = NetworkService(FakeAdb(std_outputs()), metrics_repo=repo)

    await service.record_start(DEV)
    await wait_until(lambda: len(repo.network_rows) >= 5, timeout=1.5)
    written_before = len(repo.network_rows)

    stopped = await service.record_stop(DEV)

    assert stopped["recording"] is False
    assert stopped["reason"] == "stopped"
    assert stopped["rows"] >= 5
    # final flush 后落库行数等于回显行数（无丢失，S14）
    assert len(repo.network_rows) == stopped["rows"] >= written_before
    assert DEV not in service._tasks
    assert DEV not in service._buffers
    assert DEV not in service._recorders
    assert DEV not in service._samplers


async def test_device_loss_finalizes_recording_device_lost(
    recording_enabled, fast_loop
) -> None:
    """设备失联 → 采样循环自然结束 → stopped(device_lost)（§3.6）。"""
    adb = FakeAdb(std_outputs())
    repo = FakeMetricsRepo()
    service = NetworkService(adb, metrics_repo=repo)

    await service.record_start(DEV)
    await wait_until(lambda: repo.network_rows, timeout=1.5)

    adb.never_fail = False  # 设备失联
    await wait_until(lambda: DEV not in service._tasks, timeout=3.0)

    status = await service.record_status(DEV)
    assert status["recording"] is False
    assert status["reason"] == "device_lost"
    assert status["rows"] > 0
    assert DEV not in service._buffers  # 缓冲释放（B1）


async def test_hot_reload_disable_stops_recording_config(
    recording_enabled, fast_loop, monkeypatch: pytest.MonkeyPatch
) -> None:
    """录制中热重载 recording true→false → 停录停采 stopped(config)（§3.6）。"""
    repo = FakeMetricsRepo()
    service = NetworkService(FakeAdb(std_outputs()), metrics_repo=repo)

    await service.record_start(DEV)
    await wait_until(lambda: repo.network_rows, timeout=1.5)

    monkeypatch.setattr(settings().metrics, "recording", False)
    await wait_until(lambda: DEV not in service._tasks, timeout=2.0)

    status = await service.record_status(DEV)
    assert status["recording"] is False
    assert status["reason"] == "config"
    assert DEV not in service._buffers


async def test_record_start_disabled_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """metrics.recording=false 时 record_start 抛 RecordingDisabledError（不静默）。"""
    monkeypatch.setattr(settings().metrics, "recording", False)
    service = NetworkService(FakeAdb(std_outputs()), metrics_repo=FakeMetricsRepo())

    with pytest.raises(RecordingDisabledError):
        await service.record_start(DEV)

    assert DEV not in service._tasks
    assert DEV not in service._recorders
    assert (await service.record_status(DEV))["recording"] is False


async def test_record_status_flushes_buffer(recording_enabled, fast_loop) -> None:
    """录制中 status 先 flush 批写缓冲，行数与区间口径准确。"""
    repo = FakeMetricsRepo()
    service = NetworkService(FakeAdb(std_outputs()), metrics_repo=repo)

    await service.record_start(DEV)
    await wait_until(lambda: repo.network_rows, timeout=1.5)

    status = await service.record_status(DEV)

    assert status["recording"] is True
    assert status["reason"] is None
    assert status["rows"] == len(repo.network_rows)
    assert status["oldest_ts"] is not None
    assert status["newest_ts"] is not None
    assert status["newest_ts"] >= status["oldest_ts"]


async def test_cleanup_stops_recording_shutdown(recording_enabled, fast_loop) -> None:
    """服务关闭 cleanup → stopped(shutdown) + 缓冲释放（§3.6）。"""
    repo = FakeMetricsRepo()
    service = NetworkService(FakeAdb(std_outputs()), metrics_repo=repo)

    await service.record_start(DEV)
    await wait_until(lambda: repo.network_rows, timeout=1.5)

    await service.cleanup()

    assert DEV not in service._tasks
    assert DEV not in service._buffers
    assert DEV not in service._recorders
    status = await service.record_status(DEV)
    assert status["recording"] is False
    assert status["reason"] == "shutdown"
    assert status["rows"] > 0


async def test_restart_reports_not_recording_with_aggregate_rows(
    recording_enabled, fast_loop
) -> None:
    """进程重启（新实例同仓储）→ recording:false、聚合行数保留（S13）。"""
    repo = FakeMetricsRepo()
    first = NetworkService(FakeAdb(std_outputs()), metrics_repo=repo)
    await first.record_start(DEV)
    await wait_until(lambda: len(repo.network_rows) >= 3, timeout=1.5)
    await first.record_stop(DEV)

    restarted = NetworkService(FakeAdb(std_outputs()), metrics_repo=repo)
    status = await restarted.record_status(DEV)

    assert status["recording"] is False
    assert status["rows"] == len(repo.network_rows) >= 3


async def test_export_cached_flushes_pending_batch(recording_enabled, fast_loop) -> None:
    """source=cache 查询前 flush 批写缓冲：未落盘点也被导出（S14）。"""
    repo = FakeMetricsRepo()
    service = NetworkService(FakeAdb(std_outputs()), metrics_repo=repo)

    await service.record_start(DEV)
    await wait_until(lambda: service._buffers, timeout=1.5)

    # 直接向录制器注入一个未到批的点（模拟刚采样未落库）
    marker = make_stats(ts=42.0)
    service._recorders[DEV].submit_network(DEV, marker)

    rows = await service.export_cached(DEV)

    assert any(r["ts"] == 42.0 for r in rows)  # flush 后查询可见


async def test_export_cached_range_filter(recording_enabled, fast_loop) -> None:
    """区间过滤与 flush 挂接后查询参数透传仓储。"""
    repo = FakeMetricsRepo()
    service = NetworkService(FakeAdb(std_outputs()), metrics_repo=repo)

    await service.record_start(DEV)
    await wait_until(lambda: len(repo.network_rows) >= 3, timeout=1.5)
    current = [r[1] for r in repo.network_rows]
    lo, hi = min(current), max(current)

    rows = await service.export_cached(DEV, from_ts=lo, to_ts=hi, limit=2)

    assert 1 <= len(rows) <= 2
    assert all(lo <= r["ts"] <= hi for r in rows)