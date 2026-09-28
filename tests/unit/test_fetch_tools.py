"""
外置工具获取脚本测试（方案 15 任务 0.5）
======================================

不触网：download 一律 monkeypatch 成本地假文件；真实 SHA256 锁定值由
fetch_tools 清单自带（构建可复现），测试只验证机制与清单完整性。
"""

import dataclasses
import hashlib
import sys
import zipfile
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import fetch_tools  # noqa: E402


def _replace_entry(entry, *, sha256=None, target_dir=None):
    kwargs = {}
    if sha256 is not None:
        kwargs["sha256"] = sha256
    if target_dir is not None:
        kwargs["target_dir"] = target_dir
    return dataclasses.replace(entry, **kwargs)


def test_manifest_covers_tools_trio_and_jar():
    names = {e.name for e in fetch_tools.TOOLS}
    assert names == {"adb.exe", "AdbWinApi.dll", "AdbWinUsbApi.dll", "scrcpy-server.jar"}


def test_manifest_hashes_are_locked():
    # 发布构建可复现的前提：四条目 SHA256 全部锁定（64 位十六进制）
    for e in fetch_tools.TOOLS:
        assert len(e.sha256) == 64
        assert all(c in "0123456789abcdef" for c in e.sha256)


def test_jar_url_aligns_server_version():
    # 必须与 app/scrcpy/constants.py 的 SCRCPY_SERVER_VERSION = "4.1" 对齐
    jar = next(e for e in fetch_tools.TOOLS if e.name == "scrcpy-server.jar")
    assert jar.url.endswith("/v4.1/scrcpy-server-v4.1")


def test_zip_entries_point_into_platform_tools_archive():
    for e in fetch_tools.TOOLS:
        if isinstance(e, fetch_tools.ZipEntry):
            assert e.member.startswith("platform-tools/")


def test_sha256_of(tmp_path):
    p = tmp_path / "f.bin"
    p.write_bytes(b"abc")
    assert fetch_tools.sha256_of(p) == hashlib.sha256(b"abc").hexdigest()


def test_verify_mismatch_raises(tmp_path):
    p = tmp_path / "f.bin"
    p.write_bytes(b"abc")
    with pytest.raises(SystemExit):
        fetch_tools.verify(p, "0" * 64)


def _fake_download(mapping):
    def download(url: str, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(mapping[url].read_bytes())
    return download


def test_ensure_all_extracts_zip_members_and_jar(tmp_path, monkeypatch):
    fake_zip = tmp_path / "platform-tools.zip"
    with zipfile.ZipFile(fake_zip, "w") as zf:
        zf.writestr("platform-tools/adb.exe", b"adb-binary")
        zf.writestr("platform-tools/AdbWinApi.dll", b"api-dll")
        zf.writestr("platform-tools/AdbWinUsbApi.dll", b"usb-dll")
    fake_jar = tmp_path / "scrcpy-server"
    fake_jar.write_bytes(b"jar-binary")

    tools_out = tmp_path / "tools_out"
    jar_out = tmp_path / "jar_out"
    locked = [
        _replace_entry(e, sha256=hashlib.sha256(payload).hexdigest(), target_dir=dir_)
        for e, payload, dir_ in [
            (fetch_tools.TOOLS[0], b"adb-binary", tools_out),
            (fetch_tools.TOOLS[1], b"api-dll", tools_out),
            (fetch_tools.TOOLS[2], b"usb-dll", tools_out),
            (fetch_tools.TOOLS[3], b"jar-binary", jar_out),
        ]
    ]
    monkeypatch.setattr(fetch_tools, "TOOLS", locked)
    monkeypatch.setattr(
        fetch_tools,
        "download",
        _fake_download({fetch_tools.PLATFORM_TOOLS_URL: fake_zip, fetch_tools.JAR_URL: fake_jar}),
    )

    fetch_tools.ensure_all(print_hash=False)

    assert (tools_out / "adb.exe").read_bytes() == b"adb-binary"
    assert (tools_out / "AdbWinApi.dll").read_bytes() == b"api-dll"
    assert (tools_out / "AdbWinUsbApi.dll").read_bytes() == b"usb-dll"
    assert (jar_out / "scrcpy-server.jar").read_bytes() == b"jar-binary"


def test_ensure_all_hash_mismatch_fails(tmp_path, monkeypatch):
    # ensure_all 先处理 platform-tools zip 条目，假下载映射必须同时覆盖两个 URL
    fake_zip = tmp_path / "platform-tools.zip"
    with zipfile.ZipFile(fake_zip, "w") as zf:
        zf.writestr("platform-tools/adb.exe", b"adb-binary")
        zf.writestr("platform-tools/AdbWinApi.dll", b"api-dll")
        zf.writestr("platform-tools/AdbWinUsbApi.dll", b"usb-dll")
    fake_jar = tmp_path / "scrcpy-server"
    fake_jar.write_bytes(b"jar-binary")
    locked = [_replace_entry(e, sha256="0" * 64, target_dir=tmp_path / "out") for e in fetch_tools.TOOLS]
    monkeypatch.setattr(fetch_tools, "TOOLS", locked)
    monkeypatch.setattr(
        fetch_tools,
        "download",
        _fake_download({fetch_tools.PLATFORM_TOOLS_URL: fake_zip, fetch_tools.JAR_URL: fake_jar}),
    )
    with pytest.raises(SystemExit):
        fetch_tools.ensure_all(print_hash=False)