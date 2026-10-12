"""文件管理 HTTP 端点契约测试（方案 39 T3）。

FakeFileService 覆盖依赖；断言路由形状、X-File-Job 头、Content-Disposition、
multipart 字段透传、任务查询与领域异常 → HTTP 状态码/错误码映射。
"""

import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from app.core.exceptions import (
    FileTooLargeError,
    InvalidPathError,
    JobNotFoundError,
    NotAFileError,
    PathNotFoundError,
    PermissionDeniedError,
    TargetExistsError,
    TransferBusyError,
)
from app.deps import get_file_service
from app.domain.files import FileEntry, TransferJob
from tests.unit.http_testkit import make_client, override

URL = "/api/devices/dev%3A5555"
DEV = "dev:5555"


class FakeFileService:
    """记录调用、返回预置结果的 FileService 替身。"""

    def __init__(self) -> None:
        self.entries: list[FileEntry] = [
            FileEntry(
                name="Download", is_dir=True, is_symlink=False, size=3452,
                mtime="2026-10-12 09:12", permissions="drwxr-x---", link_target=None,
            ),
            FileEntry(
                name="a b.txt", is_dir=False, is_symlink=False, size=5,
                mtime="2026-10-12 09:13", permissions="-rw-r--r--", link_target=None,
            ),
        ]
        self.listed: list[tuple[str, str]] = []
        self.downloaded: list[tuple[str, list[str]]] = []
        self.uploaded: list[tuple[str, str, str]] = []
        self.renamed: list[tuple[str, str, str]] = []
        self.deleted: list[tuple[str, list[str]]] = []
        self.jobs: dict[str, TransferJob] = {}
        self.artifact: Path | None = None
        self.finalized: list[str] = []
        self.raise_on: str | None = None
        self.errors: dict[str, Exception] = {}

    def _maybe_raise(self, method: str) -> None:
        if self.raise_on == method:
            raise self.errors[method]

    async def list_dir(self, device_id: str, path: str) -> list[FileEntry]:
        self.listed.append((device_id, path))
        self._maybe_raise("list_dir")
        return self.entries

    async def prepare_download(
        self, device_id: str, paths: list[str]
    ) -> tuple[TransferJob, Path, str]:
        self.downloaded.append((device_id, list(paths)))
        self._maybe_raise("prepare_download")
        assert self.artifact is not None
        job = TransferJob(
            id="job-1", device_id=device_id, direction="download",
            paths=list(paths), total_size=5, created_at=1.0,
        )
        self.jobs[job.id] = job
        return job, self.artifact, "a b.txt"

    def finalize_download(self, job_id: str) -> None:
        self.finalized.append(job_id)

    async def upload(
        self,
        device_id: str,
        dir_path: str,
        filename: str,
        read_chunk: Callable[[int], Awaitable[bytes]],
    ) -> TransferJob:
        data = await read_chunk(1 << 20)
        self.uploaded.append((device_id, dir_path, filename))
        self._maybe_raise("upload")
        job = TransferJob(
            id="job-2", device_id=device_id, direction="upload",
            paths=[dir_path], total_size=len(data), status="done",
            created_at=1.0, finished_at=2.0,
        )
        self.jobs[job.id] = job
        return job

    async def rename(self, device_id: str, path: str, new_name: str) -> None:
        self.renamed.append((device_id, path, new_name))
        self._maybe_raise("rename")

    async def delete(self, device_id: str, paths: list[str]) -> list[dict[str, Any]]:
        self.deleted.append((device_id, list(paths)))
        self._maybe_raise("delete")
        return [{"path": p, "ok": True, "error": None} for p in paths]

    def get_job(self, device_id: str, job_id: str) -> TransferJob:
        self._maybe_raise("get_job")
        job = self.jobs.get(job_id)
        if job is None or job.device_id != device_id:
            raise JobNotFoundError(job_id)
        return job


@pytest.fixture()
def client():
    fake = FakeFileService()
    fd, raw = tempfile.mkstemp(suffix=".bin")
    Path(raw).write_bytes(b"hello")
    import os

    os.close(fd)
    fake.artifact = Path(raw)
    with override(get_file_service, fake):
        yield make_client(), fake
    Path(raw).unlink(missing_ok=True)


def test_list_files_ok(client):
    c, fake = client
    res = c.get(f"{URL}/files", params={"path": "/sdcard"})
    assert res.status_code == 200
    assert fake.listed == [(DEV, "/sdcard")]
    body = res.json()
    assert body[0]["name"] == "Download" and body[0]["is_dir"] is True
    assert body[1]["name"] == "a b.txt" and body[1]["link_target"] is None
    assert set(body[0]) == {
        "name", "is_dir", "is_symlink", "size", "mtime", "permissions", "link_target"
    }


def test_list_files_default_path(client):
    c, fake = client
    res = c.get(f"{URL}/files")
    assert res.status_code == 200
    assert fake.listed == [(DEV, "/sdcard")]


def test_download_single(client):
    c, fake = client
    res = c.get(f"{URL}/file/download", params={"paths": ["/sdcard/a b.txt"]})
    assert res.status_code == 200
    assert res.content == b"hello"
    assert res.headers["x-file-job"] == "job-1"
    cd = res.headers["content-disposition"]
    assert "attachment" in cd and "b.txt" in cd
    assert fake.downloaded == [(DEV, ["/sdcard/a b.txt"])]
    # background 在 TestClient 响应消费后执行
    assert fake.finalized == ["job-1"]


def test_download_multi_order_preserved(client):
    c, fake = client
    res = c.get(f"{URL}/file/download", params={"paths": ["/sdcard/b", "/sdcard/a"]})
    assert res.status_code == 200
    assert fake.downloaded == [(DEV, ["/sdcard/b", "/sdcard/a"])]


def test_download_requires_paths(client):
    c, _ = client
    res = c.get(f"{URL}/file/download")
    assert res.status_code == 422


def test_upload_multipart(client):
    c, fake = client
    res = c.post(
        f"{URL}/file/upload",
        params={"dir": "/sdcard/Download"},
        files={"file": ("n.txt", b"payload", "application/octet-stream")},
    )
    assert res.status_code == 200
    assert fake.uploaded == [(DEV, "/sdcard/Download", "n.txt")]
    body = res.json()
    assert body["id"] == "job-2" and body["status"] == "done"
    assert body["direction"] == "upload" and body["total_size"] == 7


def test_rename(client):
    c, fake = client
    res = c.post(f"{URL}/file/rename", json={"path": "/sdcard/a", "new_name": "b"})
    assert res.status_code == 200 and res.json() == {"ok": True}
    assert fake.renamed == [(DEV, "/sdcard/a", "b")]


def test_delete(client):
    c, fake = client
    res = c.post(f"{URL}/file/delete", json={"paths": ["/sdcard/a", "/sdcard/b"]})
    assert res.status_code == 200
    assert fake.deleted == [(DEV, ["/sdcard/a", "/sdcard/b"])]
    assert res.json()["results"][1]["path"] == "/sdcard/b"


def test_get_job(client):
    c, fake = client
    fake.jobs["job-9"] = TransferJob(
        id="job-9", device_id=DEV, direction="download", paths=["/x"],
        total_size=100, status="running", substage="pulling", created_at=1.0,
    )
    res = c.get(f"{URL}/file-jobs/job-9")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "running" and body["substage"] == "pulling"
    assert body["total_size"] == 100 and body["error"] is None
    res2 = c.get(f"{URL}/file-jobs/nope")
    assert res2.status_code == 404


@pytest.mark.parametrize(
    "exc,status,code",
    [
        (TransferBusyError(), 409, "TRANSFER_BUSY"),
        (PermissionDeniedError("no"), 403, "PERMISSION_DENIED"),
        (PathNotFoundError("/x"), 404, "PATH_NOT_FOUND"),
        (FileTooLargeError(10), 413, "FILE_TOO_LARGE"),
        (InvalidPathError("abs"), 400, "INVALID_PATH"),
        (NotAFileError("/x"), 400, "NOT_A_FILE"),
        (TargetExistsError("/x"), 409, "TARGET_EXISTS"),
    ],
)
def test_error_mapping(client, exc, status, code):
    c, fake = client
    fake.raise_on = "list_dir"
    fake.errors = {"list_dir": exc}
    res = c.get(f"{URL}/files")
    assert res.status_code == status
    assert res.json()["error"]["code"] == code
