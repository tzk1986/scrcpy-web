"""
录屏服务测试
==================

测试 RecordingService（方案 37 子项 B3）。

测试内容：
    - 单设备启动互斥（进行态重复 start → ConflictError）
    - start 参数透传与失败无副作用（AdbError 冒泡且不落会话表）
    - 五态状态机（stop：recording → stopping → pulling → done / failed）
    - 设备端产物兜底清理（会话自身 remote_path 的 pull 与 rm）
    - 主机落盘目录时间/容量双限（_enforce_limits）
    - 超期会话自愈收尾（_self_heal）
"""

import os
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.application.recording_service import RecordingService
from app.core.config import settings
from app.core.exceptions import AdbError, ConflictError
from app.domain.recording import RecordingSession, RecordingState


# ---------------------------------------------------------------------------
# 测试 Fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_adb():
    """
    模拟 AdbDriver 协议的四个录屏方法（方案 37 B2）。

    返回：
        MagicMock: 各录屏方法均为 AsyncMock 的 ADB 驱动模拟。
    """
    adb = MagicMock()
    adb.start_recording = AsyncMock()
    adb.stop_recording = AsyncMock(return_value="/sdcard/openscrcpy_rec_x.mp4")
    adb.pull_recording = AsyncMock(return_value="data/recordings/x.mp4")
    adb.rm_remote = AsyncMock()
    return adb


@pytest.fixture
def service(mock_adb, tmp_path, monkeypatch):
    """
    构建测试用 RecordingService。

    录屏落盘目录锚定 tmp_path：避免 stop 收尾在仓库根 data/ 产生残留。
    """
    monkeypatch.setattr(settings().recording, "dir", str(tmp_path))
    return RecordingService(mock_adb)


# ---------------------------------------------------------------------------
# start：互斥 / 参数透传 / 失败无副作用
# ---------------------------------------------------------------------------

async def test_second_start_conflicts(service):
    s = await service.start("d1")
    assert s.state == RecordingState.RECORDING.value
    with pytest.raises(ConflictError):
        await service.start("d1")


async def test_start_recording_params(service, mock_adb):
    cfg = settings().recording
    s = await service.start("d1")
    assert s.remote_path == f"/sdcard/openscrcpy_rec_{s.id}.mp4"
    mock_adb.start_recording.assert_called_once_with(
        "d1", s.remote_path, cfg.bit_rate, cfg.time_limit
    )


async def test_start_failure_no_session_recorded(service, mock_adb):
    mock_adb.start_recording.side_effect = AdbError("screenrecord 启动失败")
    with pytest.raises(AdbError):
        await service.start("d1")
    assert service.get("d1") is None
    assert service.list_all() == []


# ---------------------------------------------------------------------------
# stop：五态状态机与设备端兜底清理
# ---------------------------------------------------------------------------

async def test_stop_idempotent_when_process_dead(service, mock_adb):
    """stop_recording 返回空串（进程已死）→ 设备端兜底 rm → failed。"""
    mock_adb.stop_recording.return_value = ""
    s = await service.start("d1")
    out = await service.stop("d1")
    assert out is not None and out.state == RecordingState.FAILED.value
    mock_adb.rm_remote.assert_called_once_with("d1", s.remote_path)
    mock_adb.pull_recording.assert_not_called()


async def test_pull_then_rm_remote(service, mock_adb, tmp_path):
    s = await service.start("d1")
    out = await service.stop("d1")
    assert out is not None and out.state == RecordingState.DONE.value
    mock_adb.pull_recording.assert_called_once_with("d1", s.remote_path, str(tmp_path))
    mock_adb.rm_remote.assert_called_once_with("d1", s.remote_path)


async def test_stop_pull_success_sets_local_path(service, mock_adb):
    """pull 成功：会话 local_path 记录落盘路径（B5 file 端点与前端预览依据）。"""
    mock_adb.pull_recording.return_value = "data/recordings/x.mp4"
    s = await service.start("d1")
    out = await service.stop("d1")
    assert out is not None and out.state == RecordingState.DONE.value
    assert s.local_path == "data/recordings/x.mp4"


async def test_stop_adb_error_converges_failed_and_rm_remote(service, mock_adb):
    mock_adb.stop_recording.side_effect = AdbError("pkill 失败")
    s = await service.start("d1")
    out = await service.stop("d1")
    assert out is not None and out.state == RecordingState.FAILED.value
    mock_adb.rm_remote.assert_called_once_with("d1", s.remote_path)


async def test_stop_pull_none_converges_failed(service, mock_adb):
    mock_adb.pull_recording.return_value = None
    s = await service.start("d1")
    out = await service.stop("d1")
    assert out is not None and out.state == RecordingState.FAILED.value
    assert s.local_path == ""
    mock_adb.rm_remote.assert_called_once_with("d1", s.remote_path)


async def test_repeat_stop_idempotent(service, mock_adb):
    await service.start("d1")
    first = await service.stop("d1")
    assert first is not None and first.state == RecordingState.DONE.value
    second = await service.stop("d1")
    assert second is first
    mock_adb.stop_recording.assert_called_once()
    mock_adb.pull_recording.assert_called_once()
    mock_adb.rm_remote.assert_called_once()


# ---------------------------------------------------------------------------
# 目录时间/容量双限
# ---------------------------------------------------------------------------

def test_enforce_limits_deletes_old_first(tmp_path):
    old = tmp_path / "old.mp4"
    new = tmp_path / "new.mp4"
    old.write_bytes(b"x" * 10)
    new.write_bytes(b"x" * 10)
    os.utime(old, (time.time() - 10 * 86400, time.time() - 10 * 86400))
    RecordingService._enforce_limits(tmp_path)  # retention 3 天 → old 删除
    assert not old.exists()
    assert new.exists()


def test_enforce_limits_capacity(tmp_path, monkeypatch):
    """容量维：总字节超限时按 mtime 删旧，直至降到限下。"""
    monkeypatch.setattr(settings().recording, "max_total_mb", 1)
    paths = []
    for i in range(3):
        p = tmp_path / f"rec{i}.mp4"
        p.write_bytes(b"x" * 600_000)
        t = time.time() - (3 - i) * 3600
        os.utime(p, (t, t))
        paths.append(p)
    RecordingService._enforce_limits(tmp_path)
    assert not paths[0].exists()
    assert not paths[1].exists()
    assert paths[2].exists()


# ---------------------------------------------------------------------------
# 会话自愈
# ---------------------------------------------------------------------------

async def test_self_heal_stops_stale_recording(service, mock_adb):
    """started_at 超 time_limit + 30s 仍 recording → 自动走 stop 收尾。"""
    stale = RecordingSession(
        id="stale000000",
        device_id="d1",
        started_at=time.time() - (settings().recording.time_limit + 31),
        state=RecordingState.RECORDING.value,
        remote_path="/sdcard/openscrcpy_rec_stale000000.mp4",
    )
    fresh = RecordingSession(
        id="fresh000000",
        device_id="d9",
        state=RecordingState.RECORDING.value,
        remote_path="/sdcard/openscrcpy_rec_fresh000000.mp4",
    )
    service.sessions["d1"] = stale
    service.sessions["d9"] = fresh
    await service._self_heal()
    assert stale.state in (RecordingState.DONE.value, RecordingState.FAILED.value)
    assert fresh.state == RecordingState.RECORDING.value
    mock_adb.stop_recording.assert_called_once_with("d1")