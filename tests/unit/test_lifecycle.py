"""
应用生命周期钩子测试（方案 42 §3 关服清盘链路）
================================================

monkeypatch 注入全 fake 服务跑 lifespan（不触真实 SQLite/ADB/日志/热重载），
断言：启动拉起录屏周期任务；关闭按序「先停 tick → cleanup_all_recordings
清盘」。对应 lifecycle.py 的 shutdown 顺序第 3 步扩展。
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
    fake_debug.writer.flush = AsyncMock()
    monkeypatch.setattr("app.deps.get_recording_service", lambda: rec)
    monkeypatch.setattr("app.deps.get_debug_service", lambda: fake_debug)
    monkeypatch.setattr(
        "app.deps.get_performance_service", lambda: MagicMock(cleanup=AsyncMock())
    )
    monkeypatch.setattr(
        "app.deps.get_network_service", lambda: MagicMock(cleanup=AsyncMock())
    )

    async with lifecycle.lifespan(MagicMock()):
        assert rec.calls == ["start"]

    assert rec.calls == ["start", "stop", "cleanup_all"]
