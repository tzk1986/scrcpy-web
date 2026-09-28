#!/usr/bin/env python3
"""
外置工具获取脚本（方案 15 §8.5 任务 0.5）
=========================================

从官方源下载并校验外置工具，使发布构建可复现（工具二进制均被
gitignore，无本脚本则换机/CI 无法构建）：

    - adb 三件套（adb.exe + AdbWinApi.dll + AdbWinUsbApi.dll）
      来源：Google Android SDK Platform-Tools（r36.0.0，与本机真机验证
      环境 adb 1.0.41 / 36.0.0 一致），从 zip 内 platform-tools/ 提取
    - scrcpy-server.jar
      来源：Genymobile/scrcpy GitHub Release v4.1（对齐
      app/scrcpy/constants.py 的 SCRCPY_SERVER_VERSION = "4.1"）

SHA256 锁定：清单内 sha256 非空时下载后强制比对，不匹配立即失败；
升级版本时先运行 ``python scripts/fetch_tools.py --print-hash`` 拿到
新 hash 并回填清单（Google 官方不发布 platform-tools 的 SHA256 表，
锁定值以本仓库实测为准）。

用法：
    python scripts/fetch_tools.py              # 下载并校验全部工具
    python scripts/fetch_tools.py --print-hash # 下载并打印实际 SHA256（锁定/升级用）
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = REPO_ROOT / "tools"
JAR_DIR = REPO_ROOT / "backend" / "app" / "scrcpy"
PLATFORM_TOOLS_URL = "https://dl.google.com/android/repository/platform-tools_r36.0.0-win.zip"
JAR_URL = "https://github.com/Genymobile/scrcpy/releases/download/v4.1/scrcpy-server-v4.1"


@dataclass(frozen=True)
class ZipEntry:
    """zip 内提取：下载 zip 后取指定成员落盘。"""

    name: str  # 落盘文件名
    url: str
    member: str  # zip 内成员路径
    sha256: str  # 锁定 hash（64 hex）；空串=未锁定（--print-hash 回填）
    target_dir: Path


@dataclass(frozen=True)
class FileEntry:
    """直接下载的文件。"""

    name: str
    url: str
    sha256: str
    target_dir: Path


TOOLS: list[ZipEntry | FileEntry] = [
    ZipEntry(
        "adb.exe",
        PLATFORM_TOOLS_URL,
        "platform-tools/adb.exe",
        "1e1c2280b90b3f01ad84cd8df4858b1b1995012814f3ca8893bcc3ba3848edec",
        TOOLS_DIR,
    ),
    ZipEntry(
        "AdbWinApi.dll",
        PLATFORM_TOOLS_URL,
        "platform-tools/AdbWinApi.dll",
        "9a56e72fe1372cb722a80c00b79dcecf2b37165884e470ed05f00c668c0043b0",
        TOOLS_DIR,
    ),
    ZipEntry(
        "AdbWinUsbApi.dll",
        PLATFORM_TOOLS_URL,
        "platform-tools/AdbWinUsbApi.dll",
        "5e77ccb2f25cd3a97553745adf1cd28a5fe8137cf64613b7fef9c6f92ff91f37",
        TOOLS_DIR,
    ),
    FileEntry(
        "scrcpy-server.jar",
        JAR_URL,
        "deacb991ed2509715160ffdc7907e47b4160eb30d1566217e9047fd5b8850cae",
        JAR_DIR,
    ),
]


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(url, dest)  # noqa: S310  # 固定官方 URL，非用户输入


def verify(path: Path, expected: str) -> None:
    actual = sha256_of(path)
    if expected and actual != expected:
        raise SystemExit(f"SHA256 mismatch for {path.name}: expected {expected}, got {actual}")


def ensure_all(print_hash: bool = False) -> None:
    archive_cache: dict[str, Path] = {}
    with tempfile.TemporaryDirectory(prefix="openscrcpy-tools-") as tmp:
        tmp_dir = Path(tmp)
        for entry in TOOLS:
            if isinstance(entry, ZipEntry):
                if entry.url not in archive_cache:
                    archive = tmp_dir / f"zip-{len(archive_cache)}.zip"
                    download(entry.url, archive)
                    archive_cache[entry.url] = archive
                with zipfile.ZipFile(archive_cache[entry.url]) as zf:
                    payload = zf.read(entry.member)
                target = entry.target_dir / entry.name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
            else:
                target = entry.target_dir / entry.name
                download(entry.url, target)
            actual = sha256_of(target)
            print(f"{entry.name}: sha256={actual}")
            if print_hash:
                continue
            verify(target, entry.sha256)
    print("OK: all tools fetched")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="下载并校验外置工具（方案 15 任务 0.5）")
    parser.add_argument(
        "--print-hash",
        action="store_true",
        help="下载后打印实际 SHA256 而不校验（首次锁定/升级版本用，回填进 TOOLS 清单）",
    )
    ensure_all(print_hash=parser.parse_args(argv).print_hash)
    return 0


if __name__ == "__main__":
    sys.exit(main())