"""ConPTY（pywinpty）进程适配器测试 — 仅 Windows 有真实实现。"""
import asyncio
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="ConPTY 仅 Windows")

from app.infrastructure.adb.winpty import ConPtyProcess, spawn_conpty  # noqa: E402


async def collect_until_eof(proc: ConPtyProcess, timeout: float = 10.0) -> bytes:
    """持续读 stdout 直到 EOF 或超时。"""
    loop = asyncio.get_event_loop()
    out = b""
    end = loop.time() + timeout
    while loop.time() < end:
        chunk = await asyncio.wait_for(proc.stdout.read(4096), timeout=5.0)
        if not chunk:
            return out
        out += chunk
    return out


@pytest.mark.asyncio
async def test_conpty_spawn_read_exitcode():
    proc = await spawn_conpty(["cmd.exe", "/Q", "/C", "echo CONPTY_HELLO"])
    assert isinstance(proc, ConPtyProcess)
    assert proc.pid > 0
    assert proc.returncode is None
    out = await collect_until_eof(proc)
    assert b"CONPTY_HELLO" in out
    code = await asyncio.wait_for(proc.wait(), timeout=5.0)
    assert code == 0
    assert proc.returncode == 0


@pytest.mark.asyncio
async def test_conpty_write_input_and_kill():
    proc = await spawn_conpty(["cmd.exe", "/Q", "/K"])
    try:
        assert proc.returncode is None
        proc.stdin.write(b"echo WRITTEN_OK\r")
        await proc.stdin.drain()
        out = b""
        loop = asyncio.get_event_loop()
        end = loop.time() + 8
        while b"WRITTEN_OK" not in out and loop.time() < end:
            out += await asyncio.wait_for(proc.stdout.read(4096), timeout=5.0)
        assert b"WRITTEN_OK" in out
    finally:
        proc.kill()
        await asyncio.wait_for(proc.wait(), timeout=5.0)
        assert proc.returncode is not None


@pytest.mark.asyncio
async def test_stdin_close_does_not_raise():
    proc = await spawn_conpty(["cmd.exe", "/Q", "/C", "echo bye"])
    proc.stdin.close()  # ConPTY 无 EOF 语义，应为安全 no-op 近似
    await collect_until_eof(proc)
    await asyncio.wait_for(proc.wait(), timeout=5.0)
