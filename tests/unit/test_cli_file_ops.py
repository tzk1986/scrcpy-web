"""cli 文件原语单测（方案 39 T1）：ls 解析 / 尾斜杠 / quote / timeout 透传。

注：argv 期望值以 shlex.quote 生成（与实现同源）——shlex.quote 对纯安全字符
路径（如 /sdcard）不加引号，计划原文的字面量 "'/sdcard'" 与 shlex 实际行为
不符，此处以 quote() 计算精确期望，空格名仍断言真实引号包裹（Review Focus #1）。
"""

import shlex

import pytest

from app.domain.files import FileEntry
from app.infrastructure.adb.cli import AdbCliDriver, _parse_ls_line


class _Recorder:
    """替身 _run_serial：记录调用并返回预置输出。"""

    def __init__(self, output: str = "") -> None:
        self.output = output
        self.calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    async def __call__(self, device_id: str, *args: str, **kwargs: object) -> str:
        self.calls.append(((device_id, *args), kwargs))
        return self.output


@pytest.fixture()
def driver() -> AdbCliDriver:
    return AdbCliDriver()


def test_parse_ls_normal_file() -> None:
    e = _parse_ls_line(
        "-rw-r--r-- 1 root root 12345 2026-10-12 09:12 app.txt"
    )
    assert e == FileEntry(
        name="app.txt", is_dir=False, is_symlink=False, size=12345,
        mtime="2026-10-12 09:12", permissions="-rw-r--r--",
    )


def test_parse_ls_dir_and_total_line() -> None:
    assert _parse_ls_line("total 34") is None
    assert _parse_ls_line("") is None
    e = _parse_ls_line(
        "drwxr-x--- 2 shell ext_rw_shared 3452 2026-10-12 09:12 Download"
    )
    assert e is not None and e.is_dir and e.name == "Download"


def test_parse_ls_name_with_spaces() -> None:
    e = _parse_ls_line("-rw-r--r-- 1 u g 1 2026-10-12 09:12 my report final.txt")
    assert e is not None and e.name == "my report final.txt"


def test_parse_ls_symlink() -> None:
    e = _parse_ls_line("lrwxrwxrwx 1 root root 21 2026-10-12 09:12 link -> /storage/emulated/0")
    assert e is not None and e.is_symlink and e.name == "link"
    assert e.link_target == "/storage/emulated/0"


def test_parse_ls_seconds_variant() -> None:
    e = _parse_ls_line("-rw-r--r-- 1 u g 1 2026-10-12 09:12:33 t.bin")
    assert e is not None and e.mtime == "2026-10-12 09:12:33"


async def test_list_dir_quotes_and_appends_trailing_slash(driver: AdbCliDriver) -> None:
    rec = _Recorder("total 0\n-rw-r--r-- 1 u g 1 2026-10-12 09:12 a.txt")
    driver._run_serial = rec  # type: ignore[method-assign]
    out = await driver.list_dir("dev:5555", "/sdcard/Download")
    (args, kwargs), = rec.calls
    assert args == ("dev:5555", "shell", f"ls -la {shlex.quote('/sdcard/Download/')}")
    assert kwargs.get("decode_errors") == "replace"
    assert out[0].name == "a.txt"


async def test_list_dir_path_with_space_is_quoted(driver: AdbCliDriver) -> None:
    rec = _Recorder("total 0")
    driver._run_serial = rec  # type: ignore[method-assign]
    await driver.list_dir("d", "/sdcard/my dir")
    (args, _), = rec.calls
    assert args[2] == "ls -la '/sdcard/my dir/'"


async def test_stat_size_uses_ld(driver: AdbCliDriver) -> None:
    rec = _Recorder("drwxr-x--- 2 shell ext_rw_shared 3452 2026-10-12 09:12 /sdcard")
    driver._run_serial = rec  # type: ignore[method-assign]
    e = await driver.stat_size("d", "/sdcard")
    (args, _), = rec.calls
    assert args[2] == f"ls -ld {shlex.quote('/sdcard')}"
    assert e.size == 3452


async def test_stat_size_unparsable_raises(driver: AdbCliDriver) -> None:
    from app.core.exceptions import AdbError

    rec = _Recorder("")
    driver._run_serial = rec  # type: ignore[method-assign]
    with pytest.raises(AdbError):
        await driver.stat_size("d", "/sdcard/x")


async def test_rename_and_delete_argv(driver: AdbCliDriver) -> None:
    rec = _Recorder()
    driver._run_serial = rec  # type: ignore[method-assign]
    await driver.rename_path("d", "/sdcard/a", "/sdcard/b b")
    await driver.delete_path("d", "/sdcard/x", is_dir=True)
    await driver.delete_path("d", "/sdcard/y", is_dir=False)
    assert rec.calls[0][0][2] == f"mv {shlex.quote('/sdcard/a')} {shlex.quote('/sdcard/b b')}"
    assert rec.calls[1][0][2] == f"rm -r {shlex.quote('/sdcard/x')}"
    assert rec.calls[2][0][2] == f"rm -f {shlex.quote('/sdcard/y')}"


async def test_push_pull_pass_timeout(driver: AdbCliDriver) -> None:
    rec = _Recorder()
    driver._run_serial = rec  # type: ignore[method-assign]
    await driver.push("d", "L", "R", timeout=900)
    await driver.pull("d", "R", "L", timeout=900)
    assert rec.calls[0][1] == {"timeout": 900}
    assert rec.calls[1][1] == {"timeout": 900}


async def test_run_serial_decode_errors_passthrough(driver: AdbCliDriver) -> None:
    captured: dict[str, object] = {}
    orig = AdbCliDriver._run

    async def spy(self: AdbCliDriver, *args: str, **kwargs: object) -> str:
        captured.update(args=args, kwargs=kwargs)
        return "ok"

    AdbCliDriver._run = spy  # type: ignore[method-assign]
    try:
        out = await driver._run_serial("d", "shell", "ls", decode_errors="replace")
    finally:
        AdbCliDriver._run = orig  # type: ignore[method-assign]
    assert out == "ok"
    assert captured["kwargs"] == {"decode_errors": "replace"}


async def test_run_decode_errors_replaces_invalid_utf8(driver: AdbCliDriver) -> None:
    import asyncio

    stdout = b"file_\xff_name\n"
    stderr = b""

    class _Proc:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return stdout, stderr

        def kill(self) -> None: ...

    async def fake_exec(*a: object, **k: object) -> _Proc:
        return _Proc()

    async def fake_wait(coro: object, timeout: object = None) -> tuple[bytes, bytes]:
        return await coro  # type: ignore[misc]

    orig_exec = asyncio.create_subprocess_exec
    orig_wait = asyncio.wait_for
    asyncio.create_subprocess_exec = fake_exec  # type: ignore[assignment]
    asyncio.wait_for = fake_wait  # type: ignore[assignment]
    try:
        out = await driver._run("shell", "ls", decode_errors="replace")
    finally:
        asyncio.create_subprocess_exec = orig_exec  # type: ignore[assignment]
        asyncio.wait_for = orig_wait  # type: ignore[assignment]
    assert "file_" in out and "�" in out  # 坏字节解码为 U+FFFD 替换符
