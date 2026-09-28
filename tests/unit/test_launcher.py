"""
绿色版启动器辅助函数测试（方案 15 任务 3）
==========================================

覆盖 §8.4-2/3/6、§9.3-5：DLL 搜索路径修复、单实例互斥、端口探测
回退、文件日志重定向、就绪轮询、frozen 绑定 127.0.0.1。
"""

import socket
import sys
import uuid

import pytest

from app.core import launcher


def test_resolve_bind_host_frozen_defaults_loopback():
    assert launcher.resolve_bind_host("0.0.0.0", frozen=True, explicit_env=None) == "127.0.0.1"


def test_resolve_bind_host_explicit_env_wins():
    assert launcher.resolve_bind_host("0.0.0.0", frozen=True, explicit_env="0.0.0.0") == "0.0.0.0"


def test_resolve_bind_host_source_run_keeps_configured():
    assert launcher.resolve_bind_host("0.0.0.0", frozen=False, explicit_env=None) == "0.0.0.0"


def test_find_available_port_skips_occupied():
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", 0))
        probe.listen(1)
        occupied = probe.getsockname()[1]
        assert launcher.find_available_port("127.0.0.1", occupied, attempts=2) == occupied + 1
    finally:
        probe.close()


def test_find_available_port_exhausted_raises():
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", 0))
        probe.listen(1)
        occupied = probe.getsockname()[1]
        with pytest.raises(RuntimeError):
            launcher.find_available_port("127.0.0.1", occupied, attempts=1)
    finally:
        probe.close()


def test_port_file_roundtrip(tmp_path):
    launcher.write_runtime_port(tmp_path / "port.txt", 8766)
    assert launcher.read_runtime_port(tmp_path / "port.txt") == 8766


def test_read_runtime_port_missing_or_garbage_returns_none(tmp_path):
    assert launcher.read_runtime_port(tmp_path / "missing.txt") is None
    garbage = tmp_path / "garbage.txt"
    garbage.write_text("not-a-port", encoding="utf-8")
    assert launcher.read_runtime_port(garbage) is None


def test_wait_until_port_ready_true():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        assert launcher.wait_until_port_ready("127.0.0.1", port, timeout=2.0)
    finally:
        server.close()


def test_wait_until_port_ready_timeout():
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()  # 释放后无人监听（小概率被抢注，取 64xxx-65xxx 之外的高端口）
    assert launcher.wait_until_port_ready("127.0.0.1", port, timeout=0.3) is False


def test_redirect_std_streams_writes_to_file(tmp_path):
    old_out, old_err = sys.stdout, sys.stderr
    try:
        launcher.redirect_std_streams(tmp_path / "logs" / "openscrcpy.log")
        print("hello-from-launcher")
        assert sys.stdout is not None and sys.stderr is not None
        sys.stdout.flush()
        content = (tmp_path / "logs" / "openscrcpy.log").read_text(encoding="utf-8")
        assert "hello-from-launcher" in content
    finally:
        sys.stdout, sys.stderr = old_out, old_err


@pytest.mark.skipif(sys.platform != "win32", reason="CreateMutex 单实例锁仅 Windows 实现")
def test_single_instance_mutex_second_acquire_false():
    name = f"openscrcpy-test-mutex-{uuid.uuid4().hex}"
    assert launcher.acquire_single_instance_mutex(name) is True
    assert launcher.acquire_single_instance_mutex(name) is False


def test_fix_dll_search_path_noop_on_non_frozen():
    launcher.fix_dll_search_path()  # 不抛异常即可（pty 环境无 win32 断言）