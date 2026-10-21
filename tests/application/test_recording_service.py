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
    - 自然结束监听器自动收尾（方案 42 §1）
"""

import asyncio
import os
import time
from pathlib import Path
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
    adb.set_recording_natural_end_listener = MagicMock()  # 同步注册点（方案 42 §1）
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
    assert s.remote_path.endswith(f"__{s.id}.mp4") and s.remote_path.startswith(
        "/sdcard/openscrcpy_rec_"
    )
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


async def test_stop_mkdir_failure_converges_failed_and_rm_remote(service, mock_adb, monkeypatch):
    """落盘目录不可写（mkdir OSError）→ 不冒泡，收敛 failed 且先 rm_remote 兜底。"""
    def _boom_mkdir(self: Path, *args: object, **kwargs: object) -> None:
        raise OSError("Permission denied")

    monkeypatch.setattr(Path, "mkdir", _boom_mkdir)
    s = await service.start("d1")
    out = await service.stop("d1")  # 不冒泡 OSError
    assert out is not None and out.state == RecordingState.FAILED.value
    mock_adb.rm_remote.assert_called_once_with("d1", s.remote_path)
    mock_adb.pull_recording.assert_not_called()


async def test_stop_reentrant_in_progress_is_noop(service, mock_adb):
    """进行态（stopping/pulling）内 stop 再入：后进者直返当前会话，
    不再触达 stop_recording/rm/FAILED（方案 51 E1 双击炸产物）。"""
    started = asyncio.Event()
    gate = asyncio.Event()

    async def slow_stop(device_id: str) -> str:
        started.set()
        await gate.wait()
        return "/sdcard/openscrcpy_rec_x.mp4"

    mock_adb.stop_recording = AsyncMock(side_effect=slow_stop)
    await service.start("d1")
    t1 = asyncio.create_task(service.stop("d1"))
    await started.wait()  # 前进者已置 STOPPING 并挂在 stop_recording
    # 再入：应即时直返；现码无守卫会二次挂 gate → 以超时暴露缺陷
    second = await asyncio.wait_for(service.stop("d1"), timeout=0.5)
    gate.set()
    first = await t1
    assert first.state == RecordingState.DONE.value
    assert second is first  # 同一会话对象直返，无副作用
    assert mock_adb.stop_recording.await_count == 1
    assert mock_adb.pull_recording.await_count == 1
    assert mock_adb.rm_remote.await_count == 1


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


async def test_self_heal_respects_device_level_time_limit(monkeypatch, mock_adb, tmp_path):
    """方案 45 T8：巡检按会话设备取 time_limit——覆盖值更大的会话不判 stale。"""
    import config.settings as config_settings
    from config.settings import reload_settings

    def layered(env: str = "base"):
        if env == "base":
            return {"app": {"name": "OpenScrcpy"},
                    "server": {"host": "0.0.0.0", "port": 8765}}
        if env == "user":
            return {
                "recording": {"dir": str(tmp_path)},
                "devices": {"d1": {"recording": {"time_limit": 600}}},
            }
        return {}

    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setattr(config_settings, "load_yaml_config", layered)
    saved = config_settings._settings
    config_settings._settings = None
    try:
        reload_settings()
        service = RecordingService(mock_adb)
        aged = time.time() - (settings().recording.time_limit + 31)
        overridden = RecordingSession(
            id="stale000000",
            device_id="d1",
            started_at=aged,
            state=RecordingState.RECORDING.value,
            remote_path="/sdcard/openscrcpy_rec_stale000000.mp4",
        )
        global_cfg = RecordingSession(
            id="other000000",
            device_id="d9",
            started_at=aged,
            state=RecordingState.RECORDING.value,
            remote_path="/sdcard/openscrcpy_rec_other000000.mp4",
        )
        service.sessions["d1"] = overridden
        service.sessions["d9"] = global_cfg
        await service._self_heal()
        assert overridden.state == RecordingState.RECORDING.value
        assert global_cfg.state in (RecordingState.DONE.value, RecordingState.FAILED.value)
        mock_adb.stop_recording.assert_called_once_with("d9")
    finally:
        config_settings._settings = saved


# ---------------------------------------------------------------------------
# 自然结束自动收尾（方案 42 §1）
# ---------------------------------------------------------------------------

def _registered_listener(mock_adb):
    """取 RecordingService 构造时经依赖倒置通道注册的自然结束监听器。"""
    mock_adb.set_recording_natural_end_listener.assert_called_once()
    return mock_adb.set_recording_natural_end_listener.call_args[0][0]


async def test_init_registers_natural_end_listener(service, mock_adb):
    listener = _registered_listener(mock_adb)
    assert listener == service._on_recording_natural_end


async def test_natural_end_auto_finalizes(service, mock_adb, tmp_path):
    """监听器 → 自动 stop 收尾（pull→done）——等效系统代为执行了一次停止。"""
    s = await service.start("d1")
    listener = _registered_listener(mock_adb)
    mock_adb.stop_recording.return_value = s.remote_path
    mock_adb.pull_recording.return_value = str(tmp_path / "rec.mp4")
    listener("d1")
    for _ in range(5):
        await asyncio.sleep(0)
    assert s.state == RecordingState.DONE.value
    assert s.local_path == str(tmp_path / "rec.mp4")
    mock_adb.stop_recording.assert_called_once_with("d1")
    mock_adb.pull_recording.assert_called_once_with("d1", s.remote_path, str(tmp_path))
    mock_adb.rm_remote.assert_called_once_with("d1", s.remote_path)


async def test_natural_end_pull_failure_converges_failed(service, mock_adb):
    """Review Focus ①：自然结束时设备离线（pull None）→ 收敛 failed，不悬挂不冒泡。"""
    s = await service.start("d1")
    listener = _registered_listener(mock_adb)
    mock_adb.pull_recording.return_value = None
    listener("d1")
    for _ in range(5):
        await asyncio.sleep(0)
    assert s.state == RecordingState.FAILED.value
    mock_adb.rm_remote.assert_called_once_with("d1", s.remote_path)


async def test_natural_end_guard_noop_when_not_recording(service, mock_adb):
    """守卫一：无会话 / 会话已非 recording（用户已手动停止推进）→ no-op。"""
    listener = _registered_listener(mock_adb)
    listener("ghost")
    s = await service.start("d1")
    s.state = RecordingState.STOPPING.value
    listener("d1")
    for _ in range(5):
        await asyncio.sleep(0)
    mock_adb.stop_recording.assert_not_called()


async def test_auto_finalize_guard_noop_on_replaced_session(service, mock_adb):
    """Review Focus ②：收尾协程 id 失配（会话被新 start 覆盖）→ no-op，不误停新段。"""
    s_a = await service.start("d1")
    await service.stop("d1")
    mock_adb.stop_recording.reset_mock()
    s_b = await service.start("d1")
    await service._auto_finalize("d1", s_a.id)
    for _ in range(5):
        await asyncio.sleep(0)
    assert s_b.state == RecordingState.RECORDING.value
    mock_adb.stop_recording.assert_not_called()


# ---------------------------------------------------------------------------
# stopping/pulling 自愈 + 关服/启动清盘（方案 42 §2 §3）
# ---------------------------------------------------------------------------

async def test_self_heal_converges_stuck_pulling(service, mock_adb):
    """§2：stopping/pulling 超 time_limit+120s 无推进 → 收敛 failed + 设备端兜底 rm。"""
    stuck = RecordingSession(
        id="stuck0000000",
        device_id="d1",
        started_at=time.time()
        - (settings().recording.time_limit + RecordingService.STUCK_STATE_TIMEOUT_SECONDS + 1),
        state=RecordingState.PULLING.value,
        remote_path="/sdcard/openscrcpy_rec_stuck0.mp4",
    )
    service.sessions["d1"] = stuck
    await service._self_heal()
    assert stuck.state == RecordingState.FAILED.value
    mock_adb.rm_remote.assert_called_once_with("d1", stuck.remote_path)


async def test_self_heal_ignores_fresh_stopping(service, mock_adb):
    """Review Focus ③：正在收尾（started_at 未超阈）的 stopping 不得被误杀。"""
    fresh = RecordingSession(
        id="fresh0000000",
        device_id="d9",
        state=RecordingState.STOPPING.value,
        remote_path="/sdcard/openscrcpy_rec_fresh0.mp4",
    )
    service.sessions["d9"] = fresh
    await service._self_heal()
    assert fresh.state == RecordingState.STOPPING.value
    mock_adb.rm_remote.assert_not_called()


def test_cleanup_all_recordings_clears_pattern_only(service, tmp_path):
    """§3：仅删 openscrcpy_rec_*.mp4；他名文件与旁路文件不动。"""
    hits = [
        tmp_path / "openscrcpy_rec_aaa111222333.mp4",
        tmp_path / "openscrcpy_rec_bbb111222333.mp4",
        tmp_path / "openscrcpy_rec_192.168.8.18-5555__ccc111222333.mp4",
    ]
    keeps = [tmp_path / "other.mp4", tmp_path / "openscrcpy_rec_c.txt"]
    for p in hits + keeps:
        p.write_bytes(b"x")
    service.cleanup_all_recordings()
    assert not any(p.exists() for p in hits)
    assert all(p.exists() for p in keeps)


def test_cleanup_all_recordings_missing_dir_silent(tmp_path, monkeypatch):
    """Review Focus ④a：目录不存在（首次启动）→ 静默返回不抛。"""
    monkeypatch.setattr(settings().recording, "dir", str(tmp_path / "not_created"))
    RecordingService(MagicMock()).cleanup_all_recordings()  # 不抛即通过


def test_cleanup_all_recordings_locked_file_best_effort(service, tmp_path, monkeypatch):
    """Review Focus ④b：单文件删除失败（Windows 下载器持有）→ 仅告警不抛，其余照删。"""
    (tmp_path / "openscrcpy_rec_aaa111222333.mp4").write_bytes(b"x")
    (tmp_path / "openscrcpy_rec_bbb111222333.mp4").write_bytes(b"x")

    def _boom(self: Path, *args: object, **kwargs: object) -> None:
        if self.name.endswith("aaa111222333.mp4"):
            raise OSError("文件被占用")

    original = Path.unlink

    def fake_unlink(self: Path, *args: object, **kwargs: object) -> None:
        _boom(self, *args, **kwargs)
        original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fake_unlink)
    service.cleanup_all_recordings()  # 不抛即通过
    assert (tmp_path / "openscrcpy_rec_aaa111222333.mp4").exists()  # 被占文件保留
    assert not (tmp_path / "openscrcpy_rec_bbb111222333.mp4").exists()  # 其余已删


async def test_cleanup_loop_sweeps_immediately_at_startup(service, monkeypatch):
    """§3：tick 改「首轮即时」——启动即扫盘 + cleanup，不再先睡 3600s（白盘一小时）。"""
    swept: list[bool] = []
    cleaned: list[bool] = []
    monkeypatch.setattr(RecordingService, "cleanup_all_recordings", lambda self: swept.append(True))

    async def fake_cleanup(self: RecordingService) -> None:
        cleaned.append(True)

    monkeypatch.setattr(RecordingService, "cleanup", fake_cleanup)
    await service.start_cleanup_task()
    await asyncio.sleep(0.05)
    assert swept == [True]
    assert cleaned == [True]
    await service.stop_cleanup_task()


# ---------------------------------------------------------------------------
# 方案 46 T1：落盘命名嵌设备标识 + 新旧格式解析
# ---------------------------------------------------------------------------


def test_start_remote_path_embeds_device(service: RecordingService) -> None:
    """start 生成 openscrcpy_rec_{dev}__{12hex}.mp4：IP:port 设备 id 被 sanitize。"""
    s = asyncio.run(service.start("192.168.8.18:5555"))
    assert s.remote_path == f"/sdcard/openscrcpy_rec_192.168.8.18-5555__{s.id}.mp4"


def test_start_remote_path_plain_serial_unchanged(service: RecordingService) -> None:
    """USB 序列号形态（合法字符集）原样嵌入。"""
    s = asyncio.run(service.start("rk3568_r"))
    assert s.remote_path == f"/sdcard/openscrcpy_rec_rk3568_r__{s.id}.mp4"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("openscrcpy_rec_192.168.8.18-5555__a1b2c3d4e5f6.mp4", ("192.168.8.18-5555", "a1b2c3d4e5f6")),
        ("openscrcpy_rec_a1b2c3d4e5f6.mp4", (None, "a1b2c3d4e5f6")),  # 旧格式
        ("openscrcpy_rec_A1B2C3D4E5F6.mp4", (None, None)),  # 大写 hex 非法
        ("openscrcpy_rec_dev_x__a1b2c3d4e5f6.mp4", ("dev_x", "a1b2c3d4e5f6")),  # dev 内单下划线
        ("openscrcpy_rec_tooshort__a1b2c3d4e5f67.mp4", (None, None)),  # sid 13 位
        ("other.mp4", (None, None)),
        ("openscrcpy_rec_a1b2c3d4e5f6.txt", (None, None)),
    ],
)
def test_parse_product_name(name: str, expected: tuple[str | None, str | None]) -> None:
    assert RecordingService.parse_product_name(name) == expected
