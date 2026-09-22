"""
网络监控服务测试（application/network_service.py，方案 18 Step 1-2）
====================================================================

覆盖：
    - start_monitoring 幂等；采样循环写入缓冲
    - 失联判定：连续 lost_failures 次 strict 采样失败 → 任务结束 + 缓冲释放
    - 单次失败（低于阈值）继续采样
    - stop_monitoring 释放缓冲、幂等
    - get_stats：新鲜点复用 / 冷启动自动拉起 + 内联采样 / 不新鲜内联采样
    - get_connections：即时快照、不拉起任务
    - 空闲守卫：闲置 → idle_ttl 后停采释放；持续访问续期；录制中禁停
    - cleanup 全设备停采释放
"""

import asyncio
import time
from collections import deque

import pytest

from app.application.network_service import NetworkService
from app.core.config import settings
from app.core.exceptions import AdbError
from app.infrastructure.network.sampler import NetworkStats

DEV = "net-svc-dev"

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
    """按命令前缀返回固定输出；可注入失败次数模拟失联。"""

    def __init__(self, outputs: dict[str, str] | None = None):
        self.outputs = dict(outputs or {})
        # (prefix, 剩余失败次数)：命中且次数未耗尽即抛 AdbError
        self.failures_left: list[list[object]] = []
        self.calls: list[tuple[str, str]] = []

    async def shell(self, device_id: str, cmd: str) -> str:
        self.calls.append((device_id, cmd))
        for entry in self.failures_left:
            prefix, left = entry[0], entry[1]
            if cmd.startswith(str(prefix)) and int(left) > 0:
                entry[1] = int(left) - 1
                raise AdbError(f"ADB command failed: {cmd}")
        for prefix, out in sorted(self.outputs.items(), key=lambda kv: -len(kv[0])):
            if cmd.startswith(prefix):
                return out
        return ""


class DeadAdb:
    """所有 shell 都失败的假 ADB 驱动（模拟设备失联）。"""

    async def shell(self, device_id: str, cmd: str) -> str:
        raise AdbError("ADB command failed: device offline")


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


async def wait_until(predicate, timeout: float = 3.0) -> None:
    """轮询等待条件成立（避免依赖任务调度时序的竞态）。"""
    for _ in range(int(timeout / 0.01)):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("wait_until timeout")


@pytest.fixture
def fast_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """采样循环间隔归零，测试不依赖真实等待。"""
    monkeypatch.setattr(settings().metrics, "network_interval", 0.0)


# ---------------------------------------------------------------------------
# 监控生命周期
# ---------------------------------------------------------------------------

async def test_start_monitoring_idempotent_and_buffers(monkeypatch, fast_loop) -> None:
    """start 幂等；循环把采样点写入缓冲；stop 后缓冲与任务全部释放。"""
    adb = FakeAdb(std_outputs())
    service = NetworkService(adb)

    await service.start_monitoring(DEV)
    await service.start_monitoring(DEV)  # 幂等

    assert len(service._tasks) == 1
    await wait_until(lambda: len(list(service._buffers.get(DEV, []))) >= 3)

    await service.stop_monitoring(DEV)
    assert "net-svc-dev" not in service._tasks
    assert "net-svc-dev" not in service._samplers
    assert "net-svc-dev" not in service._buffers


async def test_device_loss_stops_and_releases_buffer(monkeypatch, fast_loop) -> None:
    """连续 lost_failures 次失败 → 任务自然结束，暂存缓冲释放（B1）。"""
    monkeypatch.setattr(settings().metrics, "lost_failures", 3)
    service = NetworkService(DeadAdb())

    await service.start_monitoring(DEV)
    await wait_until(lambda: DEV not in service._tasks)

    assert DEV not in service._samplers
    assert DEV not in service._buffers  # 失联同样释放暂存


async def test_single_failure_recovers_below_threshold(monkeypatch, fast_loop) -> None:
    """失败次数低于阈值：继续采样，不结束任务。"""
    monkeypatch.setattr(settings().metrics, "lost_failures", 5)
    adb = FakeAdb(std_outputs())
    adb.failures_left = [["cat /proc/net/dev", 1]]  # 仅第一轮失败一次
    service = NetworkService(adb)

    await service.start_monitoring(DEV)
    await wait_until(lambda: len(list(service._buffers.get(DEV, []))) >= 1)

    assert DEV in service._tasks  # 未因单次失败停采
    assert list(service._buffers[DEV])[-1].rx_bytes == 500000
    await service.stop_monitoring(DEV)


async def test_stop_monitoring_idempotent() -> None:
    """stop 未监控设备不抛异常，可重复调用。"""
    service = NetworkService(FakeAdb(std_outputs()))

    await service.stop_monitoring("no-such-device")
    await service.stop_monitoring("no-such-device")


# ---------------------------------------------------------------------------
# 读取路径（冷启动策略）
# ---------------------------------------------------------------------------

async def test_get_stats_reuses_fresh_buffer_point(monkeypatch) -> None:
    """缓冲最新点新鲜（age < interval）→ 直接复用，不触发采样。"""
    adb = FakeAdb(std_outputs())
    service = NetworkService(adb)
    monkeypatch.setattr(settings().metrics, "network_interval", 2.0)
    fresh = make_stats(ts=time.time() - 0.1, rx_bytes=42)
    service._buffers[DEV] = deque([fresh], maxlen=5)

    result = await service.get_stats(DEV)

    assert result is fresh
    assert adb.calls == []  # 未采样
    assert DEV not in service._tasks  # 不因此拉起任务


async def test_get_stats_cold_start_starts_and_samples(monkeypatch) -> None:
    """无任务无缓冲：自动拉起监控 + 内联采样一次并返回。"""
    monkeypatch.setattr(settings().metrics, "network_interval", 2.0)
    service = NetworkService(FakeAdb(std_outputs()))

    result = await service.get_stats(DEV)

    assert DEV in service._tasks  # 冷启动拉起采样循环
    assert result.rx_bytes == 500000
    assert result.rx_rate_kbps == 0.0  # 首包速率必为 0（既有语义）
    await service.stop_monitoring(DEV)


async def test_get_stats_stale_point_triggers_inline_sample(monkeypatch) -> None:
    """缓冲点不新鲜（age >= interval）→ 内联采样重新取样。"""
    monkeypatch.setattr(settings().metrics, "network_interval", 2.0)
    service = NetworkService(FakeAdb(std_outputs()))
    stale = make_stats(ts=time.time() - 100, rx_bytes=1)
    service._buffers[DEV] = deque([stale], maxlen=5)

    result = await service.get_stats(DEV)

    assert result.rx_bytes == 500000  # 新采样值，而非陈旧点
    assert DEV in service._tasks
    await service.stop_monitoring(DEV)


async def test_get_connections_snapshot_without_starting_task() -> None:
    """连接列表是即时快照：不写入缓冲、不拉起采样任务。"""
    service = NetworkService(FakeAdb(std_outputs()))

    connections = await service.get_connections(DEV)

    assert connections == []
    assert DEV not in service._tasks
    assert DEV not in service._buffers


# ---------------------------------------------------------------------------
# 空闲守卫（暂存态清零）
# ---------------------------------------------------------------------------

@pytest.fixture
def guarded(monkeypatch: pytest.MonkeyPatch) -> None:
    """空闲守卫快速配置：ttl 0.05s，采样间隔放慢避免干扰。"""
    monkeypatch.setattr(settings().metrics, "idle_ttl_seconds", 0.05)
    monkeypatch.setattr(settings().metrics, "network_interval", 0.2)


async def test_guard_stops_after_idle(guarded) -> None:
    """无访问（无录制）超过 idle_ttl → 停采并释放缓冲。"""
    service = NetworkService(FakeAdb(std_outputs()))

    await service.get_stats(DEV)  # start + touch 一次
    assert DEV in service._tasks

    await asyncio.sleep(0.2)  # 宽裕于 ttl，让守卫休眠→复查→停采

    assert DEV not in service._tasks
    assert DEV not in service._buffers


async def test_guard_kept_alive_by_ongoing_access(guarded) -> None:
    """HTTP 轮询持续访问期间守卫不断续期；停止访问后停采。"""
    service = NetworkService(FakeAdb(std_outputs()))

    await service.get_stats(DEV)
    for _ in range(10):  # 持续 0.2s 的访问心跳
        service._touch(DEV)
        await asyncio.sleep(0.02)
    assert DEV in service._tasks  # 访问期间未被停采

    await asyncio.sleep(0.2)  # 停止访问 → 守卫复查 → 停采
    assert DEV not in service._tasks


async def test_guard_recording_inhibits_stop_then_resumes(guarded) -> None:
    """录制中禁止空闲停采；录制结束后守卫继续观察并停采。"""
    service = NetworkService(FakeAdb(std_outputs()))
    service._recorders[DEV] = object()

    await service.get_stats(DEV)
    await asyncio.sleep(0.2)
    assert DEV in service._tasks  # 录制中未停采

    del service._recorders[DEV]
    await asyncio.sleep(0.2)
    assert DEV not in service._tasks


async def test_cleanup_stops_all_devices() -> None:
    """cleanup 清理所有设备任务与缓冲（服务关闭路径）。"""
    service = NetworkService(FakeAdb(std_outputs()))
    for did in (DEV, "net-svc-dev-2"):
        await service.start_monitoring(did)

    await service.cleanup()

    assert service._tasks == {}
    assert service._buffers == {}
    assert service._samplers == {}