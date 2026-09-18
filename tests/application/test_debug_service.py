"""
调试会话日志惰性采集测试（方案 17 实施项 5）
==============================================

logcat 采集从「建会话即启动」改为「订阅者开启录制才启动」：
    - create_session / restore_sessions 不再自动启动采集
    - 订阅者 paused=False 时惰性启动（幂等防重复）
    - 全部订阅者暂停后宽限停止（任务取消、入口移除）
    - 宽限期内恢复录制不停止（防录制开关抖动）
    - unsubscribe 后同样判定停采
    - 停采后再次 paused=False 可重启
    - close_session 在无采集任务时幂等
"""

import asyncio
import time

import pytest

from app.application.debug_service import DebugService
from app.domain.session import DebugSession


class HangingAdb:
    """stream_logcat 挂起直到任务被取消，便于断言采集任务的生灭。"""

    def __init__(self):
        self.starts = 0
        self.cancelled = 0

    async def stream_logcat(self, device_id):
        self.starts += 1
        try:
            await asyncio.Event().wait()  # 永不返回，直到任务被 cancel
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        yield  # pragma: no cover


class Repo:
    def __init__(self, sessions=None, max_seq=0):
        self.sessions_db = sessions or {}
        self.saved = []
        self._max = max_seq

    async def save_logs_bulk(self, rows):
        self.saved.extend(rows)

    async def save_log(self, s, e, seq=0):
        pass

    async def save_session(self, s):
        self.sessions_db[s.id] = s

    async def get_session(self, sid):
        return self.sessions_db.get(sid)

    async def next_seq(self, sid):
        return self._max

    async def list_recent_sessions(self, since_ts, limit):
        return [
            s for s in self.sessions_db.values() if s.last_active >= since_ts
        ][:limit]


async def _drain_loop(n: int = 10) -> None:
    """推进事件循环若干轮，让任务完成启动/取消/移除。"""
    for _ in range(n):
        await asyncio.sleep(0)


async def test_create_session_does_not_start_logcat():
    adb = HangingAdb()
    svc = DebugService(adb=adb, repo=Repo())
    session = await svc.create_session("dev1", "u1")
    try:
        assert svc.logcat_tasks == {}
        await _drain_loop()
        assert adb.starts == 0
    finally:
        await svc.close_session(session.id)
        await svc.writer.stop()


async def test_restore_sessions_does_not_start_logcat():
    s = DebugSession(id="s1", device_id="dev1", user_id="u1")
    s.last_active = time.time()
    repo = Repo(sessions={"s1": s}, max_seq=5)
    adb = HangingAdb()
    svc = DebugService(adb=adb, repo=repo)
    count = await svc.restore_sessions()
    try:
        assert count == 1
        assert svc.sessions["s1"].seq_next == 5
        assert svc.logcat_tasks == {}  # 无订阅者，不再续跑 logcat
        await _drain_loop()
        assert adb.starts == 0
    finally:
        await svc.close_session("s1")
        await svc.writer.stop()


async def test_paused_false_starts_collection_idempotently():
    adb = HangingAdb()
    svc = DebugService(adb=adb, repo=Repo())
    session = await svc.create_session("dev1", "u1")
    ws = object()
    await svc.subscribe(session.id, ws)
    try:
        await svc.set_subscriber_filter(session.id, ws, paused=False)
        await svc.set_subscriber_filter(session.id, ws, paused=False)  # 幂等
        assert len(svc.logcat_tasks) == 1
        await _drain_loop()
        assert adb.starts == 1
    finally:
        await svc.close_session(session.id)
        await svc.writer.stop()


async def test_all_paused_stops_collection_after_grace(monkeypatch):
    monkeypatch.setattr(DebugService, "LOGCOLLECT_STOP_GRACE", 0.02)
    adb = HangingAdb()
    svc = DebugService(adb=adb, repo=Repo())
    session = await svc.create_session("dev1", "u1")
    ws = object()
    await svc.subscribe(session.id, ws)
    await svc.set_subscriber_filter(session.id, ws, paused=False)
    try:
        assert session.id in svc.logcat_tasks
        await svc.set_subscriber_filter(session.id, ws, paused=True)
        assert session.id in svc.logcat_tasks  # 宽限期内仍存活
        await asyncio.sleep(0.05)  # 超过宽限
        assert session.id not in svc.logcat_tasks  # 已取消并移除入口
        await _drain_loop()
        assert adb.cancelled == 1
    finally:
        await svc.close_session(session.id)
        await svc.writer.stop()


async def test_restart_within_grace_keeps_collection(monkeypatch):
    monkeypatch.setattr(DebugService, "LOGCOLLECT_STOP_GRACE", 0.1)
    adb = HangingAdb()
    svc = DebugService(adb=adb, repo=Repo())
    session = await svc.create_session("dev1", "u1")
    ws = object()
    await svc.subscribe(session.id, ws)
    try:
        await svc.set_subscriber_filter(session.id, ws, paused=False)
        task = svc.logcat_tasks[session.id]
        await svc.set_subscriber_filter(session.id, ws, paused=True)
        await svc.set_subscriber_filter(session.id, ws, paused=False)  # 宽限期内恢复
        await asyncio.sleep(0.2)  # 超过宽限窗口
        assert svc.logcat_tasks.get(session.id) is task  # 未被取消
        assert adb.cancelled == 0
    finally:
        await svc.close_session(session.id)
        await svc.writer.stop()


async def test_unsubscribe_all_stops_collection(monkeypatch):
    monkeypatch.setattr(DebugService, "LOGCOLLECT_STOP_GRACE", 0.02)
    adb = HangingAdb()
    svc = DebugService(adb=adb, repo=Repo())
    session = await svc.create_session("dev1", "u1")
    ws = object()
    await svc.subscribe(session.id, ws)
    await svc.set_subscriber_filter(session.id, ws, paused=False)
    try:
        await svc.unsubscribe(session.id, ws)
        await asyncio.sleep(0.05)
        assert session.id not in svc.logcat_tasks
        await _drain_loop()
        assert adb.cancelled == 1
    finally:
        await svc.close_session(session.id)
        await svc.writer.stop()


async def test_stopped_then_resumed_restarts_collection(monkeypatch):
    monkeypatch.setattr(DebugService, "LOGCOLLECT_STOP_GRACE", 0.02)
    adb = HangingAdb()
    svc = DebugService(adb=adb, repo=Repo())
    session = await svc.create_session("dev1", "u1")
    ws = object()
    await svc.subscribe(session.id, ws)
    try:
        await svc.set_subscriber_filter(session.id, ws, paused=False)
        await svc.set_subscriber_filter(session.id, ws, paused=True)
        await asyncio.sleep(0.05)  # 停采完成
        assert session.id not in svc.logcat_tasks

        await svc.set_subscriber_filter(session.id, ws, paused=False)  # 重启
        assert session.id in svc.logcat_tasks
        await _drain_loop()
        assert adb.starts == 2
    finally:
        await svc.close_session(session.id)
        await svc.writer.stop()


async def test_close_session_without_task_idempotent():
    svc = DebugService(adb=HangingAdb(), repo=Repo())
    session = await svc.create_session("dev1", "u1")
    await svc.close_session(session.id)  # 无采集任务入口时正常关闭
    assert session.id not in svc.sessions
    await svc.writer.stop()


async def test_task_removes_itself_after_finish():
    """采集任务结束（被 close 取消）后入口自清理，二次 close 不报错。"""
    adb = HangingAdb()
    svc = DebugService(adb=adb, repo=Repo())
    session = await svc.create_session("dev1", "u1")
    ws = object()
    await svc.subscribe(session.id, ws)
    await svc.set_subscriber_filter(session.id, ws, paused=False)
    await _drain_loop()
    await svc.close_session(session.id)
    await _drain_loop()
    assert session.id not in svc.logcat_tasks
    await svc.close_session(session.id)  # 幂等：无任务时再次 close 不抛
    await svc.writer.stop()