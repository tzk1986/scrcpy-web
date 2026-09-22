"""WS 调试端点（interfaces/ws/debug.py）退出路径测试。

覆盖二次审核发现的缺陷（2026-09-22）：

    - exec 分支错误回发失败（客户端已断开时 send 抛错）曾产生二次异常
      逃逸，绕过订阅/转发清理，实测残留订阅者与转发任务；
    - 清理已收进 finally：任何退出路径（命令异常、非法消息、
      客户端断开）都必须执行 unsubscribe + stop_output_forwarding。

全部使用手写 Fake（不依赖真实设备 / Starlette 运行时）。
"""

import pytest
from fastapi import WebSocketDisconnect

from app.interfaces.ws.debug import debug_stream


class FakeDebugService:
    """WS 处理器所需方法的最小替身，记录清理调用次数。"""

    def __init__(self, lines=("a", "b"), exec_error=None):
        self._lines = list(lines)
        self._exec_error = exec_error
        self.unsubscribed = 0
        self.forwarding_stopped = 0
        self.generators_closed = 0

    async def _exec_shell_stream_raw(self, session_id, cmd):
        try:
            if self._exec_error is not None:
                raise self._exec_error
            for line in self._lines:
                yield line
        finally:
            self.generators_closed += 1

    async def unsubscribe(self, session_id, websocket):
        self.unsubscribed += 1

    async def stop_output_forwarding(self, session_id):
        self.forwarding_stopped += 1


class FakeWebSocket:
    """最小 WebSocket 替身：脚本化 receive_json，记录 send_json。

    fail_send_after：send_json 第 N 次调用之后开始抛 send_exc
    （模拟 Starlette 在客户端断开后的发送行为）。
    """

    def __init__(self, messages, fail_send_after=None, send_exc=None):
        self._messages = list(messages)
        self._fail_send_after = fail_send_after
        self._send_exc = send_exc or WebSocketDisconnect(code=1006)
        self._send_count = 0
        self.sent = []

    async def accept(self):
        pass

    async def receive_json(self):
        if not self._messages:
            raise WebSocketDisconnect(code=1000)
        return self._messages.pop(0)

    async def send_json(self, payload):
        self._send_count += 1
        if self._fail_send_after is not None and self._send_count > self._fail_send_after:
            raise self._send_exc
        self.sent.append(payload)


async def test_cleanup_runs_when_client_disconnects_during_exec():
    """exec 流式输出途中客户端断开：生成器关闭 + 清理执行，异常不外泄。"""
    svc = FakeDebugService(lines=("a", "b", "c"))
    ws = FakeWebSocket([{"op": "exec", "command": "ls"}], fail_send_after=1)

    await debug_stream(ws, "s1", svc)

    assert ws.sent == [{"type": "shell_stream", "line": "a", "done": False}]
    assert svc.generators_closed == 1  # aclosing 已关闭命令生成器
    assert svc.unsubscribed == 1
    assert svc.forwarding_stopped == 1


async def test_exec_error_with_broken_send_does_not_escape_cleanup():
    """命令执行失败且回发错误也失败：不再二次抛错逃逸，清理照常执行。"""
    svc = FakeDebugService(exec_error=RuntimeError("shell exploded"))
    # 首次 send 即失败：模拟客户端已断开时的错误回发
    ws = FakeWebSocket([{"op": "exec", "command": "boom"}], fail_send_after=0)

    await debug_stream(ws, "s1", svc)

    assert svc.generators_closed == 1
    assert svc.unsubscribed == 1
    assert svc.forwarding_stopped == 1


async def test_unexpected_error_still_runs_cleanup():
    """非 WebSocketDisconnect 异常（如非法消息）：清理执行后异常照常传播。"""

    class BrokenReceiveWebSocket(FakeWebSocket):
        async def receive_json(self):
            raise ValueError("bad json")

    svc = FakeDebugService()
    ws = BrokenReceiveWebSocket([])

    with pytest.raises(ValueError):
        await debug_stream(ws, "s1", svc)

    assert svc.unsubscribed == 1
    assert svc.forwarding_stopped == 1


async def test_normal_disconnect_runs_cleanup():
    """客户端正常断开（无消息）：清理执行。"""
    svc = FakeDebugService()
    ws = FakeWebSocket([])

    await debug_stream(ws, "s1", svc)

    assert svc.unsubscribed == 1
    assert svc.forwarding_stopped == 1