"""SqliteDebugRepository / SqliteDeviceRepository 行为测试（真实临时库，不 mock SQLite）。

覆盖：CRUD、迁移/建表幂等、过滤查询、保留期清理、按库大小裁剪、VACUUM、
连接池生命周期与配置回退。每个用例用 tmp_path 下的独立库文件。
"""
import time
from pathlib import Path
from types import SimpleNamespace

import aiosqlite
import pytest

from app.domain.device import DeviceInfo
from app.domain.ports import LogEntry, LogFilter
from app.domain.session import DebugSession
from app.infrastructure.persistence import sqlite as sqlite_mod
from app.infrastructure.persistence.sqlite import (
    _POOLS,
    DatabasePool,
    SqliteDebugRepository,
    SqliteDeviceRepository,
    SqliteMetricsRepository,
    get_pool,
)


@pytest.fixture
async def db_path(tmp_path):
    """每个用例独立的 SQLite 文件路径。"""
    return str(tmp_path / "repo_test.sqlite")


@pytest.fixture(autouse=True)
async def cleanup_pools():
    """用例结束后关闭并移除本用例新建的连接池（不碰其他模块遗留的池）。"""
    before = set(_POOLS)
    yield
    for key in list(_POOLS):
        if key not in before:
            pool = _POOLS.pop(key)
            await pool.close()


@pytest.fixture
async def debug_repo(db_path):
    repo = SqliteDebugRepository(db_path)
    await repo.init_db()
    return repo


def make_entry(ts: float, level: str = "I", pid: int = 1, tag: str = "Tag",
               message: str = "msg", raw: str = "raw") -> LogEntry:
    return LogEntry(ts=ts, level=level, pid=pid, tid=1, tag=tag, message=message, raw=raw)


async def raw_exec(db_path: str, sql: str, params: tuple = ()) -> None:
    """通过共享池直接执行 SQL（用于构造异常数据 / 校验落库内容）。"""
    pool = await get_pool(db_path)
    async with pool.connection() as conn:
        if params:
            await conn.execute(sql, params)
        else:
            await conn.execute(sql)
        await conn.commit()


async def raw_fetchall(db_path: str, sql: str) -> list[tuple]:
    pool = await get_pool(db_path)
    async with pool.connection() as conn:
        cursor = await conn.execute(sql)
        return list(await cursor.fetchall())


# ---------------------------------------------------------------------------
# 连接池生命周期
# ---------------------------------------------------------------------------

async def test_pool_initialize_twice_keeps_pool_size(db_path):
    pool = DatabasePool(db_path, pool_size=2)
    await pool.initialize()
    await pool.initialize()  # 已初始化 → 早退，不重复建连接
    assert pool._pool.qsize() == 2
    await pool.close()


async def test_pool_connection_auto_initializes(db_path):
    """未显式 initialize 时，第一次取连接应自动完成初始化。"""
    pool = DatabasePool(db_path, pool_size=1)
    async with pool.connection() as conn:
        cursor = await conn.execute("SELECT 1")
        row = await cursor.fetchone()
    assert row is not None and row[0] == 1
    await pool.close()


async def test_debug_repo_close_releases_shared_pool(db_path):
    repo = SqliteDebugRepository(db_path)
    await repo.init_db()
    assert db_path in _POOLS
    pool = _POOLS[db_path]

    await repo.close()
    assert db_path not in _POOLS
    assert pool._initialized is False

    await repo.close()  # 再次 close：池已不在，静默返回


# ---------------------------------------------------------------------------
# 会话 CRUD
# ---------------------------------------------------------------------------

async def test_get_session_missing_returns_none(debug_repo):
    assert await debug_repo.get_session("no-such-session") is None


async def test_get_session_null_metadata_falls_back_to_empty_dict(debug_repo, db_path):
    await raw_exec(db_path, "INSERT INTO debug_sessions VALUES (?,?,?,?,?,?)",
                   ("legacy", "d1", "u1", 100.0, 200.0, None))
    got = await debug_repo.get_session("legacy")
    assert got is not None
    assert got.device_id == "d1"
    assert got.metadata == {}


async def test_list_recent_sessions_order_filter_limit(debug_repo, db_path):
    now = time.time()
    await debug_repo.save_session(DebugSession(
        id="old", device_id="d", user_id="u",
        created_at=now - 1000, last_active=now - 1000, metadata={"k": "v"}))
    await debug_repo.save_session(DebugSession(
        id="new", device_id="d", user_id="u",
        created_at=now, last_active=now, metadata={"a": 1}))
    # 旧库遗留行：last_active 介于两者之间，metadata 为 NULL
    await raw_exec(db_path, "INSERT INTO debug_sessions VALUES (?,?,?,?,?,?)",
                   ("nullmeta", "d2", "u2", now - 50, now - 1, None))

    recent = await debug_repo.list_recent_sessions(since_ts=now - 100)
    assert [s.id for s in recent] == ["new", "nullmeta"]  # 按 last_active 降序
    assert recent[0].metadata == {"a": 1}
    assert recent[1].metadata == {}

    limited = await debug_repo.list_recent_sessions(since_ts=now - 100, limit=1)
    assert [s.id for s in limited] == ["new"]

    all_sessions = await debug_repo.list_recent_sessions(since_ts=now - 2000)
    assert {s.id for s in all_sessions} == {"old", "new", "nullmeta"}

    assert await debug_repo.list_recent_sessions(since_ts=now + 10) == []


# ---------------------------------------------------------------------------
# 日志写入 / 增量查询
# ---------------------------------------------------------------------------

async def test_save_logs_bulk_empty_is_noop(debug_repo):
    await debug_repo.save_log("s", make_entry(1.0), seq=0)
    await debug_repo.save_logs_bulk([])  # 空列表直接返回
    assert await debug_repo.next_seq("s") == 1
    assert [r["seq"] for r in await debug_repo.query_logs_since("s", 0)] == [0]


async def test_save_logs_bulk_and_query_logs_since(debug_repo):
    rows = [("s", float(i), "I", 7, 8, "Tag", f"m{i}", "raw", i) for i in range(5)]
    await debug_repo.save_logs_bulk(rows)

    all_rows = await debug_repo.query_logs_since("s", 0)
    assert [r["seq"] for r in all_rows] == [0, 1, 2, 3, 4]  # seq 升序
    assert all_rows[2] == {"ts": 2.0, "level": "I", "pid": 7, "tid": 8,
                           "tag": "Tag", "message": "m2", "raw": "raw", "seq": 2}

    resumed = await debug_repo.query_logs_since("s", 2, limit=2)
    assert [r["seq"] for r in resumed] == [2, 3]

    assert await debug_repo.query_logs_since("s", 99) == []
    assert await debug_repo.query_logs_since("other-session", 0) == []
    assert await debug_repo.next_seq("s") == 5


async def test_query_logs_filters(debug_repo):
    rows = [
        ("f", 1.0, "I", 10, 1, "Camera", "start", "r", 0),
        ("f", 2.0, "W", 10, 1, "Camera", "slow", "r", 1),
        ("f", 3.0, "E", 20, 1, "Audio", "fail", "r", 2),
        ("f", 4.0, "I", 20, 1, "AudioTrack", "play", "r", 3),
    ]
    await debug_repo.save_logs_bulk(rows)

    only_w = await debug_repo.query_logs("f", LogFilter(level="W"))
    assert [(e.level, e.message) for e in only_w] == [("W", "slow")]

    cam = await debug_repo.query_logs("f", LogFilter(tag="Camera"))  # 子串匹配
    assert [e.message for e in cam] == ["start", "slow"]  # 最旧在前

    by_pid = await debug_repo.query_logs("f", LogFilter(pid=20))
    assert [e.message for e in by_pid] == ["fail", "play"]

    combo = await debug_repo.query_logs("f", LogFilter(level="I", tag="Audio", pid=20))
    assert [e.message for e in combo] == ["play"]

    # limit 先取最新 N 条（ts DESC），再反转为最旧在前
    latest_two = await debug_repo.query_logs("f", LogFilter(limit=2))
    assert [e.message for e in latest_two] == ["fail", "play"]

    assert await debug_repo.query_logs("f", LogFilter(level="V")) == []


# ---------------------------------------------------------------------------
# shell 历史
# ---------------------------------------------------------------------------

async def test_shell_history_roundtrip_ordering_limit(debug_repo, db_path):
    await debug_repo.save_shell_history("h", "ls -la", "total 0")
    assert await raw_fetchall(
        db_path, "SELECT command, output FROM shell_history WHERE session_id='h'"
    ) == [("ls -la", "total 0")]

    # 用确定 ts 插入（time.time() 在 Windows 上分辨率粗，不能依赖调用先后）
    for ts, cmd, out in [(100.0, "cmd1", "o1"), (200.0, "cmd2", "o2"), (300.0, "cmd3", "o3")]:
        await raw_exec(db_path, "INSERT INTO shell_history VALUES (?,?,?,?)", ("ord", ts, cmd, out))

    assert await debug_repo.get_shell_history("ord") == [
        ("cmd1", "o1"), ("cmd2", "o2"), ("cmd3", "o3")]
    assert await debug_repo.get_shell_history("ord", limit=2) == [
        ("cmd2", "o2"), ("cmd3", "o3")]
    assert await debug_repo.get_shell_history("no-such-session") == []


# ---------------------------------------------------------------------------
# 保留期清理
# ---------------------------------------------------------------------------

async def test_delete_old_logs(debug_repo):
    now = time.time()
    await debug_repo.save_logs_bulk([
        ("d", now - 10_000, "I", 1, 1, "T", "ancient", "r", 0),
        ("d", now - 500, "I", 1, 1, "T", "fresh", "r", 1),
    ])

    assert await debug_repo.delete_old_logs(retention_seconds=1000) == 1
    remaining = await debug_repo.query_logs_since("d", 0)
    assert [r["message"] for r in remaining] == ["fresh"]

    assert await debug_repo.delete_old_logs(retention_seconds=1000) == 0  # 无过期数据


async def test_delete_old_shell_history(debug_repo, db_path):
    await debug_repo.save_shell_history("h", "ls", "out")
    await raw_exec(db_path, "INSERT INTO shell_history VALUES (?,?,?,?)",
                   ("h", time.time() - 10_000, "stale", "old output"))

    assert await debug_repo.delete_old_shell_history(retention_seconds=3600) == 1
    assert await debug_repo.get_shell_history("h") == [("ls", "out")]

    assert await debug_repo.delete_old_shell_history(retention_seconds=3600) == 0


async def test_delete_session_logs_also_clears_shell_history(debug_repo):
    await debug_repo.save_log("a", make_entry(1.0, message="a1"), seq=0)
    await debug_repo.save_log("a", make_entry(2.0, message="a2"), seq=1)
    await debug_repo.save_log("b", make_entry(3.0, message="b1"), seq=0)
    await debug_repo.save_shell_history("a", "cmd", "out")

    assert await debug_repo.delete_session_logs("a") == 2  # 返回删除的日志条数
    assert await debug_repo.query_logs_since("a", 0) == []
    assert await debug_repo.get_shell_history("a") == []
    assert [r["message"] for r in await debug_repo.query_logs_since("b", 0)] == ["b1"]

    assert await debug_repo.delete_session_logs("a") == 0  # 已空


# ---------------------------------------------------------------------------
# 库大小 / 裁剪 / VACUUM
# ---------------------------------------------------------------------------

async def test_get_db_size_bytes(debug_repo, db_path, tmp_path):
    missing = SqliteDebugRepository(str(tmp_path / "no_such_dir" / "missing.sqlite"))
    assert await missing.get_db_size_bytes() == 0  # 文件不存在

    size = await debug_repo.get_db_size_bytes()
    assert size > 0
    assert size == Path(db_path).stat().st_size


async def test_trim_to_db_size(debug_repo, db_path):
    await SqliteMetricsRepository(db_path).init_db()  # trim 遍历 4 表，需指标表存在
    await debug_repo.save_log("keep", make_entry(time.time()), seq=0)
    size = await debug_repo.get_db_size_bytes()
    # 未超限：直接返回 0，不删任何数据
    assert await debug_repo.trim_to_db_size(size + 1024) == 0
    assert await debug_repo.next_seq("keep") == 1

    rows = [("bulk", float(i), "I", 1, 1, "T", "x" * 100, "r", i) for i in range(1500)]
    await debug_repo.save_logs_bulk(rows)
    # WAL 写入只在 checkpoint 后反映到主库文件大小
    pool = await get_pool(db_path)
    async with pool.connection() as conn:
        await conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    size = await debug_repo.get_db_size_bytes()
    assert size > 50_000

    # 超限：按每批 1000 条删除。WAL 下主库文件不会因 DELETE 缩小，
    # 循环只能靠"表已空"收尾，因此超限时会删掉全部日志（含较新的 keep 行）。
    assert await debug_repo.trim_to_db_size(size // 4) == 1501
    assert await debug_repo.query_logs_since("bulk", 0) == []
    assert await debug_repo.query_logs_since("keep", 0) == []


async def test_trim_logs_breaks_when_file_shrinks_below_limit(db_path):
    """裁剪循环内的收缩复检分支（while True 内的 size<=max → break）。

    默认配置下主库文件不会因 DELETE 变小（WAL + 无 auto_vacuum），
    该分支不可达。这里用真实 SQLite 特性构造：建表前开启
    auto_vacuum=FULL 并把池连接切回 rollback journal，
    使 DELETE 提交时物理截断文件——首轮删 1000 条后文件即低于上限，
    下一轮复检命中 break（若未命中会继续删光 1500 条，断言 deleted==1000
    即是该分支生效的行为证据）。
    """
    # 必须先于建表设置 auto_vacuum，否则不生效（新库无表，直接设置即可）
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute_fetchall("PRAGMA auto_vacuum=FULL")

    # 单连接小池：方便把池内连接切成 rollback journal（WAL 下删除
    # 收缩要等 checkpoint，循环内的 get_db_size_bytes 读主库文件看不到）
    pool = DatabasePool(db_path, pool_size=1)
    _POOLS[db_path] = pool
    repo = SqliteDebugRepository(db_path)
    await repo.init_db()
    await SqliteMetricsRepository(db_path).init_db()  # trim 遍历 4 表，需指标表存在

    async with pool.connection() as conn:
        await conn.execute_fetchall("PRAGMA journal_mode=DELETE")

    rows = [("s", float(i), "I", 1, 1, "T", "x" * 300, "r", i) for i in range(1500)]
    await repo.save_logs_bulk(rows)

    before = await repo.get_db_size_bytes()
    assert before > 100_000  # 数据主导文件体积，schema 开销可忽略
    max_size = before // 2

    deleted = await repo.trim_to_db_size(max_size)
    # 首轮删 1000 条后 auto_vacuum 截断文件 → 复检低于上限 → break
    assert deleted == 1000
    assert len(await repo.query_logs_since("s", 0)) == 500  # 剩余 500 条被保留
    assert await repo.get_db_size_bytes() < max_size  # 收缩真实发生

    # 再次裁剪：已低于上限，直接返回 0
    assert await repo.trim_to_db_size(max_size) == 0


async def test_vacuum_compacts_without_data_loss(debug_repo):
    await debug_repo.save_logs_bulk(
        [("v", float(i), "I", 1, 1, "T", "y" * 200, "r", i) for i in range(50)])
    await debug_repo.delete_session_logs("v")

    await debug_repo.vacuum()
    assert await debug_repo.query_logs_since("v", 0) == []

    await debug_repo.save_log("v", make_entry(1.0), seq=0)  # 库仍可读写
    assert [r["seq"] for r in await debug_repo.query_logs_since("v", 0)] == [0]


# ---------------------------------------------------------------------------
# 设备仓库
# ---------------------------------------------------------------------------

async def test_device_repo_crud(tmp_path):
    db = str(tmp_path / "devices.sqlite")
    repo = SqliteDeviceRepository(db)
    await repo.init_db()
    await repo.init_db()  # 建表幂等
    assert await repo.list_all() == []

    wifi = DeviceInfo(id="192.168.1.5:5555", model="Pixel 6", os_version="13",
                      resolution=(1080, 2400), battery=88, status="online",
                      ip="192.168.1.5", port=5555)
    await repo.save(wifi)
    got = await repo.get(wifi.id)
    assert got == wifi  # 分辨率拆列存储后完整还原
    assert got.resolution == (1080, 2400)

    usb = DeviceInfo(id="emulator-5554", model="sdk", os_version="30",
                     resolution=(720, 1280), battery=100, status="busy")
    await repo.save(usb)
    got_usb = await repo.get("emulator-5554")
    assert got_usb == usb and got_usb.ip is None and got_usb.port is None

    # upsert：同 id 再次 save 覆盖旧值
    updated = DeviceInfo(id=wifi.id, model="Pixel 6 Pro", os_version="14",
                         resolution=(1440, 3120), battery=50, status="offline",
                         ip="192.168.1.5", port=5555)
    await repo.save(updated)
    got = await repo.get(wifi.id)
    assert got == updated

    assert {d.id for d in await repo.list_all()} == {"192.168.1.5:5555", "emulator-5554"}

    await repo.delete(wifi.id)
    assert await repo.get(wifi.id) is None
    assert [d.id for d in await repo.list_all()] == ["emulator-5554"]
    await repo.delete(wifi.id)  # 删除不存在的设备：静默返回


async def test_device_repo_close_releases_shared_pool(tmp_path):
    db = str(tmp_path / "devices_close.sqlite")
    repo = SqliteDeviceRepository(db)
    await repo.init_db()
    assert db in _POOLS

    await repo.close()
    assert db not in _POOLS
    await repo.close()  # 幂等


# ---------------------------------------------------------------------------
# 模块级初始化
# ---------------------------------------------------------------------------

async def test_module_init_db_creates_all_tables(tmp_path, monkeypatch):
    path = str(tmp_path / "module_init.sqlite")
    fake = SimpleNamespace(
        database=SimpleNamespace(path=path),
        debug=SimpleNamespace(db_pool_size=2),
    )
    monkeypatch.setattr(sqlite_mod, "settings", lambda: fake)

    await sqlite_mod.init_db()

    names = {r[0] for r in await raw_fetchall(
        path, "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"debug_sessions", "debug_logs", "shell_history", "devices",
            "perf_samples", "network_samples"} <= names

    # 未显式传路径时，仓库构造器回退到配置值
    assert SqliteDebugRepository().db_path == path
    assert SqliteDeviceRepository().db_path == path


# ---------------------------------------------------------------------------
# SqliteMetricsRepository（方案 18：录制落盘与缓存态导出）
# ---------------------------------------------------------------------------

@pytest.fixture
async def metrics_repo(db_path):
    repo = SqliteMetricsRepository(db_path)
    await repo.init_db()
    return repo


def make_perf_row(device: str = "dev-1", ts: float = 10.0, cpu: float = 20.0):
    return (device, ts, cpu, 4096.0, 2048.0, 60.0, 0,
            "com.example/.MainActivity", "com.example")


def make_network_row(device: str = "dev-1", ts: float = 10.0, rx: int = 100):
    return (device, ts, rx, 200, 1.5, 0.5, 2, 1, "MyWiFi")


async def test_metrics_init_db_idempotent(metrics_repo):
    """重复 init_db 幂等（CREATE IF NOT EXISTS）。"""
    await metrics_repo.init_db()
    await metrics_repo.init_db()


async def test_metrics_save_and_query_roundtrip(metrics_repo):
    """批量写入后按 (device_id, ts) 升序查询；limit 截断；字段名与列一致。"""
    await metrics_repo.save_perf_samples_bulk([
        make_perf_row(ts=30.0, cpu=30.0),
        make_perf_row(ts=10.0, cpu=10.0),
        make_perf_row(ts=20.0, cpu=20.0),
    ])

    rows = await metrics_repo.query_perf_samples("dev-1", limit=2)

    assert [r["ts"] for r in rows] == [10.0, 20.0]
    assert rows[0]["cpu_percent"] == 10.0
    assert rows[0]["current_activity"] == "com.example/.MainActivity"
    assert rows[0]["device_id"] == "dev-1"


async def test_metrics_query_range_filter_inclusive(metrics_repo):
    """from/to 区间过滤为闭区间 [from, to]。"""
    await metrics_repo.save_network_samples_bulk([
        make_network_row(ts=10.0, rx=100),
        make_network_row(ts=20.0, rx=200),
        make_network_row(ts=30.0, rx=300),
    ])

    rows = await metrics_repo.query_network_samples("dev-1", from_ts=10.0, to_ts=20.0)
    assert [r["ts"] for r in rows] == [10.0, 20.0]

    rows = await metrics_repo.query_network_samples("dev-1", from_ts=30.0)
    assert [r["ts"] for r in rows] == [30.0]

    rows = await metrics_repo.query_network_samples("dev-1", to_ts=10.0)
    assert [r["ts"] for r in rows] == [10.0]

    # 设备隔离
    assert await metrics_repo.query_network_samples("other-dev") == []


async def test_metrics_sample_stats_aggregates(metrics_repo):
    """perf/network 聚合 stats：(rows, min_ts, max_ts)，两表相互独立。"""
    await metrics_repo.save_perf_samples_bulk([
        make_perf_row(ts=10.0),
        make_perf_row(ts=30.0),
    ])
    await metrics_repo.save_network_samples_bulk([
        make_network_row(ts=5.0),
    ])

    assert await metrics_repo.perf_sample_stats("dev-1") == (2, 10.0, 30.0)
    assert await metrics_repo.network_sample_stats("dev-1") == (1, 5.0, 5.0)
    assert await metrics_repo.perf_sample_stats("missing") == (0, None, None)


async def test_metrics_count_rows_sums_both_tables(metrics_repo):
    """count_metrics_rows 为两表之和。"""
    assert await metrics_repo.count_metrics_rows() == 0

    await metrics_repo.save_perf_samples_bulk([make_perf_row(ts=1.0)])
    await metrics_repo.save_network_samples_bulk([
        make_network_row(ts=1.0),
        make_network_row(ts=2.0),
    ])

    assert await metrics_repo.count_metrics_rows() == 3


async def test_delete_old_metrics_removes_expired_only(metrics_repo):
    """删除超保留期行（ts 早于 cutoff），保留新行，返回删除数。"""
    now = time.time()
    await metrics_repo.save_perf_samples_bulk([
        make_perf_row(ts=now - 7200),   # 超保留期（3600s）
        make_perf_row(ts=now - 100),    # 保留
    ])
    await metrics_repo.save_network_samples_bulk([
        make_network_row(ts=now - 7200),  # 超期
    ])

    deleted = await metrics_repo.delete_old_metrics(retention_seconds=3600)

    assert deleted == 2
    assert await metrics_repo.count_metrics_rows() == 1
    rows = await metrics_repo.query_perf_samples("dev-1")
    assert len(rows) == 1 and rows[0]["ts"] == now - 100


async def test_trim_metrics_rows_deletes_oldest_cross_table(metrics_repo):
    """两表合计超 max_rows 时按 ts 删最旧（跨表归并）。"""
    await metrics_repo.save_perf_samples_bulk([
        make_perf_row(ts=1.0), make_perf_row(ts=2.0), make_perf_row(ts=3.0),
        make_perf_row(ts=7.0), make_perf_row(ts=8.0),
    ])
    await metrics_repo.save_network_samples_bulk([
        make_network_row(ts=4.0), make_network_row(ts=5.0), make_network_row(ts=6.0),
    ])
    assert await metrics_repo.count_metrics_rows() == 8

    deleted = await metrics_repo.trim_metrics_rows(max_rows=3)

    assert deleted == 5
    assert await metrics_repo.count_metrics_rows() == 3
    remaining_ts = sorted([
        r["ts"] for r in await metrics_repo.query_perf_samples("dev-1", limit=100)
    ] + [
        r["ts"] for r in await metrics_repo.query_network_samples("dev-1", limit=100)
    ])
    assert remaining_ts == [6.0, 7.0, 8.0]  # 保留最新 3 条（跨表）


async def test_trim_metrics_rows_noop_under_limit(metrics_repo):
    """未超限时返回 0 且不删数据。"""
    await metrics_repo.save_perf_samples_bulk([
        make_perf_row(ts=1.0), make_perf_row(ts=2.0),
    ])

    assert await metrics_repo.trim_metrics_rows(max_rows=10) == 0
    assert await metrics_repo.count_metrics_rows() == 2