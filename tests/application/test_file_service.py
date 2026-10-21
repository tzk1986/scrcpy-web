"""FileService 单测（方案 39 T2）。

覆盖：路径净化与白名单矩阵、ls 错误映射、下载单文件/多选 zip、
互斥 TransferBusy、暂存即清、上传流式与 413、重命名/删除（含软链 rm -f）、
任务表 TTL 与孤儿暂存清扫。FakeAdb 走 AdbDriver Protocol 鸭子替身。
"""

import asyncio
import os
import tempfile
import time
import zipfile
from pathlib import Path

import pytest

from app.application.file_service import FileService
from app.core.exceptions import (
    AdbError,
    FileTooLargeError,
    InvalidPathError,
    JobNotFoundError,
    NotAFileError,
    PathNotFoundError,
    PermissionDeniedError,
    TargetExistsError,
    TransferBusyError,
)
from app.domain.files import FileEntry


def make_entry(
    name: str = "a.txt",
    is_dir: bool = False,
    is_symlink: bool = False,
    size: int = 100,
) -> FileEntry:
    return FileEntry(
        name=name,
        is_dir=is_dir,
        is_symlink=is_symlink,
        size=size,
        mtime="2026-10-12 09:12",
        permissions=("drwxr-x---" if is_dir else "lrwxrwxrwx" if is_symlink else "-rw-r--r--"),
    )


class FakeAdb:
    """AdbDriver 替身：entries 驱动 stat/list；pull 落假内容；push/rename/delete 记录。"""

    def __init__(self) -> None:
        self.entries: dict[str, FileEntry] = {}
        self.pull_contents: dict[str, bytes] = {}
        self.listed: list[str] = []
        self.pulled: list[tuple[str, str, int | None]] = []
        self.pushed: list[tuple[str, str, str, int | None]] = []
        self.renamed: list[tuple[str, str]] = []
        self.deleted: list[tuple[str, bool]] = []
        self.pull_gate: asyncio.Event | None = None
        self.push_gate: asyncio.Event | None = None

    async def list_dir(self, device_id: str, path: str) -> list[FileEntry]:
        self.listed.append(path)
        if path == "/missing":
            raise AdbError("ls: /missing: No such file or directory")
        if path == "/denied":
            raise AdbError("ls: /denied: Permission denied")
        # 模拟真实 ls -la：含 . 与 .. 自描述条目（cli 段原样上浮，服务段过滤）
        return [
            make_entry(name=".", is_dir=True),
            make_entry(name="..", is_dir=True),
            make_entry(name="x"),
        ]

    async def stat_size(self, device_id: str, path: str) -> FileEntry:
        if path in self.entries:
            return self.entries[path]
        raise AdbError(f"ls: {path}: No such file or directory")

    async def pull(self, device_id: str, remote: str, local: str, timeout: int | None = None) -> None:
        if self.pull_gate is not None:
            await self.pull_gate.wait()
        if remote not in self.pull_contents:
            raise AdbError(f"pull failed for {remote}")
        Path(local).write_bytes(self.pull_contents[remote])
        self.pulled.append((remote, local, timeout))

    async def push(self, device_id: str, local: str, remote: str, timeout: int | None = None) -> None:
        if self.push_gate is not None:
            await self.push_gate.wait()
        self.pushed.append((device_id, local, remote, timeout))

    async def rename_path(self, device_id: str, old: str, new: str) -> None:
        self.renamed.append((old, new))

    async def delete_path(self, device_id: str, path: str, is_dir: bool) -> None:
        self.deleted.append((path, is_dir))


@pytest.fixture()
def adb() -> FakeAdb:
    return FakeAdb()


@pytest.fixture()
def svc(adb: FakeAdb) -> FileService:
    return FileService(adb=adb)  # type: ignore[arg-type]


# ---------- normalize / 白名单矩阵（Review Focus #4）----------


def test_normalize_basic(svc: FileService) -> None:
    assert svc.normalize_path("/sdcard/") == "/sdcard"
    assert svc.normalize_path("/sdcard/a b") == "/sdcard/a b"
    assert svc.normalize_path("/sdcard/./x/../y") == "/sdcard/y"


@pytest.mark.parametrize("bad", ["", "a/b", "rel/path", "\x00", "/x\x00y"])
def test_normalize_rejects(svc: FileService, bad: str) -> None:
    with pytest.raises(InvalidPathError):
        svc.normalize_path(bad)


def test_normalize_no_reflect(svc: FileService) -> None:
    with pytest.raises(InvalidPathError) as ei:
        svc.normalize_path("../../etc/passwd")
    assert ".." not in str(ei.value)


async def test_writable_matrix(svc: FileService, adb: FakeAdb) -> None:
    adb.entries = {"/sdcard/a": make_entry("a"), "/sdcard_x/b": make_entry("b"), "/data/c": make_entry("c")}
    # /sdcard/../etc → normalize 成 /etc → 白名单外 403
    with pytest.raises(PermissionDeniedError):
        await svc.rename("/d", "/sdcard/../etc/x", "y")
    # 同头前缀绕过
    with pytest.raises(PermissionDeniedError):
        await svc.rename("/d", "/sdcard_x/b", "y")
    with pytest.raises(PermissionDeniedError):
        await svc.delete("/d", ["/data/c"])
    # /data/local/tmp 允许（delete 不存在会 stat 失败进 results，但不 403）
    results = await svc.delete("/d", ["/data/local/tmp/z"])
    assert results[0]["ok"] is False and results[0]["path"] == "/data/local/tmp/z"


# ---------- 浏览与错误映射 ----------


async def test_list_dir_passthrough(svc: FileService, adb: FakeAdb) -> None:
    out = await svc.list_dir("d", "/sdcard/")
    assert adb.listed == ["/sdcard"]  # normalize 后传下划线裸路径
    assert [e.name for e in out] == ["x"]  # . / .. 已过滤


async def test_list_dir_error_mapping(svc: FileService) -> None:
    with pytest.raises(PathNotFoundError):
        await svc.list_dir("d", "/missing")
    with pytest.raises(PermissionDeniedError):
        await svc.list_dir("d", "/denied")


# ---------- 下载 ----------


async def test_download_single_success_and_cleanup(svc: FileService, adb: FakeAdb) -> None:
    adb.entries["/sdcard/a.txt"] = make_entry("a.txt", size=7)
    adb.pull_contents["/sdcard/a.txt"] = b"content"
    job, artifact, filename = await svc.prepare_download("d", ["/sdcard/a.txt"])
    assert filename == "a.txt"
    assert artifact.read_bytes() == b"content"
    assert job.status == "running" and job.substage == "http"
    assert job.total_size == 7
    assert adb.pulled[0][0] == "/sdcard/a.txt"
    assert adb.pulled[0][2] == svc.TRANSFER_TIMEOUT
    # 精确断言本 job 工作目录被删——不按全局前缀计数（同机其他服务的
    # scrcpy-filejob-* 目录会在断言窗口内增删，全局计数跨进程不稳定）
    wd = svc._workdirs[job.id]
    svc.finalize_download(job.id)
    assert job.status == "done" and job.finished_at is not None
    assert not artifact.exists()
    assert not wd.exists()
    # 锁已释放：再来一次不互斥
    job2, _, _ = await svc.prepare_download("d", ["/sdcard/a.txt"])
    assert job2.id != job.id


async def test_download_single_ls_ld_returns_fullpath(svc: FileService, adb: FakeAdb) -> None:
    """真机 `ls -ld /a/b/c.bin` 的 name 列是全路径（2026-10-12 实测口径）。

    产物名与下载名必须取 basename(normed)，否则 pathlib 拼接绝对路径会
    替换掉 tmp 目录，把文件写出工作区外。
    """
    p = "/sdcard/Download/c.bin"
    adb.entries[p] = make_entry(name=p, size=3)
    adb.pull_contents[p] = b"abc"
    job, artifact, filename = await svc.prepare_download("d", [p])
    try:
        assert filename == "c.bin"
        assert artifact.parent == svc._workdirs[job.id]
    finally:
        svc.finalize_download(job.id)
        Path("/sdcard/Download/c.bin").unlink(missing_ok=True)  # 缺陷路径可能已写出盘外


async def test_download_directory_rejected(svc: FileService, adb: FakeAdb) -> None:
    adb.entries["/sdcard/dir"] = make_entry("dir", is_dir=True)
    with pytest.raises(NotAFileError):
        await svc.prepare_download("d", ["/sdcard/dir"])
    # 失败路径释放锁（不卡死后续传输）
    adb.entries["/sdcard/a.txt"] = make_entry("a.txt")
    adb.pull_contents["/sdcard/a.txt"] = b"x"
    job, _, _ = await svc.prepare_download("d", ["/sdcard/a.txt"])
    assert job.substage == "http"


async def test_download_busy_mutex(svc: FileService, adb: FakeAdb) -> None:
    adb.entries["/sdcard/a.txt"] = make_entry("a.txt")
    adb.pull_contents["/sdcard/a.txt"] = b"x"
    adb.pull_gate = asyncio.Event()
    task = asyncio.create_task(svc.prepare_download("d", ["/sdcard/a.txt"]))
    await asyncio.sleep(0.01)  # 让 pull 挂起
    with pytest.raises(TransferBusyError):
        await svc.prepare_download("d", ["/sdcard/a.txt"])
    # 别的设备不受影响
    adb2 = FakeAdb()
    adb2.entries["/sdcard/a.txt"] = make_entry("a.txt")
    adb2.pull_contents["/sdcard/a.txt"] = b"y"
    svc2 = FileService(adb=adb2)  # type: ignore[arg-type]
    job2, _, _ = await svc2.prepare_download("d2", ["/sdcard/a.txt"])
    assert job2.substage == "http"
    adb.pull_gate.set()
    job, _, _ = await task
    svc.finalize_download(job.id)


async def test_download_multi_zip(svc: FileService, adb: FakeAdb) -> None:
    adb.entries["/sdcard/a.txt"] = make_entry("a.txt", size=3)
    adb.entries["/sdcard/my b.txt"] = make_entry("my b.txt", size=5)
    adb.pull_contents["/sdcard/a.txt"] = b"AAA"
    adb.pull_contents["/sdcard/my b.txt"] = b"BBBBB"
    job, artifact, filename = await svc.prepare_download("d", ["/sdcard/a.txt", "/sdcard/my b.txt"])
    assert filename == "a.txt_etc.zip"
    assert job.total_size == 8
    with zipfile.ZipFile(artifact) as zf:
        assert sorted(zf.namelist()) == ["a.txt", "my b.txt"]
        assert zf.read("my b.txt") == b"BBBBB"
    svc.finalize_download(job.id)
    assert not artifact.exists()


async def test_download_pull_failure_releases_lock(svc: FileService, adb: FakeAdb) -> None:
    adb.entries["/sdcard/a.txt"] = make_entry("a.txt")
    # pull_contents 缺失 → pull 抛 AdbError
    with pytest.raises(AdbError):
        await svc.prepare_download("d", ["/sdcard/a.txt"])
    adb.pull_contents["/sdcard/a.txt"] = b"ok"
    job, _, _ = await svc.prepare_download("d", ["/sdcard/a.txt"])
    svc.finalize_download(job.id)


# ---------- 上传 ----------


def _chunk_reader(chunks: list[bytes]):
    it = iter(chunks)

    async def read(n: int) -> bytes:
        try:
            return next(it)
        except StopIteration:
            return b""

    return read


async def test_upload_success(svc: FileService, adb: FakeAdb) -> None:
    job = await svc.upload("d", "/sdcard/Download", "n.txt", _chunk_reader([b"x" * 10, b"x" * 10, b""]))
    assert job.status == "done"
    assert job.direction == "upload"
    assert job.total_size == 20
    _dev, _local, remote, timeout = adb.pushed[0]
    assert remote == "/sdcard/Download/n.txt"
    assert timeout == svc.TRANSFER_TIMEOUT
    # 即传即清：登记已清（目录实删由 download 用例经同一清理路径钉住）
    assert job.id not in svc._workdirs
    # 锁释放：可再次上传
    job2 = await svc.upload("d", "/data/local/tmp", "m", _chunk_reader([b"1"]))
    assert job2.status == "done"


async def test_upload_limit_413(svc: FileService, adb: FakeAdb) -> None:
    svc.MAX_UPLOAD_BYTES = 8
    with pytest.raises(FileTooLargeError):
        await svc.upload("d", "/sdcard", "big.bin", _chunk_reader([b"x" * 10, b"x" * 10]))
    assert adb.pushed == []
    assert not svc._workdirs  # 异常路径也即传即清（svc 为本用例私有实例）
    # 锁释放
    job = await svc.upload("d", "/sdcard", "ok.bin", _chunk_reader([b"x"]))
    assert job.status == "done"


async def test_upload_rejects_outside_whitelist(svc: FileService) -> None:
    with pytest.raises(PermissionDeniedError):
        await svc.upload("d", "/data/app", "a", _chunk_reader([b"x"]))


@pytest.mark.parametrize("bad", ["", ".", "..", "\x00", "/", "..\\..\\ev", "a\\b.txt", "\\abs"])
async def test_upload_bad_filename(svc: FileService, bad: str) -> None:
    with pytest.raises(InvalidPathError):
        await svc.upload("d", "/sdcard", bad, _chunk_reader([b"x"]))


async def test_upload_filename_basename(svc: FileService, adb: FakeAdb) -> None:
    job = await svc.upload("d", "/sdcard", "sub/dir/n.txt", _chunk_reader([b"x"]))
    assert job.status == "done"
    assert adb.pushed[0][2] == "/sdcard/n.txt"


# ---------- 重命名 / 删除 ----------


async def test_rename_success_and_conflict(svc: FileService, adb: FakeAdb) -> None:
    adb.entries["/sdcard/a"] = make_entry("a")
    await svc.rename("d", "/sdcard/a", "b b")
    assert adb.renamed == [("/sdcard/a", "/sdcard/b b")]
    adb.entries["/sdcard/b b"] = make_entry("b b")
    with pytest.raises(TargetExistsError):
        await svc.rename("d", "/sdcard/a", "b b")


@pytest.mark.parametrize("bad", ["", ".", "..", "x/y"])
async def test_rename_bad_new_name(svc: FileService, adb: FakeAdb, bad: str) -> None:
    adb.entries["/sdcard/a"] = make_entry("a")
    with pytest.raises(InvalidPathError):
        await svc.rename("d", "/sdcard/a", bad)
    assert adb.renamed == []


async def test_delete_preflight_whitelist(svc: FileService, adb: FakeAdb) -> None:
    with pytest.raises(PermissionDeniedError):
        await svc.delete("d", ["/sdcard/a", "/system/b"])
    assert adb.deleted == []  # 预检失败整体拒绝，一项都不执行


async def test_delete_mixed_results(svc: FileService, adb: FakeAdb) -> None:
    adb.entries["/sdcard/a"] = make_entry("a")
    adb.entries["/sdcard/dir"] = make_entry("dir", is_dir=True)
    adb.entries["/sdcard/link"] = make_entry("link", is_symlink=True)
    results = await svc.delete("d", ["/sdcard/a", "/sdcard/dir", "/sdcard/link", "/sdcard/gone"])
    assert [r["path"] for r in results] == ["/sdcard/a", "/sdcard/dir", "/sdcard/link", "/sdcard/gone"]
    assert results[0]["ok"] is True
    assert results[3]["ok"] is False and results[3]["error"]
    assert adb.deleted == [("/sdcard/a", False), ("/sdcard/dir", True), ("/sdcard/link", False)]


# ---------- 任务表 ----------


async def test_get_job_and_cross_device(svc: FileService, adb: FakeAdb) -> None:
    adb.entries["/sdcard/a.txt"] = make_entry("a.txt")
    adb.pull_contents["/sdcard/a.txt"] = b"x"
    job, _, _ = await svc.prepare_download("d", ["/sdcard/a.txt"])
    assert svc.get_job("d", job.id) is job
    with pytest.raises(JobNotFoundError):
        svc.get_job("other", job.id)


async def test_ttl_sweep(svc: FileService, adb: FakeAdb) -> None:
    adb.entries["/sdcard/a.txt"] = make_entry("a.txt")
    adb.pull_contents["/sdcard/a.txt"] = b"x"
    job, _, _ = await svc.prepare_download("d", ["/sdcard/a.txt"])
    svc.finalize_download(job.id)
    job.finished_at = time.time() - svc.JOB_TTL_SECONDS - 1
    # get_job 先 sweep 后查表：过期条目当场回收，查询落空
    with pytest.raises(JobNotFoundError):
        svc.get_job("d", job.id)
    assert job.id not in svc._jobs


async def test_orphan_tempdir_sweep(svc: FileService) -> None:
    stale = Path(tempfile.mkdtemp(prefix="scrcpy-filejob-"))
    old = time.time() - (svc.STALE_TMPDIR_MINUTES * 60 + 120)
    os.utime(stale, (old, old))
    fresh = Path(tempfile.mkdtemp(prefix="scrcpy-filejob-"))
    try:
        svc._sweep()
        assert not stale.exists()
        assert fresh.exists()
    finally:
        for d in (stale, fresh):
            if d.exists():
                d.rmdir()
