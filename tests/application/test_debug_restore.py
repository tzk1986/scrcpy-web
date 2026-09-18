"""服务重启后 restore_sessions 恢复会话的测试（mock adb/repo）。

方案 17 实施项 5 起 logcat 采集改为订阅者开启录制才启动，
restore 仅重建会话对象与 seq 游标，不再自动续跑采集。
"""
import asyncio
import time

import pytest

from app.application.debug_service import DebugService
from app.domain.session import DebugSession


class Adb:
    def __init__(self):
        self.devices_seen = []

    async def stream_logcat(self, device_id):
        self.devices_seen.append(device_id)
        yield "09-17 10:00:00.000     1     1 I T: hello"


class Repo:
    def __init__(self, sessions=None, max_seq=0):
        self.sessions_db = sessions or {}
        self.saved = []
        self._max = max_seq
        self.list_args = None

    async def save_log(self, s, e, seq=0): self.saved.append((s, seq))
    async def save_logs_bulk(self, rows): self.saved.extend((r[0], r[8]) for r in rows)
    async def save_session(self, s): self.sessions_db[s.id] = s
    async def get_session(self, sid): return self.sessions_db.get(sid)
    async def next_seq(self, sid): return self._max

    async def list_recent_sessions(self, since_ts, limit):
        self.list_args = (since_ts, limit)
        return [s for s in self.sessions_db.values() if s.last_active >= since_ts][:limit]


def make_session(sid, last_active=None):
    s = DebugSession(id=sid, device_id="dev1", user_id="u1")
    if last_active is not None:
        s.last_active = last_active
    return s


@pytest.mark.asyncio
async def test_restore_sessions_recovers_sessions_without_logcat():
    """模拟进程重启：会话在 DB、内存为空 → 恢复对象并续接 seq；
    采集惰性化后无人订阅不再启动 logcat（实施项 5）。"""
    repo = Repo(sessions={"s1": make_session("s1", time.time())}, max_seq=5)
    adb = Adb()
    svc_new = DebugService(adb=adb, repo=repo)
    count = await svc_new.restore_sessions()
    assert count == 1
    assert "s1" in svc_new.sessions
    assert svc_new.sessions["s1"].seq_next == 5          # 从 next_seq 续接，保证单调
    assert svc_new.logcat_tasks == {}                    # 无订阅者，不再自动续跑采集
    await asyncio.sleep(0.1)
    assert adb.devices_seen == []                        # 采集确实未启动
    await svc_new.close_session("s1")
    await svc_new.writer.stop()


@pytest.mark.asyncio
async def test_restore_respects_ttl_and_limit():
    """仅恢复 TTL 内会话，且按 restore_max_sessions 上限（透传给 repo）。"""
    old = make_session("ancient", time.time() - 400 * 86400)  # 远超 7 天 TTL
    fresh = make_session("fresh", time.time())
    repo = Repo(sessions={"ancient": old, "fresh": fresh})
    svc = DebugService(adb=Adb(), repo=repo)
    count = await svc.restore_sessions()
    since_ts, limit = repo.list_args
    assert limit == 20                                    # settings restore_max_sessions 默认
    assert time.time() - 8 * 86400 < since_ts <= time.time() - 6 * 86400  # ≈ TTL 7 天前
    assert count == 1
    assert "ancient" not in svc.sessions
    await svc.close_session("fresh")
    await svc.writer.stop()
