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


async def test_create_session_rejects_invalid_device_id():
    from app.core.exceptions import InvalidIdentifierError

    adb = HangingAdb()
    svc = DebugService(adb=adb, repo=Repo())
    try:
        with pytest.raises(InvalidIdentifierError):
            await svc.create_session("dev1&calc", "u1")
        assert svc.sessions == {}  # 无副作用：会话未创建
    finally:
        await svc.writer.stop()


async def test_create_session_id_is_opaque_random():
    """H2 修复：session_id 为随机十六进制，不含 device_id/user_id/时间戳，且两次不同。"""
    import re

    adb = HangingAdb()
    svc = DebugService(adb=adb, repo=Repo())
    try:
        s1 = await svc.create_session("dev1", "u1")
        s2 = await svc.create_session("dev1", "u1")
        assert re.fullmatch(r"[0-9a-f]{12}", s1.id)
        assert "dev1" not in s1.id and "u1" not in s1.id
        assert s1.id != s2.id
    finally:
        await svc.close_session(s1.id)
        await svc.close_session(s2.id)
        await svc.writer.stop()


async def test_create_session_active_limit():
    """A1 L2：活跃调试会话数达上限 → SessionLimitError（503），不再新建。"""
    from app.application.debug_service import _MAX_DEBUG_SESSIONS
    from app.core.exceptions import SessionLimitError

    adb = HangingAdb()
    svc = DebugService(adb=adb, repo=Repo())
    for i in range(_MAX_DEBUG_SESSIONS):
        svc.sessions[f"fill-{i}"] = DebugSession(id=f"fill-{i}", device_id="dev", user_id="u")

    with pytest.raises(SessionLimitError):
        await svc.create_session("dev1", "u1")
    assert len(svc.sessions) == _MAX_DEBUG_SESSIONS
    await svc.writer.stop()


# ---- 方案 51 C1：idle reaper（空转会话回收，解除 32 槽死锁） ----


async def test_reap_idle_expired_no_subscriber_closed():
    """过期且无订阅者的会话应被回收（close_session 全清理）。"""
    from app.application.debug_service import _SESSION_IDLE_TTL_SECONDS

    svc = DebugService(adb=HangingAdb(), repo=Repo())
    session = await svc.create_session("dev1", "u1")
    session.last_active = time.time() - _SESSION_IDLE_TTL_SECONDS - 1
    await svc._reap_idle_sessions()
    assert session.id not in svc.sessions
    await svc.writer.stop()


async def test_reap_idle_expired_with_subscriber_kept():
    """虽过期但仍有订阅者：不回收（避免掐断在看的会话）。"""
    from app.application.debug_service import _SESSION_IDLE_TTL_SECONDS

    svc = DebugService(adb=HangingAdb(), repo=Repo())
    session = await svc.create_session("dev1", "u1")
    ws = object()
    await svc.subscribe(session.id, ws)
    session.last_active = time.time() - _SESSION_IDLE_TTL_SECONDS - 1
    await svc._reap_idle_sessions()
    assert session.id in svc.sessions
    await svc.unsubscribe(session.id, ws)
    await svc.close_session(session.id)
    await svc.writer.stop()


async def test_reap_idle_fresh_session_kept():
    """未过期的会话不受影响。"""
    svc = DebugService(adb=HangingAdb(), repo=Repo())
    session = await svc.create_session("dev1", "u1")
    await svc._reap_idle_sessions()
    assert session.id in svc.sessions
    await svc.close_session(session.id)
    await svc.writer.stop()


class HangingShell:
    """get_output 永不返回的假 shell：钉住转发任务生灭，不依赖真 PTY。"""

    is_alive = True

    async def get_output(self):
        await asyncio.Event().wait()
        return None  # pragma: no cover

    async def stop(self):
        pass


async def test_unsubscribe_keeps_forwarding_while_subscribers_remain():
    """方案 51 C2：一方退订不得掐断全体——仅订阅者清零才停输出转发。"""
    svc = DebugService(adb=HangingAdb(), repo=Repo())
    session = await svc.create_session("dev1", "u1")
    ws1, ws2 = object(), object()
    await svc.subscribe(session.id, ws1)
    await svc.subscribe(session.id, ws2)
    svc._start_output_forwarding(session.id, HangingShell())
    task = svc.shell_output_forwarding_tasks[session.id]
    try:
        await svc.unsubscribe_and_maybe_stop_forwarding(session.id, ws1)
        assert svc.shell_output_forwarding_tasks.get(session.id) is task
        assert not task.done()
        await svc.unsubscribe_and_maybe_stop_forwarding(session.id, ws2)
        assert session.id not in svc.shell_output_forwarding_tasks
        assert task.done()
    finally:
        task.cancel()
        await svc.close_session(session.id)
        await svc.writer.stop()


async def test_resubscribe_after_all_left_restarts_forwarding():
    """C2 连带口径：订阅清零停转发后，再次 get_or_create_shell（存活 shell）
    须补建转发，否则新订阅者永久收不到 PTY 输出。"""
    adb = HangingAdb()
    svc = DebugService(adb=adb, repo=Repo())
    session = await svc.create_session("dev1", "u1")
    ws = object()
    await svc.subscribe(session.id, ws)
    shell = HangingShell()
    svc.shell_sessions[session.id] = shell
    svc._start_output_forwarding(session.id, shell)
    try:
        await svc.unsubscribe_and_maybe_stop_forwarding(session.id, ws)
        assert session.id not in svc.shell_output_forwarding_tasks
        # 新客户端订阅：走 subscribe op 同款入口
        await svc.subscribe(session.id, object())
        await svc.get_or_create_shell(session.id)
        assert session.id in svc.shell_output_forwarding_tasks
        await svc.unsubscribe_and_maybe_stop_forwarding(session.id, list(svc.subscribers[session.id])[0])
    finally:
        await svc.close_session(session.id)
        await svc.writer.stop()


class DeadShell:
    """is_alive=False 且 get_output 永挂：模拟死亡后转发任务仍锁存的形态。"""

    is_alive = False

    def __init__(self):
        self.stopped = False

    async def get_output(self):
        await asyncio.Event().wait()
        return None  # pragma: no cover

    async def stop(self):
        self.stopped = True


class LiveShell:
    """先产 lines 中的行、随后永挂的假 shell。"""

    is_alive = True

    def __init__(self, lines=()):
        self._lines = list(lines)

    async def get_output(self):
        if self._lines:
            return self._lines.pop(0)
        await asyncio.Event().wait()
        return None  # pragma: no cover

    async def stop(self):
        pass


class _RecordingWs:
    def __init__(self):
        self.sent = []

    async def send_json(self, payload):
        self.sent.append(payload)


class _ShellAdb:
    def __init__(self, new_shell):
        self.new_shell = new_shell

    async def create_shell(self, device_id):
        return self.new_shell


async def test_dead_shell_replacement_self_heals_forwarding():
    """方案 51 C3：shell 死亡移除时须 cancel 旧转发任务并 stop 旧 shell，
    否则死亡残任务顶锁，新 shell 永久无输出。"""
    dead = DeadShell()
    new = LiveShell(lines=["hi"])
    svc = DebugService(adb=_ShellAdb(new), repo=Repo())  # type: ignore[arg-type]
    session = await svc.create_session("dev1", "u1")
    ws = _RecordingWs()
    await svc.subscribe(session.id, ws)
    svc.shell_sessions[session.id] = dead
    svc._start_output_forwarding(session.id, dead)
    old_task = svc.shell_output_forwarding_tasks[session.id]
    try:
        got = await svc.get_or_create_shell(session.id)
        assert got is new
        assert dead.stopped
        assert old_task.cancelled()
        new_task = svc.shell_output_forwarding_tasks.get(session.id)
        assert new_task is not None and new_task is not old_task
        await _drain_loop()
        assert {"type": "shell_stream", "line": "hi"} in ws.sent
    finally:
        for t in (old_task, svc.shell_output_forwarding_tasks.get(session.id)):
            if t is not None and not t.done():
                t.cancel()
        await svc.close_session(session.id)
        await svc.writer.stop()


class BoomAdb:
    """stream_logcat 立即抛非取消异常：模拟采集链路死亡（方案 51 C8）。"""

    def __init__(self):
        self.starts = 0

    async def stream_logcat(self, device_id):
        self.starts += 1
        if False:  # pragma: no cover
            yield ""
        raise RuntimeError("logcat stream died")


async def test_logcat_error_broadcasts_stream_error_and_rebuilds():
    """采集异常中断：订阅者可感知（stream_error 帧），任务入口清除、
    下次开录经 _ensure_logcat_task 惰性重建。"""
    adb = BoomAdb()
    svc = DebugService(adb=adb, repo=Repo())
    session = await svc.create_session("dev1", "u1")
    ws = _RecordingWs()
    await svc.subscribe(session.id, ws)
    try:
        await svc.set_subscriber_filter(session.id, ws, paused=False)
        task = svc.logcat_tasks[session.id]
        await task  # 异常已被吞 → 任务正常收尾
        await _drain_loop()
        assert any(m.get("type") == "stream_error" for m in ws.sent)
        assert session.id not in svc.logcat_tasks
        # 重建：再次开录即惰性重启采集
        await svc.set_subscriber_filter(session.id, ws, paused=False)
        assert session.id in svc.logcat_tasks
        await _drain_loop()  # 等新任务实际迭代 generator（starts 才自增）
        assert adb.starts == 2
    finally:
        await svc.close_session(session.id)
        await svc.writer.stop()
