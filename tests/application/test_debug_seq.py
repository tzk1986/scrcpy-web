"""logcat 采集为条目分配递增 seq 的测试（mock adb/repo）。"""
import pytest

from app.application.debug_service import DebugService
from app.domain.session import DebugSession


class FakeAdb:
    def __init__(self, lines):
        self._lines = lines

    async def stream_logcat(self, device_id):
        for ln in self._lines:
            yield ln


class FakeRepo:
    def __init__(self):
        self.saved = []

    async def save_log(self, session_id, entry, seq=0):
        self.saved.append((seq, entry.message))

    async def save_session(self, session):
        pass

    async def get_session(self, sid):
        pass

    async def next_seq(self, sid):
        return 0


@pytest.mark.asyncio
async def test_collect_logcat_assigns_increasing_seq():
    lines = [
        "09-17 10:00:00.000     1     1 I TagA: msg-a",
        "09-17 10:00:00.100     1     1 I TagB: msg-b",
        "09-17 10:00:00.200     1     1 W TagC: msg-c",
    ]
    repo = FakeRepo()
    svc = DebugService(adb=FakeAdb(lines), repo=repo)
    session = DebugSession(id="s", device_id="d", user_id="u")
    await svc._collect_logcat(session)  # FakeAdb 流自然耗尽结束
    assert [s for s, _ in repo.saved] == [0, 1, 2]
    assert [e["seq"] for e in session.log_buffer] == [0, 1, 2]
