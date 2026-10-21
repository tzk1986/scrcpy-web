"""
应用生命周期钩子测试（方案 42 §3 关服清盘链路）
================================================

monkeypatch 注入全 fake 服务跑 lifespan（不触真实 SQLite/ADB/日志/热重载），
断言：启动拉起录屏周期任务与设备状态轮询；关闭按序「先停设备轮询 →
先停 tick → cleanup_all_recordings 清盘」。对应 lifecycle.py 的
shutdown 顺序第 3 步扩展；设备轮询接线见路线图 §10 断点收口。
"""

from unittest.mock import AsyncMock, MagicMock

from app import lifecycle
from app.core.config import settings


class FakeRecordingService:
    """录屏服务替身：按调用名记录序列，供顺序断言。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def start_cleanup_task(self) -> None:
        self.calls.append("start")

    async def stop_cleanup_task(self) -> None:
        self.calls.append("stop")

    def cleanup_all_recordings(self) -> None:
        self.calls.append("cleanup_all")


async def test_lifespan_shutdown_clears_all_recordings(monkeypatch) -> None:
    rec = FakeRecordingService()
    monkeypatch.setattr(settings().app, "config_watch", False)
    monkeypatch.setattr(lifecycle, "setup_logging", lambda: None)
    monkeypatch.setattr(lifecycle, "init_db", AsyncMock())

    fake_debug = MagicMock()
    fake_debug.restore_sessions = AsyncMock()
    fake_debug.start_cleanup_task = AsyncMock()
    fake_debug.stop_cleanup_task = AsyncMock()
    fake_debug.writer.stop = AsyncMock()  # 方案 51 D13：停机由 flush 改 stop（冲净+取消循环）
    fake_device = MagicMock()
    fake_device.start = AsyncMock()
    fake_device.stop = AsyncMock()
    fake_dev_repo = MagicMock(close=AsyncMock())
    fake_dbg_repo = MagicMock(close=AsyncMock())
    monkeypatch.setattr("app.deps.get_recording_service", lambda: rec)
    monkeypatch.setattr("app.deps.get_debug_service", lambda: fake_debug)
    monkeypatch.setattr("app.deps.get_device_service", lambda: fake_device)
    # 方案 51 D05：停机释放两库连接池——fake 注入保持不触真实 SQLite
    monkeypatch.setattr("app.deps.get_device_repository", lambda: fake_dev_repo)
    monkeypatch.setattr("app.deps.get_debug_repository", lambda: fake_dbg_repo)
    monkeypatch.setattr(
        "app.deps.get_performance_service", lambda: MagicMock(cleanup=AsyncMock())
    )
    monkeypatch.setattr(
        "app.deps.get_network_service", lambda: MagicMock(cleanup=AsyncMock())
    )

    async with lifecycle.lifespan(MagicMock()):
        assert rec.calls == ["start"]
        fake_device.start.assert_awaited_once()

    assert rec.calls == ["start", "stop", "cleanup_all"]
    fake_device.stop.assert_awaited_once()
    fake_debug.writer.stop.assert_awaited_once()
    fake_dev_repo.close.assert_awaited_once()
    fake_dbg_repo.close.assert_awaited_once()
