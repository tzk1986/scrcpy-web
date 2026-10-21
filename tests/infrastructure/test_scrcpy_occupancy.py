"""
方案 48：起流前设备残留 scrcpy 进程占用检测（_detect_stray_server）单测
=====================================================================

不触真机：monkeypatch `scrcpy.asyncio.create_subprocess_exec` 为假进程队列，
覆盖——命中 + 参数指纹归因（D3 本软件残留 / 其他工具）、无残留放行、
检测工具缺失降级放行（rc>1 / spawn 异常）、命令模板含防自匹配正则。
"""

import time
from collections import deque
from typing import Any

import pytest

from app.core.exceptions import ScrcpyOccupiedError
from app.infrastructure.stream import scrcpy as scrcpy_module
from app.infrastructure.stream.scrcpy import ScrcpyEncoder

DEV = "192.168.8.25:5555"

# 本软件启动参数指纹（与 max_size=0/tunnel_forward 等启动串一致）
OWN_CMDLINE = (
    "app_process\x00/\x00com.genymobile.scrcpy.Server\x005.0.1\x00"
    "log_level=info\x00max_size=0\x00max_fps=30\x00video_bit_rate=4000000\x00"
    "video_codec=h264\x00tunnel_forward=true\x00control=true\x00audio=false\x00"
    "show_touches=false\x00stay_awake=false\x00power_off_on_close=false\x00"
    "clipboard_autosync=false\x00"
)
# 第三方 scrcpy 桌面版典型命令行（无 max_size=0 指纹）
OTHER_CMDLINE = (
    "app_process\x00/\x00com.genymobile.scrcpy.Server\x002.4\x00"
    "log_level=verbose\x00max_size=1080\x00audio=true\x00"
)


class FakeProc:
    def __init__(self, returncode: int, out: bytes = b"", err: bytes = b"") -> None:
        self.returncode = returncode
        self._out = out
        self._err = err

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._out, self._err


class ProcQueue(deque):
    """假进程队列 + 调用参数记录（deque 子类才有 __dict__ 挂 calls）。"""

    calls: list


@pytest.fixture()
def fake_adb(monkeypatch: pytest.MonkeyPatch) -> ProcQueue:
    """替换 create_subprocess_exec：按队列顺序返回假进程，并记录调用参数。"""
    procs = ProcQueue()
    procs.calls = []

    async def _exec(*args: Any, **kwargs: Any) -> Any:
        procs.calls.append(tuple(str(a) for a in args))
        item = procs.popleft() if procs else FakeProc(1, b"")
        if isinstance(item, BaseException):
            raise item
        return item

    monkeypatch.setattr(scrcpy_module.asyncio, "create_subprocess_exec", _exec)
    return procs


def _queue_hit(procs: ProcQueue, cmdline: bytes) -> None:
    procs.append(FakeProc(0, b"/proc/2258/cmdline\n"))
    procs.append(FakeProc(0, cmdline))


@pytest.mark.asyncio
async def test_stray_own_fingerprint_raises_with_own_hint(fake_adb) -> None:
    """命中且参数指纹与本软件一致 → D3 归因「上次会话残留」文案。"""
    _queue_hit(fake_adb, OWN_CMDLINE.encode())
    encoder = ScrcpyEncoder()
    with pytest.raises(ScrcpyOccupiedError) as exc:
        await encoder._detect_stray_server(DEV)
    assert exc.value.code == "SCRCPY_SESSION_OCCUPIED"
    assert DEV in exc.value.message
    assert "残留" in exc.value.message
    # 检测命令须为设备侧 grep（[.] 防自匹配正则）
    grep_call = fake_adb.calls[0]  # type: ignore[attr-defined]
    assert any("scrcpy[.]Server" in part for part in grep_call)
    assert "/proc/[0-9]*/cmdline" in " ".join(grep_call)


@pytest.mark.asyncio
async def test_stray_other_fingerprint_raises_with_other_hint(fake_adb) -> None:
    """命中但指纹不符（如第三方 scrcpy）→ 「其他工具占用」文案。"""
    _queue_hit(fake_adb, OTHER_CMDLINE.encode())
    encoder = ScrcpyEncoder()
    with pytest.raises(ScrcpyOccupiedError) as exc:
        await encoder._detect_stray_server(DEV)
    assert "其他工具" in exc.value.message


@pytest.mark.asyncio
async def test_stray_cmdline_unreadable_falls_back_to_other_hint(fake_adb) -> None:
    """命中但读 cmdline 失败（cat 报错）→ 归因降级用通用「其他工具或残留」文案，仍阻断。"""
    fake_adb.append(FakeProc(0, b"/proc/2258/cmdline\n"))
    fake_adb.append(FakeProc(1, b""))
    encoder = ScrcpyEncoder()
    with pytest.raises(ScrcpyOccupiedError):
        await encoder._detect_stray_server(DEV)


@pytest.mark.asyncio
async def test_no_stray_passes_through(fake_adb) -> None:
    """grep rc=1 无匹配 → 正常放行（不抛、无第二次 cat 调用）。"""
    fake_adb.append(FakeProc(1, b""))
    encoder = ScrcpyEncoder()
    await encoder._detect_stray_server(DEV)
    assert len(fake_adb.calls) == 1  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_detect_tool_missing_degrades_to_pass(fake_adb) -> None:
    """grep 不可用（rc>1，如老 ROM）→ 降级放行不误伤正常起流。"""
    fake_adb.append(FakeProc(2, b"", b"grep: not found\n"))
    encoder = ScrcpyEncoder()
    await encoder._detect_stray_server(DEV)


@pytest.mark.asyncio
async def test_spawn_error_degrades_to_pass(fake_adb) -> None:
    """adb 子进程 spawn 异常 → 降级放行（检测不可用不阻断主流程）。"""
    fake_adb.append(OSError("adb not found"))
    encoder = ScrcpyEncoder()
    await encoder._detect_stray_server(DEV)


# ---------------------------------------------------------------------------
# 方案 48 D2：本会话 PID 枚举 + stop() 设备侧终止（治理残留根因）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_enumerate_remote_pids_parses_all(fake_adb) -> None:
    fake_adb.append(FakeProc(0, b"/proc/2256/cmdline\n/proc/2258/cmdline\n"))
    encoder = ScrcpyEncoder()
    assert await encoder._enumerate_remote_pids(DEV) == ["2256", "2258"]


@pytest.mark.asyncio
async def test_enumerate_remote_pids_empty_or_error_returns_empty(fake_adb) -> None:
    fake_adb.append(FakeProc(1, b""))  # 无匹配
    encoder = ScrcpyEncoder()
    assert await encoder._enumerate_remote_pids(DEV) == []
    fake_adb.append(OSError("adb gone"))
    assert await encoder._enumerate_remote_pids(DEV) == []


@pytest.mark.asyncio
async def test_stop_kills_recorded_remote_pids(fake_adb) -> None:
    """stop() 先发设备侧 kill -9（全部本会话 PID），并清空记录。"""
    encoder = ScrcpyEncoder()
    encoder._device_id = DEV
    encoder._remote_pids = ["2256", "2258"]
    await encoder.stop()
    kill_cmd = " ".join(fake_adb.calls[0])  # type: ignore[attr-defined]
    assert "kill -9 2256 2258" in kill_cmd
    assert encoder._remote_pids == []


@pytest.mark.asyncio
async def test_stop_without_pids_skips_kill(fake_adb) -> None:
    """无记录 PID（起流早期失败/枚举降级）→ stop 不发 kill 命令，行为同旧。"""
    encoder = ScrcpyEncoder()
    encoder._device_id = DEV
    await encoder.stop()
    joined = [" ".join(c) for c in fake_adb.calls]  # type: ignore[attr-defined]
    assert not any("kill -9" in cmd for cmd in joined)


# ---------------------------------------------------------------------------
# 方案 51 B2：收尾中 pids 注册表——重连收尾窗误检「设备残留占用」防御性跳过
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clear_finalizing_registry():
    """隔离全局注册表，避免用例间互污（stop 用例会写入）。"""
    scrcpy_module._FINALIZING_PIDS.clear()
    yield
    scrcpy_module._FINALIZING_PIDS.clear()


@pytest.mark.asyncio
async def test_stop_registers_finalizing_pids(fake_adb) -> None:
    """stop() 设备侧 kill 前把本会话 PID 登记为「收尾中」（带过期时刻）。"""
    encoder = ScrcpyEncoder()
    encoder._device_id = DEV
    encoder._remote_pids = ["2256", "2258"]
    await encoder.stop()
    entry = scrcpy_module._FINALIZING_PIDS[DEV]
    assert set(entry) == {"2256", "2258"}
    assert all(v > time.monotonic() for v in entry.values())


@pytest.mark.asyncio
async def test_stray_all_pids_finalizing_skips(fake_adb) -> None:
    """命中 PID 全属收尾会话 → 跳过检测放行（不抛、不再读 cmdline）。"""
    scrcpy_module._FINALIZING_PIDS[DEV] = {"2258": time.monotonic() + 10}
    fake_adb.append(FakeProc(0, b"/proc/2258/cmdline\n"))
    encoder = ScrcpyEncoder()
    await encoder._detect_stray_server(DEV)  # 不抛即通过
    assert len(fake_adb.calls) == 1  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_stray_partial_finalizing_still_raises(fake_adb) -> None:
    """命中含注册表外 PID（真实他占并存）→ 仍按原逻辑阻断。"""
    scrcpy_module._FINALIZING_PIDS[DEV] = {"2258": time.monotonic() + 10}
    fake_adb.append(FakeProc(0, b"/proc/2258/cmdline\n/proc/2259/cmdline\n"))
    fake_adb.append(FakeProc(0, OWN_CMDLINE.encode()))
    encoder = ScrcpyEncoder()
    with pytest.raises(ScrcpyOccupiedError):
        await encoder._detect_stray_server(DEV)


@pytest.mark.asyncio
async def test_stray_finalizing_entry_expired_still_raises(fake_adb) -> None:
    """注册表条目已过期（长残留不再是本会话收尾）→ 正常阻断，并惰性清理。"""
    scrcpy_module._FINALIZING_PIDS[DEV] = {"2258": time.monotonic() - 1}
    _queue_hit(fake_adb, OWN_CMDLINE.encode())
    encoder = ScrcpyEncoder()
    with pytest.raises(ScrcpyOccupiedError):
        await encoder._detect_stray_server(DEV)
    assert DEV not in scrcpy_module._FINALIZING_PIDS
