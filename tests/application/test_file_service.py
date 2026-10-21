"""FileService 单测（方案 39 T2）。

覆盖：路径净化与白名单矩阵、ls 错误映射、下载单文件/多选 zip、
互斥 TransferBusy、暂存即清、上传流式与 413、重命名/删除（含软链 rm -f）、
任务表 TTL 与孤儿暂存清扫。FakeAdb 走 AdbDriver Protocol 鸭子替身。
"""

import asyncio
import os
import shlex
import shutil
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
from app.domain.files import FileEntry, TransferJob


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
    """AdbDriver 替身：entries 驱动 stat/list；pull 落假内容；push/rename/delete 记录。

    shell 为方案 51 E7 readlink -f 替身：real_paths 未配置的键回显原路径
    （即无软链语义，存量用例行为不变）。
    """

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
        self.real_paths: dict[str, str] = {}
        self.shells: list[str] = []
        self.shell_raises = False
        self.readlink_f_broken = False

    async def shell(self, device_id: str, cmd: str) -> str:
        self.shells.append(cmd)
        if self.shell_raises:
            raise AdbError("readlink: command not found")
        tokens = shlex.split(cmd)
        path = tokens[-1]
        if "-f" in tokens:
            if self.readlink_f_broken:  # A7.1 形态：-f 连真实路径也 rc=1
                raise AdbError("readlink: rc=1")
            if path in self.real_paths:
                return self.real_paths[path]
            # 模拟 readlink -f 全链解析：父目录命中登记项则前缀替换
            for k, v in self.real_paths.items():
                if path.startswith(k + "/"):
                    return v + path[len(k):]
            return path  # GNU 语义：非链接也规范化回显
        # 单级 readlink：仅链接本体有输出，非链接 rc≠0
        if path in self.real_paths:
            return self.real_paths[path]
        raise AdbError("readlink: rc=1 (not a symlink)")

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


async def test_download_multi_zip_dedup_arcnames(svc: FileService, adb: FakeAdb) -> None:
    # 方案 51 E10：不同目录同名文件多选下载，arcname 追加 _1/_2 序号，互不吞条目
    for i, size in ((1, 3), (2, 5), (3, 7)):
        path = f"/sdcard/{i}/a.txt"
        adb.entries[path] = make_entry("a.txt", size=size)
        adb.pull_contents[path] = b"x" * size
    job, artifact, _ = await svc.prepare_download(
        "d", ["/sdcard/1/a.txt", "/sdcard/2/a.txt", "/sdcard/3/a.txt"]
    )
    with zipfile.ZipFile(artifact) as zf:
        assert zf.namelist() == ["a.txt", "a_1.txt", "a_2.txt"]
        assert zf.read("a_2.txt") == b"x" * 7
    assert job.total_size == 15
    svc.finalize_download(job.id)


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


# ---------- 方案 51 E7：设备端 readlink -f 真实路径复核（软链逃逸治理）----------


async def test_delete_symlink_escape_403(svc: FileService, adb: FakeAdb) -> None:
    """/sdcard/link 解析到白名单外 → 预检整体 403，一项不执行。"""
    adb.entries["/sdcard/link"] = make_entry("link", is_symlink=True)
    adb.real_paths["/sdcard/link"] = "/system/app/x"
    with pytest.raises(PermissionDeniedError):
        await svc.delete("d", ["/sdcard/link"])
    assert adb.deleted == []


async def test_delete_link_targeting_inside_whitelist_ok(svc: FileService, adb: FakeAdb) -> None:
    """链接解析后仍在白名单内（/sdcard 内互链）→ 放行，rm 语义同旧。"""
    adb.entries["/sdcard/link"] = make_entry("link", is_symlink=True)
    adb.real_paths["/sdcard/link"] = "/sdcard/other"
    results = await svc.delete("d", ["/sdcard/link"])
    assert results[0]["ok"] is True
    assert adb.deleted == [("/sdcard/link", False)]


async def test_delete_escaped_parent_component_403(svc: FileService, adb: FakeAdb) -> None:
    """父目录经软链逃逸：/sdcard/ld → /data，删 /sdcard/ld/f 解析出白名单外 → 403。"""
    adb.entries["/sdcard/ld/f"] = make_entry("f")
    adb.real_paths["/sdcard/ld/f"] = "/data/f"
    with pytest.raises(PermissionDeniedError):
        await svc.delete("d", ["/sdcard/ld/f"])
    assert adb.deleted == []


async def test_rename_source_or_target_escape_403(svc: FileService, adb: FakeAdb) -> None:
    """重命名源解析到白名单外 → 403，不发 mv。"""
    adb.entries["/sdcard/link"] = make_entry("link", is_symlink=True)
    adb.real_paths["/sdcard/link"] = "/system/app/x"
    with pytest.raises(PermissionDeniedError):
        await svc.rename("d", "/sdcard/link", "b")
    assert adb.renamed == []


async def test_upload_dest_dir_escape_403(svc: FileService, adb: FakeAdb) -> None:
    """上传目标目录本身是逃逸软链 → 403，不 push。"""
    adb.real_paths["/sdcard/up"] = "/system/etc"
    with pytest.raises(PermissionDeniedError):
        await svc.upload("d", "/sdcard/up", "n.txt", _chunk_reader([b"x"]))
    assert adb.pushed == []


async def test_upload_name_collision_with_escape_link_403(svc: FileService, adb: FakeAdb) -> None:
    """落点文件位已是逃逸软链（push 会跟随写目标）→ 复核完整落点 403。"""
    adb.real_paths["/sdcard/Download/n.txt"] = "/system/app/n.txt"
    with pytest.raises(PermissionDeniedError):
        await svc.upload(
            "d", "/sdcard/Download", "n.txt", _chunk_reader([b"x"])
        )
    assert adb.pushed == []


async def test_write_ops_pass_with_normal_paths(svc: FileService, adb: FakeAdb) -> None:
    """正常真实路径（readlink 回显原路径）→ 复核不改变既有行为。"""
    adb.entries["/sdcard/a"] = make_entry("a")
    await svc.rename("d", "/sdcard/a", "b")
    assert adb.renamed == [("/sdcard/a", "/sdcard/b")]
    job = await svc.upload("d", "/sdcard", "m", _chunk_reader([b"x"]))
    assert job.status == "done"


async def test_sdcard_view_alias_legit_writes_pass(svc: FileService, adb: FakeAdb) -> None:
    """E7 真机回归（.22/A9 实测）：/sdcard 视图本体 /storage/emulated/0 等价别名化，
    合法写不再被复核误伤；A7.1 单级兜底形态（→/storage/self/primary）同样放行。"""
    adb.real_paths["/sdcard"] = "/storage/emulated/0"
    job = await svc.upload("d", "/sdcard", "v.txt", _chunk_reader([b"x"]))
    assert job.status == "done"
    # 别名只影响复核，实际写入路径仍是用户语义的 /sdcard/*
    assert adb.pushed[-1][2] == "/sdcard/v.txt"
    # 视图本体下的子路径解析（前缀替换形态）也放行
    job = await svc.upload("d", "/sdcard/Download", "w.txt", _chunk_reader([b"x"]))
    assert job.status == "done"
    # A7.1 形态：-f rc=1 → 单级 readlink 出 /storage/self/primary，别名后放行
    adb.readlink_f_broken = True
    job = await svc.upload("d", "/sdcard", "u.txt", _chunk_reader([b"x"]))
    assert job.status == "done"


async def test_readlink_f_broken_falls_back_to_single_level(svc: FileService, adb: FakeAdb) -> None:
    """.25 实测 A7.1 形态：-f rc=1 → 单级 readlink 兜底仍拦逃逸；非链接降级放行。"""
    adb.readlink_f_broken = True
    adb.entries["/data/local/tmp/link"] = make_entry("link", is_symlink=True)
    adb.real_paths["/data/local/tmp/link"] = "/system/app"
    with pytest.raises(PermissionDeniedError):
        await svc.delete("d", ["/data/local/tmp/link"])
    assert adb.deleted == []
    # 非链接（-f 与单级皆 rc=1）→ 回显原路径，白名单内照常执行
    adb.entries["/sdcard/ok.txt"] = make_entry("ok.txt")
    results = await svc.delete("d", ["/sdcard/ok.txt"])
    assert results[0]["ok"] is True
    assert adb.deleted == [("/sdcard/ok.txt", False)]


async def test_readlink_unavailable_degrades_to_text_check(svc: FileService, adb: FakeAdb) -> None:
    """ROM 无 readlink（shell 抛 AdbError）→ 降级保留原文本防线，不阻断写。"""
    adb.shell_raises = True
    adb.entries["/sdcard/a"] = make_entry("a")
    await svc.rename("d", "/sdcard/a", "b")
    assert adb.renamed == [("/sdcard/a", "/sdcard/b")]
    # 白名单外的文本拒绝仍然生效（降级不放行越界）
    with pytest.raises(PermissionDeniedError):
        await svc.rename("d", "/system/a", "b")


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


# ---------- running 超时强制回收（方案 51 E8）----------


async def test_sweep_reclaims_stale_running_job(svc: FileService) -> None:
    """background 收尾被跳过等原因滞留 running 的任务：超 TRANSFER_TIMEOUT+120
    由 sweep 强制置 failed，释放设备互斥并清暂存目录（否则该设备永久 TransferBusy）。"""
    job = TransferJob(
        id="j1",
        device_id="d",
        direction="download",
        paths=["/sdcard/a"],
        total_size=0,
        status="running",
        substage="pulling",
        created_at=time.time() - svc.TRANSFER_TIMEOUT - 121,
    )
    svc._jobs["j1"] = job
    wd = Path(tempfile.mkdtemp(prefix="scrcpy-filejob-"))
    svc._workdirs["j1"] = wd
    svc._busy.add("d")
    svc._sweep()
    assert job.status == "failed"
    assert job.error
    assert job.finished_at is not None
    assert "d" not in svc._busy
    assert not wd.exists()
    assert "j1" not in svc._workdirs


async def test_sweep_keeps_fresh_running_job(svc: FileService) -> None:
    job = TransferJob(
        id="j2",
        device_id="d",
        direction="download",
        paths=["/sdcard/a"],
        total_size=0,
        status="running",
        substage="pulling",
        created_at=time.time() - svc.TRANSFER_TIMEOUT - 60,  # +120 宽限窗内
    )
    svc._jobs["j2"] = job
    svc._busy.add("d")
    svc._sweep()
    assert job.status == "running"
    assert job.finished_at is None
    assert "d" in svc._busy
    assert svc.get_job("d", "j2") is job
