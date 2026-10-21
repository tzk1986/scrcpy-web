#!/usr/bin/env python3
"""方案 39 spike：设备文件管理真机验收（非 pytest，不入 CI）。

数据流：直接驱动应用层 FileService + 基础设施层 AdbCliDriver（不起 HTTP），
与 tests/manual/spike_37b_recording.py 同范式。spike 只验收不改产品码。

用法（仓库根，Git Bash）：
    MSYS_NO_PATHCONV=1 PYTHONPATH=backend python -u tests/manual/spike_39_files.py
    MSYS_NO_PATHCONV=1 PYTHONPATH=backend python -u tests/manual/spike_39_files.py \
        --device 192.168.8.25:5555 --out /tmp/s39.json

判据（逐项写入输出 JSON 的 verdicts）：
    S39-1 ls 三态解析：普通/空格名/符号链接（link_target 分离）+ 目录语义
    S39-2 上传字节一致：2MB 随机 → upload → 设备端 sha256 比对
    S39-3 下载单文件字节一致：prepare_download 产物 hash 比对 + finalize 暂存即清
    S39-4 多选 zip：两文件 zip 解包内容一致 + zip 命名
    S39-5 重命名：成功改名 + 目标已存在 TargetExistsError
    S39-6 删除：文件 rm -f / 目录 rm -r / 软链 rm -f 且链接目标不受影响
    S39-7 白名单：/data 写 403；/proc 读放行（读全盘）
    S39-8 注入面：new_name "../evil" InvalidPathError；shell 元字符名文件
          正常落盘且不被 shell 执行
    S39-9 非 UTF-8 名：设备端造坏字节名 → list_dir 替换符呈现不崩

退出码：存在 FAIL 判据 → 1；全 pass/观察 → 0。
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import shlex
import sys
import tempfile
import time
import zipfile
from pathlib import Path

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    # 判据 detail 可含 U+FFFD 等非 GBK 字符（S39-9 坏名呈现），防 print 崩
    sys.stdout.reconfigure(errors="replace")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))  # config 包在仓库根（与 run_server.py 同口径）

from app.application.file_service import FileService  # noqa: E402
from app.core.exceptions import (  # noqa: E402
    InvalidPathError,
    PermissionDeniedError,
    TargetExistsError,
)
from app.infrastructure.adb.cli import AdbCliDriver  # noqa: E402

SPIKE_DIR = "/sdcard/Download/s39_spike"


def _q(s: str) -> str:
    return shlex.quote(s)


def chunk_reader(local: Path):
    """返回按 CHUNK 顺序吐字节的 read_chunk 回调（读完吐 b''）。"""
    it = local.open("rb")

    async def read_chunk(n: int) -> bytes:
        data = it.read(n)
        if not data:
            it.close()
        return data

    return read_chunk


class Verdicts:
    def __init__(self) -> None:
        self.items: list[dict[str, str]] = []

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        status = "PASS" if ok else "FAIL"
        self.items.append({"name": name, "status": status, "detail": detail})
        print(f"[{status}] {name} {detail}", flush=True)

    @property
    def failed(self) -> int:
        return sum(1 for i in self.items if i["status"] == "FAIL")


def local_digest(path: Path, algo: str) -> str:
    h = hashlib.new(algo)
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


async def device_digest(adb: AdbCliDriver, dev: str, remote: str) -> tuple[str, str]:
    """设备端文件摘要（sha256sum 优先；部分机型 toybox 未编入，回退 md5sum）。"""
    try:
        out = await adb.shell(dev, f"sha256sum {_q(remote)}")
        algo = "sha256"
    except Exception:
        out = await adb.shell(dev, f"md5sum {_q(remote)}")
        algo = "md5"
    parts = out.split()
    return algo, (parts[0] if parts else "")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", default="192.168.8.25:5555")
    ap.add_argument("--out", default="/tmp/s39_files.json")
    args = ap.parse_args()

    dev = args.device
    adb = AdbCliDriver()
    svc = FileService(adb=adb)
    v = Verdicts()
    tmp_root = Path(tempfile.mkdtemp(prefix="s39_local_"))

    devices = await adb.list_device_states()
    if dev not in devices:
        print(f"设备 {dev} 不在线（在线：{devices}）", flush=True)
        return 2

    try:
        await adb.shell(dev, f"rm -rf {_q(SPIKE_DIR)}; mkdir -p {_q(SPIKE_DIR)}")

        # ---- S39-1 ls 三态解析 ----
        # ln -sf 分文件系统：ext4 挂载允许（.18），fuse/sdcardfs 拒绝（.25/.33）
        # ——软链判据自适应，不因环境不支持而失败（S39-6 软链分支同步跟随）
        await adb.shell(
            dev,
            f"touch {_q(SPIKE_DIR + '/plain.txt')}; "
            f"echo hi > {_q(SPIKE_DIR + '/spike file 1.txt')}; "
            f"mkdir -p {_q(SPIKE_DIR + '/sub')}",
        )
        link_ok = True
        try:
            await adb.shell(dev, f"ln -sf {_q(SPIKE_DIR)} {_q(SPIKE_DIR + '/link')}")
        except Exception:
            link_ok = False
        entries = await svc.list_dir(dev, SPIKE_DIR)
        by_name = {e.name: e for e in entries}
        ok1 = (
            "plain.txt" in by_name
            and "spike file 1.txt" in by_name
            and "sub" in by_name and by_name["sub"].is_dir
            and not any(e.name in (".", "..") for e in entries)
            and (
                not link_ok
                or (
                    "link" in by_name and by_name["link"].is_symlink
                    and by_name["link"].link_target == SPIKE_DIR
                )
            )
        )
        v.add("S39-1 ls 三态解析", ok1, f"entries={sorted(by_name)} symlink_env_ok={link_ok}")

        # ---- S39-2 上传字节一致（2MB 随机） ----
        local_big = tmp_root / "big.bin"
        local_big.write_bytes(os.urandom(2 * 1024 * 1024))
        t0 = time.monotonic()
        job = await svc.upload(dev, SPIKE_DIR, "big.bin", chunk_reader(local_big))
        up_s = time.monotonic() - t0
        algo, remote_hash = await device_digest(adb, dev, f"{SPIKE_DIR}/big.bin")
        expected_hash = local_digest(local_big, algo)
        hash_ok = job.status == "done" and remote_hash == expected_hash
        v.add("S39-2 上传字节一致", hash_ok, f"status={job.status} {up_s:.2f}s {algo}={'ok' if hash_ok else remote_hash[:16] + '≠' + expected_hash[:16]}")

        # ---- S39-3 下载单文件字节一致 + 暂存即清 ----
        job_d, artifact, filename = await svc.prepare_download(dev, [f"{SPIKE_DIR}/big.bin"])
        dl_ok = artifact.read_bytes() == local_big.read_bytes() and filename == "big.bin"
        artifact_dir = artifact.parent
        svc.finalize_download(job_d.id)
        cleaned = not artifact_dir.exists()
        v.add("S39-3 下载一致+暂存即清", dl_ok and cleaned, f"hash_ok={dl_ok} cleaned={cleaned}")

        # ---- S39-4 多选 zip ----
        job_z, zpath, zname = await svc.prepare_download(
            dev, [f"{SPIKE_DIR}/plain.txt", f"{SPIKE_DIR}/spike file 1.txt"]
        )
        with zipfile.ZipFile(zpath) as zf:
            names = sorted(zf.namelist())
            content_ok = zf.read("spike file 1.txt") == b"hi\n" and zf.read("plain.txt") == b""
        svc.finalize_download(job_z.id)
        v.add(
            "S39-4 多选 zip",
            names == ["plain.txt", "spike file 1.txt"] and content_ok
            and zname == "plain.txt_etc.zip",
            f"names={names} zname={zname}",
        )

        # ---- S39-5 重命名 ----
        await svc.rename(dev, f"{SPIKE_DIR}/plain.txt", "renamed.txt")
        after_rename = await svc.list_dir(dev, SPIKE_DIR)
        renamed_ok = any(e.name == "renamed.txt" for e in after_rename)
        conflict_ok = False
        try:
            await svc.rename(dev, f"{SPIKE_DIR}/renamed.txt", "spike file 1.txt")
        except TargetExistsError:
            conflict_ok = True
        v.add(
            "S39-5 重命名+冲突拒绝", renamed_ok and conflict_ok,
            f"renamed_ok={renamed_ok} conflict={conflict_ok}",
        )

        # ---- S39-6 删除三态（文件/目录/软链，链接目标目录完好） ----
        del_paths = [f"{SPIKE_DIR}/renamed.txt", f"{SPIKE_DIR}/sub"]
        if link_ok:
            del_paths.append(f"{SPIKE_DIR}/link")
        results = await svc.delete(dev, del_paths)
        all_ok = all(r["ok"] for r in results)
        try:
            await svc.list_dir(dev, f"{SPIKE_DIR}/sub")
            dir_gone = False
        except Exception:
            dir_gone = True
        link_parent_ok = any(
            e.name == "Download" for e in await svc.list_dir(dev, "/sdcard")
        )
        v.add(
            "S39-6 删除（文件/目录/软链）",
            all_ok and dir_gone and link_parent_ok,
            f"all_ok={all_ok} dir_gone={dir_gone} parent_intact={link_parent_ok}",
        )

        # ---- S39-7 白名单（写拒 / 读放行） ----
        wl_write_ok = False
        try:
            local_x = tmp_root / "x.bin"
            local_x.write_bytes(b"x")
            await svc.upload(dev, "/data/spike39", "x.bin", chunk_reader(local_x))
        except PermissionDeniedError:
            wl_write_ok = True
        proc_entries = await svc.list_dir(dev, "/proc")
        v.add(
            "S39-7 白名单写403/读放行",
            wl_write_ok and len(proc_entries) > 0,
            f"write_403={wl_write_ok} proc_readable={len(proc_entries) > 0}",
        )

        # ---- S39-8 注入面 ----
        evil_rejected = False
        try:
            await svc.rename(dev, f"{SPIKE_DIR}/big.bin", "../evil")
        except InvalidPathError:
            evil_rejected = True
        meta_name = "x;echo pwned#"
        local_m = tmp_root / "m.bin"
        local_m.write_bytes(b"safe")
        job_m = await svc.upload(dev, SPIKE_DIR, meta_name, chunk_reader(local_m))
        meta_entries = await svc.list_dir(dev, SPIKE_DIR)
        meta_ok = any(e.name == meta_name for e in meta_entries) and job_m.status == "done"
        pwned_exists = False
        try:
            await adb.shell(dev, f"ls -ld {_q('/sdcard/pwned')}")
            pwned_exists = True
        except Exception:
            pwned_exists = False
        inj_ok = evil_rejected and meta_ok and not pwned_exists
        v.add(
            "S39-8 注入面", inj_ok,
            f"dotdot_rejected={evil_rejected} meta_name_intact={meta_ok} not_execed={not pwned_exists}",
        )

        # ---- S39-9 非 UTF-8 名不崩 ----
        await adb.shell(dev, "touch /sdcard/Download/s39_spike/$(printf 'b\\xffad')")
        try:
            bad_entries = await svc.list_dir(dev, SPIKE_DIR)
            bad = [e.name for e in bad_entries if "ad" in e.name]
            v.add("S39-9 非 UTF-8 名不崩", len(bad_entries) > 0, f"bad_like={bad!r}")
        except Exception as exc:
            v.add("S39-9 非 UTF-8 名不崩", False, f"list_dir 抛: {exc}")

        await adb.shell(dev, f"rm -rf {_q(SPIKE_DIR)}")
    finally:
        # 释放互斥并清本地暂存
        shutil.rmtree(tmp_root, ignore_errors=True)

    out = Path(args.out)
    out.write_text(
        json.dumps({"device": dev, "verdicts": v.items}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"结果写入 {out}，FAIL={v.failed}", flush=True)
    return 1 if v.failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
