"""
文件管理服务
==============

层：应用。

方案 39 核心单元：目录浏览 / 上传 / 下载（多选 zip）/ 删除 / 重命名。

安全：读全盘、写白名单（/sdcard、/data/local/tmp）；路径 normalize 前、
shell quote 后双层防御；上传单文件 512MB；单设备单飞互斥
（TransferBusyError，对齐方案 35 连接治理语义）。

暂存：tempfile 目录（前缀 scrcpy-filejob-）。下载清理由 FileResponse
background 任务触发 finalize_download；客户端中断可能跳过 background，
故 _sweep 惰性清扫超 15 分钟的孤儿暂存目录兜底（已知限制缓解）。

任务表：内存 dict，done/failed 条目 60s TTL 惰性回收；互斥锁只覆盖
传输进行态（结束即释放，与 TTL 展示期解耦）。

application 层不 import fastapi：上传字节流经 read_chunk 回调注入
（http 层传 UploadFile.read）。
"""

import posixpath
import shlex
import shutil
import tempfile
import time
import uuid
import zipfile
from collections.abc import Awaitable, Callable
from pathlib import Path

from app.core.exceptions import (
    AdbError,
    FileTooLargeError,
    InvalidPathError,
    JobNotFoundError,
    NotAFileError,
    OpenScrcpyException,
    PathNotFoundError,
    PermissionDeniedError,
    TargetExistsError,
    TransferBusyError,
)
from app.domain.files import FileEntry, TransferJob
from app.domain.ports import AdbDriver


class FileService:
    """设备文件管理应用服务（经 deps 单例：任务表与互斥态进程内共享）。"""

    WRITABLE_ROOTS = ("/sdcard", "/data/local/tmp")
    # A9+ 实测回归（2026-10-21，.22）：/sdcard → /storage/self/primary →
    # /storage/emulated/0，视图本体不在白名单则合法写全被 E7 复核误伤 403。
    # 视图本体与链接路径同权限（adbd 经 /sdcard 见到的即是它），等价别名化。
    VIEW_ROOT_ALIASES = {"/storage/emulated/0": "/sdcard", "/storage/self/primary": "/sdcard"}
    MAX_UPLOAD_BYTES = 512 * 1024 * 1024
    TRANSFER_TIMEOUT = 900
    JOB_TTL_SECONDS = 60
    STALE_TMPDIR_MINUTES = 15
    CHUNK = 1024 * 1024

    def __init__(self, adb: AdbDriver) -> None:
        self._adb = adb
        self._jobs: dict[str, TransferJob] = {}
        self._workdirs: dict[str, Path] = {}
        # device_id → 传输进行态标记（互斥即治理）
        self._busy: set[str] = set()

    # ---------- 路径净化 ----------

    def normalize_path(self, path: str) -> str:
        """绝对化 + posix normpath；拒相对/空/NUL；消息不回显原值。"""
        if not path or "\x00" in path or not path.startswith("/"):
            raise InvalidPathError("path must be absolute")
        normed = posixpath.normpath(path)
        if "\x00" in normed or not normed.startswith("/"):
            raise InvalidPathError("path normalization failed")
        return normed

    def _ensure_writable(self, path: str) -> None:
        """写目标须落在白名单前缀内（root+"/" 比对，防 /sdcard_x 同头绕过）。"""
        for root in self.WRITABLE_ROOTS:
            if path == root or path.startswith(root + "/"):
                return
        raise PermissionDeniedError(f"写操作仅限白名单目录：{', '.join(self.WRITABLE_ROOTS)}")

    async def _resolve_real(self, device_id: str, path: str) -> str:
        """设备端真实路径解析（方案 51 E7：软链逃逸治理）。

        首选 `readlink -f`（GNU/toybox 新版全链解析）；A7.1 toybox -f 不
        兼容（.25 实测连真实目录也 rc=1，2026-10-21），退单级 `readlink`
        覆盖「操作目标本体是软链」主逃逸面（相对目标按 dirname 拼接）。
        两式皆失败（非链接/能力缺失）→ 回显原路径，保留 normalize+前缀
        文本防线（方案 48 检测能力降级同型）。
        """
        q = shlex.quote(path)
        try:
            real = (await self._adb.shell(device_id, f"readlink -f {q}")).strip()
            if real.startswith("/"):
                return real
        except AdbError:
            pass
        try:
            link = (await self._adb.shell(device_id, f"readlink {q}")).strip()
        except AdbError:
            return path
        if not link:
            return path
        if not link.startswith("/"):
            link = posixpath.join(posixpath.dirname(path), link)
        return posixpath.normpath(link)

    @classmethod
    def _canonicalize_real(cls, real: str) -> str:
        """/sdcard 的视图本体映射回白名单等效根（E7 误伤回归修正），其余原样。"""
        for alias, root in cls.VIEW_ROOT_ALIASES.items():
            if real == alias:
                return root
            if real.startswith(alias + "/"):
                return root + real[len(alias) :]
        return real

    async def _ensure_writable_real(self, device_id: str, path: str) -> None:
        """写操作前置复核：文本白名单校验 + 设备端真实路径复核（软链解析后仍须在白名单内）。"""
        self._ensure_writable(path)
        real = await self._resolve_real(device_id, path)
        self._ensure_writable(self._canonicalize_real(real))

    @staticmethod
    def _check_name(name: str) -> str:
        """文件名校验（上传在 basename 后调用；重命名直接调用）。
        拒 "/" 与 "\\"：Windows 上 Path 拼接把反斜杠当分隔符，须同面拦截（方案 51 E2）。"""
        if not name or name in (".", "..") or "\x00" in name:
            raise InvalidPathError("invalid file name")
        if "/" in name or "\\" in name:
            raise InvalidPathError("invalid file name")
        return name

    @staticmethod
    def _raise_mapped_ls_error(exc: AdbError, path: str) -> None:
        """设备端 ls 错误文本 → 领域异常（404/403，其余原样上抛）。"""
        msg = str(exc)
        if "No such file or directory" in msg or "Not a directory" in msg:
            raise PathNotFoundError(path) from exc
        if "Permission denied" in msg:
            raise PermissionDeniedError("设备端读取该路径被拒绝") from exc
        raise exc

    # ---------- 浏览 ----------

    async def list_dir(self, device_id: str, path: str) -> list[FileEntry]:
        """列目录（读全盘不设白名单；错误文本映射 404/403）。

        `ls -la` 的 `.`/`..` 自描述条目在服务段过滤，不下发前端（API.md 契约）。
        """
        normed = self.normalize_path(path)
        try:
            entries = await self._adb.list_dir(device_id, normed)
        except AdbError as exc:
            self._raise_mapped_ls_error(exc, normed)
            raise  # unreachable（_raise_mapped 必抛）；满足 mypy 路径检查
        return [e for e in entries if e.name not in (".", "..")]

    async def _stat_or_raise(self, device_id: str, normed: str) -> FileEntry:
        try:
            return await self._adb.stat_size(device_id, normed)
        except AdbError as exc:
            self._raise_mapped_ls_error(exc, normed)
            raise  # unreachable

    # ---------- 互斥 ----------

    def _acquire(self, device_id: str) -> None:
        if device_id in self._busy:
            raise TransferBusyError()
        self._busy.add(device_id)

    def _release(self, device_id: str) -> None:
        self._busy.discard(device_id)

    @staticmethod
    def _unique_arcname(name: str, used: set[str]) -> str:
        """zip arcname 去重：重名在扩展名前插入 _1/_2… 序号（方案 51 E10）。"""
        if name not in used:
            used.add(name)
            return name
        stem, ext = posixpath.splitext(name)
        i = 1
        while f"{stem}_{i}{ext}" in used:
            i += 1
        deduped = f"{stem}_{i}{ext}"
        used.add(deduped)
        return deduped

    # ---------- 下载 ----------

    async def prepare_download(
        self, device_id: str, paths: list[str]
    ) -> tuple[TransferJob, Path, str]:
        """建任务并 pull（多选则 zip）至暂存；返回 (job, 本地产物, 下载文件名)。

        设备锁在成功返回后仍由传输持有——http 层 FileResponse background
        调 finalize_download 收尾（清暂存 + 释放）；异常路径本函数内释放。
        """
        if not paths:
            raise InvalidPathError("empty path list")
        normed = [self.normalize_path(p) for p in paths]
        self._acquire(device_id)
        job = self._new_job(device_id, "download", normed)
        tmp = Path(tempfile.mkdtemp(prefix="scrcpy-filejob-"))
        self._workdirs[job.id] = tmp
        try:
            if len(normed) == 1:
                entry = await self._stat_or_raise(device_id, normed[0])
                if entry.is_dir:
                    raise NotAFileError(normed[0])
                job.total_size = entry.size
                # ls -ld 的 name 是全路径（实测），产物名以本地 basename 为准，
                # 防 pathlib 把绝对路径拼出 tmp 外
                filename = posixpath.basename(normed[0])
                job.substage = "pulling"
                artifact = tmp / filename
                await self._adb.pull(
                    device_id, normed[0], str(artifact), timeout=self.TRANSFER_TIMEOUT
                )
            else:
                job.substage = "zipping"
                artifact = tmp / "transfer.zip"
                total = 0
                used_arcs: set[str] = set()
                with zipfile.ZipFile(artifact, "w", zipfile.ZIP_DEFLATED) as zf:
                    for p in normed:
                        entry = await self._stat_or_raise(device_id, p)
                        if entry.is_dir:
                            raise NotAFileError(p)
                        total += entry.size
                        raw = tmp / f"raw_{uuid.uuid4().hex}"
                        await self._adb.pull(
                            device_id, p, str(raw), timeout=self.TRANSFER_TIMEOUT
                        )
                        # 方案 51 E10：重名 arcname 追加 _1/_2 序号——多数解压
                        # 工具对重复条目只保留其一，多选同名文件下载会静默吞条目
                        zf.write(raw, arcname=self._unique_arcname(posixpath.basename(p), used_arcs))
                        raw.unlink()  # 即打包即清（磁盘保留策略）
                job.total_size = total
                filename = f"{posixpath.basename(normed[0])}_etc.zip"
            job.substage = "http"
            return job, artifact, filename
        except Exception as exc:
            job.status = "failed"
            job.error = str(exc)
            job.finished_at = time.time()
            self._workdirs.pop(job.id, None)
            shutil.rmtree(tmp, ignore_errors=True)
            self._release(device_id)
            raise

    def finalize_download(self, job_id: str) -> None:
        """响应发送完成后收尾：删暂存、任务置 done、释放设备锁。"""
        job = self._jobs.get(job_id)
        if job is None:
            return
        workdir = self._workdirs.pop(job_id, None)
        if workdir is not None:
            shutil.rmtree(workdir, ignore_errors=True)
        if job.status == "running":
            job.status = "done"
        job.finished_at = time.time()
        self._release(job.device_id)

    # ---------- 上传 ----------

    async def upload(
        self,
        device_id: str,
        dir_path: str,
        filename: str,
        read_chunk: Callable[[int], Awaitable[bytes]],
    ) -> TransferJob:
        """流式接收→暂存→push→即清。read_chunk 由 http 层注入（UploadFile.read）。"""
        target_dir = self.normalize_path(dir_path)
        self._ensure_writable(target_dir)
        name = self._check_name(posixpath.basename(filename or ""))
        target = posixpath.join(target_dir, name)
        # 方案 51 E7：落点全链解析复核（父目录与同名逃逸软链一并覆盖）
        await self._ensure_writable_real(device_id, target)
        self._acquire(device_id)
        job = self._new_job(device_id, "upload", [target])
        tmp = Path(tempfile.mkdtemp(prefix="scrcpy-filejob-"))
        self._workdirs[job.id] = tmp
        job.substage = "http"
        written = 0
        try:
            local = tmp / name
            # 纵深防御：即便名校验面未来再漏，落盘必须仍在暂存目录内（方案 51 E2）
            if local.resolve().parent != tmp.resolve():
                raise InvalidPathError("invalid file name")
            with local.open("wb") as sink:
                while True:
                    chunk = await read_chunk(self.CHUNK)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > self.MAX_UPLOAD_BYTES:
                        raise FileTooLargeError(self.MAX_UPLOAD_BYTES)
                    sink.write(chunk)
            job.total_size = written
            job.substage = "pushing"
            await self._adb.push(
                device_id, str(local), target,
                timeout=self.TRANSFER_TIMEOUT,
            )
            job.status = "done"
            job.finished_at = time.time()
            return job
        except Exception as exc:
            job.status = "failed"
            job.error = str(exc)
            job.finished_at = time.time()
            raise
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
            self._workdirs.pop(job.id, None)
            self._release(device_id)

    # ---------- 重命名 / 删除 ----------

    async def rename(self, device_id: str, path: str, new_name: str) -> None:
        """同目录重命名：源须存在、目标须不存在、两者均在写白名单内。"""
        normed = self.normalize_path(path)
        self._ensure_writable(normed)
        name = self._check_name(new_name)
        target = posixpath.join(posixpath.dirname(normed), name)
        # 方案 51 E7：源与落点设备端真实路径复核（拦父组件/本体软链逃逸）
        await self._ensure_writable_real(device_id, normed)
        await self._ensure_writable_real(device_id, target)
        await self._stat_or_raise(device_id, normed)
        try:
            await self._adb.stat_size(device_id, target)
        except AdbError:
            pass  # 目标不存在（ls 非零退出）——可重命名
        else:
            raise TargetExistsError(target)
        await self._adb.rename_path(device_id, normed, target)

    async def delete(self, device_id: str, paths: list[str]) -> list[dict[str, object]]:
        """删除：先全量白名单预检（任一越界整体 403），再逐项执行汇总。"""
        normed = [self.normalize_path(p) for p in paths]
        for p in normed:
            # 方案 51 E7：预检含设备端真实路径复核（软链逃逸整体 403）
            await self._ensure_writable_real(device_id, p)
        results: list[dict[str, object]] = []
        for p in normed:
            try:
                entry = await self._stat_or_raise(device_id, p)
                is_dir = entry.is_dir and not entry.is_symlink  # 软链 rm -f 不递归目标
                await self._adb.delete_path(device_id, p, is_dir)
                results.append({"path": p, "ok": True, "error": None})
            except OpenScrcpyException as exc:
                results.append({"path": p, "ok": False, "error": str(exc)})
        return results

    # ---------- 任务表 ----------

    def get_job(self, device_id: str, job_id: str) -> TransferJob:
        """查询任务（惰性回收过期条目）；跨设备探测按不存在处理。"""
        self._sweep()
        job = self._jobs.get(job_id)
        if job is None or job.device_id != device_id:
            raise JobNotFoundError(job_id)
        return job

    def _new_job(self, device_id: str, direction: str, paths: list[str]) -> TransferJob:
        self._sweep()
        job = TransferJob(
            id=uuid.uuid4().hex,
            device_id=device_id,
            direction=direction,
            paths=list(paths),
            total_size=0,
            created_at=time.time(),
        )
        self._jobs[job.id] = job
        return job

    def _sweep(self) -> None:
        """惰性回收：running 超时强制收敛；过期任务条目出表；超时孤儿暂存目录删除。"""
        now = time.time()
        # 滞留 running（如 FileResponse background 收尾被跳过）会永久占设备互斥，
        # 超 TRANSFER_TIMEOUT+120 宽限窗强制置 failed 并释放（方案 51 E8）
        for j in self._jobs.values():
            if j.status == "running" and now - j.created_at > self.TRANSFER_TIMEOUT + 120:
                j.status = "failed"
                j.error = "传输超时未回收（已强制收敛）"
                j.finished_at = now
                self._release(j.device_id)
                workdir = self._workdirs.pop(j.id, None)
                if workdir is not None:
                    shutil.rmtree(workdir, ignore_errors=True)
        expired = [
            jid
            for jid, j in self._jobs.items()
            if j.status != "running" and j.finished_at is not None
            and now - j.finished_at > self.JOB_TTL_SECONDS
        ]
        for jid in expired:
            self._jobs.pop(jid, None)
            workdir = self._workdirs.pop(jid, None)
            if workdir is not None:
                shutil.rmtree(workdir, ignore_errors=True)
        cutoff = now - self.STALE_TMPDIR_MINUTES * 60
        root = Path(tempfile.gettempdir())
        try:
            candidates = [d for d in root.glob("scrcpy-filejob-*") if d.is_dir()]
        except OSError:
            return
        for d in candidates:
            if d.name in {p.name for p in self._workdirs.values()}:
                continue  # 仍被进行态任务引用
            try:
                if d.stat().st_mtime < cutoff:
                    shutil.rmtree(d, ignore_errors=True)
            except OSError:
                continue
