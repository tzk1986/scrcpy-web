"""断线续传 get_logs_since 的增量/回退语义测试（mock adb/repo）。"""
import pytest

from app.application.debug_service import DebugService
from app.domain.session import DebugSession


class Adb:
    async def stream_logcat(self, d):
        if False:
            yield


class Repo:
    async def save_logs_bulk(self, rows): pass
    async def save_log(self, s, e, seq=0): pass
    async def save_session(self, s): pass
    async def get_session(self, s): pass
    async def next_seq(self, s): return 0

    async def query_logs_since(self, session_id, from_seq, limit=1000):
        return [
            {"ts": 1.0, "level": "I", "pid": 1, "tid": 1, "tag": "t",
             "message": f"db{i}", "raw": "", "seq": from_seq + i}
            for i in range(min(limit, 10))
        ]


@pytest.mark.asyncio
async def test_get_logs_since_from_memory():
    svc = DebugService(adb=Adb(), repo=Repo())
    s = DebugSession(id="s", device_id="d", user_id="u")
    s.log_buffer = [{"seq": i, "message": f"m{i}"} for i in range(100)]
    svc.sessions[s.id] = s
    logs, missing = await svc.get_logs_since("s", from_seq=90)
    assert [l["seq"] for l in logs] == list(range(90, 100))
    assert missing == 0


@pytest.mark.asyncio
async def test_get_logs_since_counts_missing():
    svc = DebugService(adb=Adb(), repo=Repo())
    s = DebugSession(id="s", device_id="d", user_id="u")
    s.log_buffer = [{"seq": i} for i in range(20, 60)]   # 头部 0-19 已环形淘汰
    svc.sessions[s.id] = s
    logs, missing = await svc.get_logs_since("s", from_seq=10)
    assert missing == 10                                  # 10..19 拿不到
    assert logs[0]["seq"] == 20


@pytest.mark.asyncio
async def test_get_logs_since_falls_back_to_db_for_closed_session():
    svc = DebugService(adb=Adb(), repo=Repo())
    logs, missing = await svc.get_logs_since("closed", from_seq=0)
    assert missing == 0
    assert logs[0]["message"] == "db0"
