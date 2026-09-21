"""DebugService 补充单测：订阅广播、shell 生命周期、清理任务、错误路径。

与 test_debug_service.py（惰性采集语义）互补，本文件覆盖：
    - 会话获取/关闭（含 shell 与输出转发任务的清理、shell.stop 异常容忍）
    - logcat 采集的级别过滤 / 速率限制 / 环形淘汰 / 流异常
    - _notify_subscribers 与 _notify_shell_output 的过滤、暂停、死连接清理
    - get_logs 内存快路径与 DB 慢路径
    - exec_shell / get_or_create_shell / output forwarding / stream 执行系列
    - 定期清理任务与 run_cleanup / cleanup_session / get_db_stats

全部使用手写 Fake（mock adb/repo/websocket/shell），不依赖真实设备与外部服务。

注意：过滤/限速用例通过 ScriptedParseService 绕过 _parse_logcat_line，
直接注入预设 LogEntry，避免与解析器字段映射疑点（见文件末尾 xfail）耦合。
"""

import asyncio

import pytest

import app.application.debug_service as dbg_mod
from app.application.debug_service import DebugService
from app.core.config import settings as get_settings
from app.domain.ports import LogEntry
from app.domain.session import DebugSession


# ---------------------------------------------------------------------------
# 替身
# ---------------------------------------------------------------------------

class FakeTime:
    """debug_service 模块级可控时钟，用于触发速率限制窗口重置。"""

    def __init__(self, t: float = 1000.0):
        self.t = t

    def time(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


class FakeWS:
    """最小 WebSocket 替身：记录 send_json 载荷；fail=True 模拟已断开。"""

    def __init__(self, fail: bool = False):
        self.sent = []
        self.fail = fail

    async def send_json(self, payload):
        if self.fail:
            raise RuntimeError("connection closed")
        self.sent.append(payload)


class FakeShell:
    """ShellSession 替身：可配置存活状态、execute 输出、输出队列与 stop 异常。"""

    def __init__(self, alive=True, lines=(), fail_execute=None, out=None, stop_error=None):
        self.is_alive = alive
        self._lines = list(lines)
        self._fail_execute = fail_execute
        self._out = out  # asyncio.Queue | None（None → get_output 立即返回 None/EOF）
        self._stop_error = stop_error
        self.executed = []
        self.stopped = 0

    async def execute(self, cmd):
        self.executed.append(cmd)
        if self._fail_execute is not None:
            raise self._fail_execute
        for line in self._lines:
            yield line

    async def get_output(self):
        if self._out is None:
            return None
        return await self._out.get()

    async def stop(self):
        if self._stop_error is not None:
            raise self._stop_error
        self.stopped += 1


class FakeAdb:
    def __init__(self, lines=(), error=None, shell_out="", shell=None):
        self._lines = list(lines)
        self._error = error
        self._shell_out = shell_out
        self._shell = shell
        self.shell_calls = []
        self.shell_created = 0

    async def stream_logcat(self, device_id):
        for line in self._lines:
            yield line
        if self._error is not None:
            raise self._error

    async def shell(self, device_id, cmd):
        self.shell_calls.append((device_id, cmd))
        return self._shell_out

    async def create_shell(self, device_id):
        self.shell_created += 1
        return self._shell


class ClockAdb(FakeAdb):
    """按行推进可控时钟的 logcat 流（触发窗口重置分支）。"""

    def __init__(self, lines, clock: FakeTime, advances):
        super().__init__(lines=lines)
        self._clock = clock
        self._advances = list(advances)

    async def stream_logcat(self, device_id):
        for i, line in enumerate(self._lines):
            if i < len(self._advances):
                self._clock.advance(self._advances[i])
            yield line


class FakeRepo:
    def __init__(self):
        self.bulk = []
        self.sessions_db = {}
        self.shell_history = []
        self.query_logs_result = []
        self.last_filter = None
        self.deleted_session_logs = 0
        self.cleanup_calls = 0
        self.next_seq_calls = 0

    async def save_logs_bulk(self, rows):
        self.bulk.extend(rows)

    async def save_session(self, session):
        self.sessions_db[session.id] = session

    async def get_session(self, session_id):
        return self.sessions_db.get(session_id)

    async def list_recent_sessions(self, since_ts, limit):
        return list(self.sessions_db.values())[:limit]

    async def next_seq(self, session_id):
        self.next_seq_calls += 1
        return 0

    async def query_logs(self, session_id, filter):
        self.last_filter = filter
        return self.query_logs_result

    async def save_shell_history(self, session_id, cmd, output):
        self.shell_history.append((session_id, cmd, output))

    async def delete_old_logs(self, retention_seconds):
        self.cleanup_calls += 1
        return 3

    async def delete_old_shell_history(self, retention_seconds):
        return 2

    async def trim_logs_to_db_size(self, max_size_bytes):
        return 1

    async def get_db_size_bytes(self):
        return 5 * 1024 * 1024

    async def delete_session_logs(self, session_id):
        return self.deleted_session_logs


def make_entry(level: str, message: str) -> LogEntry:
    return LogEntry(ts=1.0, level=level, pid=1, tid=1, tag="T", message=message, raw=message)


class ScriptedParseService(DebugService):
    """绕过 _parse_logcat_line：按顺序把预设 LogEntry 交给采集逻辑。"""

    def __init__(self, *args, entries, **kwargs):
        super().__init__(*args, **kwargs)
        self._entries = list(entries)

    def _parse_logcat_line(self, line):
        return self._entries.pop(0)


def make_session(sid: str = "s1", device: str = "dev1") -> DebugSession:
    return DebugSession(id=sid, device_id=device, user_id="u1")


async def drain(n: int = 8) -> None:
    """推进事件循环若干轮，让任务完成启动/结束/移除。"""
    for _ in range(n):
        await asyncio.sleep(0)


# ---------------------------------------------------------------------------
# 会话获取 / 关闭
# ---------------------------------------------------------------------------

async def test_get_session_prefers_memory_then_falls_back_to_repo():
    repo = FakeRepo()
    svc = DebugService(adb=FakeAdb(), repo=repo)
    s = make_session("s1")
    svc.sessions["s1"] = s
    assert await svc.get_session("s1") is s

    repo.sessions_db["s2"] = make_session("s2")
    assert (await svc.get_session("s2")).id == "s2"
    assert await svc.get_session("nope") is None


async def test_close_session_stops_shell_and_forwarding_task():
    svc = DebugService(adb=FakeAdb(), repo=FakeRepo())
    session = make_session()
    svc.sessions[session.id] = session
    shell = FakeShell()
    svc.shell_sessions[session.id] = shell
    fwd = asyncio.create_task(asyncio.Event().wait())
    svc.shell_output_forwarding_tasks[session.id] = fwd

    await svc.close_session(session.id)
    await drain()
    assert shell.stopped == 1
    assert fwd.cancelled()
    assert session.id not in svc.sessions
    assert session.id not in svc.shell_sessions


async def test_close_session_survives_shell_stop_error():
    svc = DebugService(adb=FakeAdb(), repo=FakeRepo())
    session = make_session()
    svc.sessions[session.id] = session
    svc.shell_sessions[session.id] = FakeShell(stop_error=RuntimeError("pty stuck"))

    await svc.close_session(session.id)  # 不抛出，仅告警
    assert session.id not in svc.sessions
    assert session.id not in svc.shell_sessions


async def test_restore_skips_sessions_already_in_memory():
    repo = FakeRepo()
    repo.sessions_db["s1"] = make_session("s1")  # DB 副本
    svc = DebugService(adb=FakeAdb(), repo=repo)
    existing = make_session("s1")
    existing.seq_next = 99
    svc.sessions["s1"] = existing

    assert await svc.restore_sessions() == 0
    assert svc.sessions["s1"] is existing
    assert existing.seq_next == 99  # 未被 DB 副本覆盖
    assert repo.next_seq_calls == 0  # 跳过在 next_seq 之前


async def test_grace_stop_noop_when_no_collect_task(monkeypatch):
    monkeypatch.setattr(DebugService, "LOGCOLLECT_STOP_GRACE", 0.01)
    svc = DebugService(adb=FakeAdb(), repo=FakeRepo())
    session = await svc.create_session("dev1", "u1")
    try:
        ws = FakeWS()
        await svc.subscribe(session.id, ws)
        await svc.unsubscribe(session.id, ws)  # 无采集任务 → 宽限后直接返回
        await asyncio.sleep(0.03)
        assert svc.logcat_tasks == {}
        assert session.id not in svc.subscribers
    finally:
        await svc.writer.stop()


# ---------------------------------------------------------------------------
# logcat 采集：过滤 / 限速 / 淘汰 / 异常
# ---------------------------------------------------------------------------

async def test_collect_filters_below_min_level(monkeypatch):
    monkeypatch.setattr(get_settings().debug, "min_log_level", "I")
    lines = ["line-d", "line-i"]
    svc = ScriptedParseService(
        adb=FakeAdb(lines=lines),
        repo=FakeRepo(),
        entries=[make_entry("D", "verbose-msg"), make_entry("I", "info-msg")],
    )
    session = make_session()
    await svc._collect_logcat(session)
    assert [e["message"] for e in session.log_buffer] == ["info-msg"]
    await svc.writer.stop()


async def test_collect_rate_limit_drops_low_level_and_resets_window(monkeypatch):
    monkeypatch.setattr(get_settings().debug, "log_rate_limit", 1)
    clock = FakeTime()
    monkeypatch.setattr(dbg_mod, "time", clock)
    lines = ["m0", "m1", "m2", "m3"]
    svc = ScriptedParseService(
        adb=ClockAdb(lines, clock, advances=[0, 0, 0, 2]),
        repo=FakeRepo(),
        entries=[
            make_entry("I", "m0"),  # 窗口内第 1 条 → 保留
            make_entry("I", "m1"),  # 超限 → 丢弃（<W）
            make_entry("W", "m2"),  # 超限但 W → 保留
            make_entry("I", "m3"),  # 时钟推进 2s → 窗口重置 → 保留
        ],
    )
    session = make_session()
    await svc._collect_logcat(session)
    assert [e["message"] for e in session.log_buffer] == ["m0", "m2", "m3"]
    await svc.writer.stop()


async def test_collect_evicts_oldest_when_buffer_full(monkeypatch):
    monkeypatch.setattr(DebugService, "MAX_LOG_BUFFER", 2)
    lines = [f"09-17 10:00:0{i}.000 1 1 I TagA: m{i}" for i in range(3)]
    svc = DebugService(adb=FakeAdb(lines=lines), repo=FakeRepo())
    session = make_session()
    await svc._collect_logcat(session)
    assert [e["message"] for e in session.log_buffer] == ["m1", "m2"]  # 最旧的 m0 被淘汰
    assert [e["seq"] for e in session.log_buffer] == [1, 2]
    await svc.writer.stop()


async def test_collect_survives_stream_error():
    repo = FakeRepo()
    svc = DebugService(adb=FakeAdb(lines=["09-17 10:00:00.000 1 1 I TagA: ok"],
                                   error=RuntimeError("device lost")), repo=repo)
    session = make_session()
    await svc._collect_logcat(session)  # 不抛出，仅记录错误
    await svc.writer.flush()
    assert [e["message"] for e in session.log_buffer] == ["ok"]
    assert len(repo.bulk) == 1
    await svc.writer.stop()


async def test_finished_collect_task_removes_itself():
    svc = DebugService(adb=FakeAdb(lines=["09-17 10:00:00.000 1 1 I TagA: only"]),
                       repo=FakeRepo())
    session = make_session()
    svc._ensure_logcat_task(session)  # 流耗尽自然结束
    await drain()
    assert session.id not in svc.logcat_tasks  # done_callback 已清理入口
    await svc.writer.stop()


# ---------------------------------------------------------------------------
# 日志推送：_notify_subscribers / _notify_shell_output
# ---------------------------------------------------------------------------

async def test_notify_subscribers_respects_pause_and_filters():
    svc = DebugService(adb=FakeAdb(), repo=FakeRepo())
    sid = "s1"
    paused, by_level, by_tag = FakeWS(), FakeWS(), FakeWS()
    level_miss, tag_miss = FakeWS(), FakeWS()
    svc.subscribers[sid] = {
        paused: {"paused": True},
        by_level: {"paused": False, "level": "I", "tag": None},
        by_tag: {"paused": False, "level": None, "tag": "TagB"},
        level_miss: {"paused": False, "level": "E", "tag": None},
        tag_miss: {"paused": False, "level": None, "tag": "Nope"},
    }
    entry = {"level": "I", "tag": "xxTagByy", "message": "m", "seq": 0}
    await svc._notify_subscribers(sid, entry)

    assert paused.sent == []
    assert level_miss.sent == []
    assert tag_miss.sent == []
    assert by_level.sent == [{"type": "log", "entry": entry}]
    assert by_tag.sent == [{"type": "log", "entry": entry}]


async def test_notify_subscribers_removes_dead_connections():
    svc = DebugService(adb=FakeAdb(), repo=FakeRepo())
    sid = "s1"
    dead, alive = FakeWS(fail=True), FakeWS()
    svc.subscribers[sid] = {dead: {"paused": False}, alive: {"paused": False}}
    await svc._notify_subscribers(sid, {"level": "I", "tag": "T"})

    assert dead not in svc.subscribers[sid]
    assert sid in svc.subscribers  # 仍有存活者，会话键保留
    assert alive.sent == [{"type": "log", "entry": {"level": "I", "tag": "T"}}]

    svc.subscribers[sid] = {FakeWS(fail=True): {"paused": False}}
    await svc._notify_subscribers(sid, {"level": "I"})
    assert sid not in svc.subscribers  # 最后一个也断开 → 键删除


async def test_notify_shell_output_sends_and_cleans_dead():
    svc = DebugService(adb=FakeAdb(), repo=FakeRepo())
    await svc._notify_shell_output("absent", "x")  # 无订阅者直接返回

    sid = "s1"
    dead, ok = FakeWS(fail=True), FakeWS()
    svc.subscribers[sid] = {dead: {"paused": True}, ok: {}}
    await svc._notify_shell_output(sid, "hello")
    assert ok.sent == [{"type": "shell_stream", "line": "hello"}]
    assert dead not in svc.subscribers[sid]

    svc.subscribers[sid] = {FakeWS(fail=True): {}}
    await svc._notify_shell_output(sid, "bye")
    assert sid not in svc.subscribers


# ---------------------------------------------------------------------------
# 解析 / 查询
# ---------------------------------------------------------------------------

def test_parse_logcat_line_malformed_falls_back():
    svc = DebugService(adb=FakeAdb(), repo=FakeRepo())
    entry = svc._parse_logcat_line("garbage output")
    assert entry.level == "I"
    assert entry.pid == 0 and entry.tid == 0
    assert entry.tag == ""
    assert entry.message == "garbage output"


def test_parse_logcat_line_threadtime_field_mapping():
    """按模块文档声明的 threadtime 契约：PID TID LEVEL 应对应 pid/tid/level。"""
    svc = DebugService(adb=FakeAdb(), repo=FakeRepo())
    entry = svc._parse_logcat_line("09-08 14:23:45.678  1234  5678 W ActivityManager: hi")
    assert entry.level == "W"
    assert entry.pid == 1234
    assert entry.tid == 5678
    assert entry.tag == "ActivityManager"
    assert entry.message == "hi"


async def test_get_logs_memory_path_filters():
    svc = DebugService(adb=FakeAdb(), repo=FakeRepo())
    session = make_session()
    session.log_buffer = [
        {"ts": 1.0, "level": "I", "tag": "TagA", "message": "a"},
        {"ts": 2.0, "level": "W", "tag": "TagB", "message": "b"},
        {"ts": 3.0, "level": "E", "tag": "TagA", "message": "c"},
    ]
    svc.sessions[session.id] = session

    assert [e["message"] for e in await svc.get_logs(session.id, level="I")] == ["a"]
    assert [e["message"] for e in await svc.get_logs(session.id, tag="TagA")] == ["a", "c"]
    assert [e["message"] for e in await svc.get_logs(session.id, limit=1)] == ["c"]


async def test_get_logs_falls_back_to_db():
    repo = FakeRepo()
    repo.query_logs_result = [
        LogEntry(ts=1.0, level="I", pid=1, tid=2, tag="T", message="m", raw="r")
    ]
    svc = DebugService(adb=FakeAdb(), repo=repo)
    logs = await svc.get_logs("closed-session", level="I", tag="T", limit=5)

    assert logs == [{"ts": 1.0, "level": "I", "pid": 1, "tid": 2,
                     "tag": "T", "message": "m", "raw": "r"}]
    assert repo.last_filter.level == "I"
    assert repo.last_filter.tag == "T"
    assert repo.last_filter.limit == 5


# ---------------------------------------------------------------------------
# shell 执行
# ---------------------------------------------------------------------------

async def test_exec_shell_success_records_history():
    adb = FakeAdb(shell_out="pong")
    repo = FakeRepo()
    svc = DebugService(adb=adb, repo=repo)
    session = make_session()
    svc.sessions[session.id] = session

    out = await svc.exec_shell(session.id, "ping")
    assert out == "pong"
    assert adb.shell_calls == [("dev1", "ping")]
    assert repo.shell_history == [(session.id, "ping", "pong")]
    assert session.shell_history == ["ping"]


async def test_exec_shell_missing_session_raises():
    svc = DebugService(adb=FakeAdb(), repo=FakeRepo())
    with pytest.raises(ValueError):
        await svc.exec_shell("nope", "ls")


async def test_get_or_create_shell_creates_then_reuses():
    shell = FakeShell()
    adb = FakeAdb(shell=shell)
    svc = DebugService(adb=adb, repo=FakeRepo())
    session = make_session()
    svc.sessions[session.id] = session

    got = await svc.get_or_create_shell(session.id)
    assert got is shell
    assert adb.shell_created == 1
    assert svc.shell_sessions[session.id] is shell
    assert session.id in svc.shell_output_forwarding_tasks  # 转发任务已启动

    again = await svc.get_or_create_shell(session.id)
    assert again is shell
    assert adb.shell_created == 1  # 存活 → 复用
    await svc.stop_output_forwarding(session.id)


async def test_get_or_create_shell_replaces_dead_instance():
    dead, fresh = FakeShell(alive=False), FakeShell(alive=True)
    adb = FakeAdb(shell=fresh)
    svc = DebugService(adb=adb, repo=FakeRepo())
    session = make_session()
    svc.sessions[session.id] = session
    svc.shell_sessions[session.id] = dead

    got = await svc.get_or_create_shell(session.id)
    assert got is fresh
    assert adb.shell_created == 1
    assert svc.shell_sessions[session.id] is fresh
    await svc.stop_output_forwarding(session.id)


async def test_get_or_create_shell_missing_session_raises():
    svc = DebugService(adb=FakeAdb(shell=FakeShell()), repo=FakeRepo())
    with pytest.raises(ValueError):
        await svc.get_or_create_shell("nope")


# ---------------------------------------------------------------------------
# shell 输出转发
# ---------------------------------------------------------------------------

async def test_output_forwarding_idempotent_while_running_and_restarts_after_done():
    svc = DebugService(adb=FakeAdb(), repo=FakeRepo())
    queue: asyncio.Queue = asyncio.Queue()
    shell = FakeShell(out=queue)
    sid = "s1"

    svc._start_output_forwarding(sid, shell)
    task1 = svc.shell_output_forwarding_tasks[sid]
    svc._start_output_forwarding(sid, shell)  # 运行中 → 不重建
    assert svc.shell_output_forwarding_tasks[sid] is task1

    await queue.put(None)  # EOF → 循环退出
    await drain()
    assert task1.done()

    svc._start_output_forwarding(sid, shell)  # 已结束 → 重建
    task2 = svc.shell_output_forwarding_tasks[sid]
    assert task2 is not task1

    await svc.stop_output_forwarding(sid)  # 任务尚未启动即被取消，不抛
    assert sid not in svc.shell_output_forwarding_tasks


async def test_output_forwarding_errors_on_dead_pty():
    class RaisingShell(FakeShell):
        async def get_output(self):
            raise RuntimeError("pty gone")

    svc = DebugService(adb=FakeAdb(), repo=FakeRepo())
    svc._start_output_forwarding("s1", RaisingShell())
    await drain()
    task = svc.shell_output_forwarding_tasks["s1"]
    assert task.done()  # 异常被吞掉，任务正常结束


async def test_output_forwarding_streams_lines_to_subscribers():
    svc = DebugService(adb=FakeAdb(), repo=FakeRepo())
    queue: asyncio.Queue = asyncio.Queue()
    shell = FakeShell(out=queue)
    ws = FakeWS()
    svc.subscribers["s1"] = {ws: {"paused": True}}  # shell 输出不受 paused 影响

    svc._start_output_forwarding("s1", shell)
    await queue.put("line-1")
    await drain()
    assert ws.sent == [{"type": "shell_stream", "line": "line-1"}]

    await queue.put(None)
    await drain()
    assert svc.shell_output_forwarding_tasks["s1"].done()


async def test_stop_output_forwarding_cancels_running_task():
    svc = DebugService(adb=FakeAdb(), repo=FakeRepo())
    queue: asyncio.Queue = asyncio.Queue()
    svc._start_output_forwarding("s1", FakeShell(out=queue))
    task = svc.shell_output_forwarding_tasks["s1"]
    await drain()  # 让任务进入 get_output 等待

    await svc.stop_output_forwarding("s1")
    assert task.done()
    assert "s1" not in svc.shell_output_forwarding_tasks

    await svc.stop_output_forwarding("s1")  # 幂等


# ---------------------------------------------------------------------------
# 流式命令执行
# ---------------------------------------------------------------------------

async def test_exec_shell_stream_collects_output_and_records():
    shell = FakeShell(lines=["a", "b"])
    repo = FakeRepo()
    svc = DebugService(adb=FakeAdb(shell=shell), repo=repo)
    session = make_session()
    svc.sessions[session.id] = session

    result = await svc.exec_shell_stream(session.id, "ls")
    assert result == {"output": "ab", "success": True}
    assert repo.shell_history == [(session.id, "ls", "ab")]
    assert session.shell_history == ["ls"]
    await svc.stop_output_forwarding(session.id)


async def test_exec_shell_stream_error_path_still_records_history():
    shell = FakeShell(fail_execute=RuntimeError("shell exploded"))
    repo = FakeRepo()
    svc = DebugService(adb=FakeAdb(shell=shell), repo=repo)
    session = make_session()
    svc.sessions[session.id] = session

    result = await svc.exec_shell_stream(session.id, "boom")
    assert result["success"] is False
    assert "shell exploded" in result["output"]
    assert repo.shell_history == [(session.id, "boom", "shell exploded")]
    await svc.stop_output_forwarding(session.id)


async def test_exec_shell_stream_missing_session_raises():
    svc = DebugService(adb=FakeAdb(shell=FakeShell()), repo=FakeRepo())
    with pytest.raises(ValueError):
        await svc.exec_shell_stream("nope", "ls")


async def test_exec_shell_stream_raw_yields_lines_and_records():
    shell = FakeShell(lines=["x", "y"])
    repo = FakeRepo()
    svc = DebugService(adb=FakeAdb(shell=shell), repo=repo)
    session = make_session()
    svc.sessions[session.id] = session

    lines = [line async for line in svc._exec_shell_stream_raw(session.id, "ls")]
    assert lines == ["x", "y"]
    assert repo.shell_history == [(session.id, "ls", "xy")]
    assert session.shell_history == ["ls"]
    await svc.stop_output_forwarding(session.id)


async def test_exec_shell_stream_raw_missing_session_raises():
    svc = DebugService(adb=FakeAdb(shell=FakeShell()), repo=FakeRepo())
    with pytest.raises(ValueError):
        async for _line in svc._exec_shell_stream_raw("nope", "ls"):
            pass


# ---------------------------------------------------------------------------
# 清理任务与统计
# ---------------------------------------------------------------------------

async def test_start_cleanup_task_duplicate_and_stop():
    svc = DebugService(adb=FakeAdb(), repo=FakeRepo())
    await svc.start_cleanup_task()
    first = svc.cleanup_task
    assert first is not None and not first.done()

    await svc.start_cleanup_task()  # 已运行 → 提前返回，不重建
    assert svc.cleanup_task is first

    await svc.stop_cleanup_task()
    assert first.cancelled()
    await svc.stop_cleanup_task()  # 无任务时幂等


async def test_cleanup_loop_runs_cleanup_then_exits_on_cancel():
    repo = FakeRepo()
    svc = DebugService(adb=FakeAdb(), repo=repo)
    task = asyncio.create_task(svc._cleanup_loop(0.01))
    for _ in range(200):
        if repo.cleanup_calls:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    await task  # 循环内部吞掉 CancelledError，正常结束
    assert repo.cleanup_calls >= 1


async def test_run_cleanup_returns_stats():
    svc = DebugService(adb=FakeAdb(), repo=FakeRepo())
    stats = await svc.run_cleanup()
    assert stats == {
        "logs_deleted": 3,
        "shell_deleted": 2,
        "size_trimmed": 1,
        "db_size_bytes": 5 * 1024 * 1024,
        "db_size_mb": 5.0,
    }


async def test_cleanup_session_delegates_to_repo():
    repo = FakeRepo()
    repo.deleted_session_logs = 7
    svc = DebugService(adb=FakeAdb(), repo=repo)
    assert await svc.cleanup_session("s1") == 7


async def test_get_db_stats_counts_sessions_and_subscribers():
    svc = DebugService(adb=FakeAdb(), repo=FakeRepo())
    svc.sessions["a"] = make_session("a")
    svc.sessions["b"] = make_session("b")
    svc.subscribers["a"] = {FakeWS(): {}, FakeWS(): {}}

    stats = await svc.get_db_stats()
    assert stats["db_size_bytes"] == 5 * 1024 * 1024
    assert stats["db_size_mb"] == 5.0
    assert stats["active_sessions"] == 2
    assert stats["total_subscribers"] == 2