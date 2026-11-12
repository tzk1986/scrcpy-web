"""方案 58 A-05：兜底 adb input 并发闸（1 在飞）+ 溢出丢弃 + 失败计数降级。

覆盖：
    - 串行闸：第二条输入的 spawn 必须等第一条 adb 进程退出后进行（保序，
      杜绝输入风暴下进程堆积与乱序执行）
    - 等待者达上限 → 新输入直接丢弃（不 spawn）、计数并告警
    - 连续失败达阈 → 一次 degraded 告警；成功即复位计数
    - 进程 wait 超时 → kill + 告警（防 adb 进程挂死占死闸门）
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from structlog.testing import capture_logs

from app.infrastructure.stream import scrcpy as scrcpy_module
from app.infrastructure.stream.scrcpy import ScrcpyEncoder

DEVICE_ID = "emulator-5554"


def make_encoder() -> ScrcpyEncoder:
    enc = ScrcpyEncoder()
    enc._device_id = DEVICE_ID
    enc._control_sender = None  # 控制通道不可用 → adb 兜底
    return enc


class HangingProc:
    """wait 阻塞在 release 事件上的假进程；记录 kill。"""

    def __init__(self, tag: str, release: asyncio.Event, order: list[str]) -> None:
        self.tag = tag
        self.release = release
        self.order = order

    async def wait(self) -> int:
        self.order.append(f"wait:{self.tag}")
        await self.release.wait()
        return 0

    def kill(self) -> None:
        self.order.append(f"kill:{self.tag}")


class OkProc:
    """立即成功退出的假进程。"""

    async def wait(self) -> int:
        return 0

    def kill(self) -> None:
        pass


async def test_fallback_unsupported_action_returns_degraded_signal():
    """方案 58 B-23 裁决 a：scroll 等兜底不支持的动作被丢弃时，
    send_input 返回 False 供 WS 层推 input_degraded 提示帧；不 spawn 进程。"""
    encoder = make_encoder()

    with patch("asyncio.create_subprocess_exec") as exec_mock:
        result = await encoder.send_input(
            {"action": "scroll", "x": 1, "y": 2, "v_scroll": -1}
        )

    assert result is False
    exec_mock.assert_not_called()


async def test_fallback_supported_action_returns_true():
    """兜底支持的动作（touch）派发成功 → send_input 返回 True（无提示帧）。"""
    encoder = make_encoder()

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=OkProc())):
        result = await encoder.send_input({"action": "touch", "x": 1, "y": 1})

    assert result is True


async def test_fallback_key_up_skip_returns_true():
    """down=False 抬起在兜底态有意跳过（非降级丢弃）→ 返回 True 不提示。"""
    encoder = make_encoder()

    with patch("asyncio.create_subprocess_exec") as exec_mock:
        result = await encoder.send_input({"action": "key", "keycode": 4, "down": False})

    assert result is True
    exec_mock.assert_not_called()


async def test_fallback_gate_serializes_spawns():
    """第一条兜底输入进程未退出前，第二条不得 spawn（1 在飞 + 保序）。"""
    encoder = make_encoder()
    order: list[str] = []
    release1 = asyncio.Event()
    procs = [HangingProc("1", release1, order), HangingProc("2", release1, order)]

    async def fake_exec(*args, **kwargs):
        order.append(f"spawn:{args[-1]}")
        return procs.pop(0)

    with patch("asyncio.create_subprocess_exec", new=fake_exec):
        t1 = asyncio.create_task(encoder.send_input({"action": "touch", "x": 1, "y": 1}))
        t2 = asyncio.create_task(encoder.send_input({"action": "key", "keycode": 4}))
        await asyncio.sleep(0.05)
        release1.set()
        await asyncio.gather(t1, t2)

    assert order == ["spawn:1", "wait:1", "spawn:4", "wait:2"]


async def test_fallback_overflow_drops_without_spawn(monkeypatch):
    """等待者达上限：新输入直接丢弃不 spawn，dropped 计数并告警。"""
    encoder = make_encoder()
    monkeypatch.setattr(scrcpy_module, "_FALLBACK_MAX_WAITERS", 1)
    encoder._fallback_waiters = 1  # 已满（闸上已有排队者）

    with patch("asyncio.create_subprocess_exec") as exec_mock, capture_logs() as logs:
        await encoder.send_input({"action": "touch", "x": 1, "y": 2})

    exec_mock.assert_not_called()
    assert encoder._fallback_dropped == 1
    drops = [e for e in logs if e["event"] == "fallback_adb_input_dropped"]
    assert drops and drops[0]["dropped"] == 1


async def test_fallback_consecutive_failures_degrade_warning():
    """连续失败达阈（5）发一次 degraded 告警；成功一次即复位计数。"""
    encoder = make_encoder()

    for i in range(4):
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(side_effect=OSError("no adb"))):
            await encoder.send_input({"action": "touch", "x": 1, "y": 1})
        assert encoder._fallback_failures == i + 1

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(side_effect=OSError("no adb"))), \
            capture_logs() as logs:
        await encoder.send_input({"action": "touch", "x": 1, "y": 1})
    assert encoder._fallback_failures == 5
    assert any(e["event"] == "fallback_adb_input_degraded" for e in logs)

    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=OkProc())):
        await encoder.send_input({"action": "touch", "x": 1, "y": 1})
    assert encoder._fallback_failures == 0


async def test_fallback_wait_timeout_kills_process(monkeypatch):
    """进程 wait 超时 → kill 之并告警，闸门及时释放。"""
    encoder = make_encoder()
    monkeypatch.setattr(scrcpy_module, "_FALLBACK_WAIT_TIMEOUT", 0.05)
    order: list[str] = []
    never = asyncio.Event()  # 永不置位 → wait 永挂
    proc = HangingProc("slow", never, order)

    async def fake_exec(*args, **kwargs):
        return proc

    with patch("asyncio.create_subprocess_exec", new=fake_exec), capture_logs() as logs:
        await encoder.send_input({"action": "touch", "x": 1, "y": 1})

    assert "kill:slow" in order
    assert any(e["event"] == "fallback_adb_input_timeout" for e in logs)
    assert encoder._fallback_waiters == 0
