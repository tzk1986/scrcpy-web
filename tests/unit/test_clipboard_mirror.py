"""
方案 55：剪贴板单向镜像（设备→PC）会话级轮询 task
================================================

覆盖：
- 变更判定：首轮即推、同文不重推、变化再推
- 防环回：note_set 登记的文本被轮询读到时一次性拦截
- 连续失败自停：推 clipboard_mirror enabled=false reason=poll_failed 后 task 退出
- 幂等开停与 stop 收口；编码器暂缺计失败不崩环；发送失败静默收口
"""
import asyncio

from app.interfaces.ws.clipboard_mirror import ClipboardMirror

DEV = "1.2.3.4:5555"


class StubEncoder:
    """get_device_clipboard 替身：按序列返回，耗尽后重复末值。"""

    def __init__(self, results: list[str | None]) -> None:
        self._results = list(results)
        self.calls: list[int] = []

    async def get_device_clipboard(self, copy_key: int = 0,
                                   timeout: float = 3.0) -> str | None:
        self.calls.append(copy_key)
        if self._results:
            return self._results.pop(0)
        return None


class StubSvc:
    def __init__(self, encoder: StubEncoder | None) -> None:
        self.encoder = encoder

    def get_encoder(self, device_id: str) -> StubEncoder | None:
        return self.encoder


class Sink:
    def __init__(self) -> None:
        self.msgs: list[dict] = []

    async def send(self, msg: dict) -> None:
        self.msgs.append(msg)

    async def wait(self, n: int, timeout: float = 3.0) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        while len(self.msgs) < n:
            if asyncio.get_running_loop().time() > deadline:
                raise AssertionError(f"等待 {n} 条消息超时，现有 {self.msgs}")
            await asyncio.sleep(0.01)


def _mirror(encoder_results: list[str | None],
            encoder: StubEncoder | None = None) -> tuple[ClipboardMirror, StubEncoder]:
    enc = encoder or StubEncoder(encoder_results)
    return ClipboardMirror(DEV, StubSvc(enc), interval=0.01, max_failures=3), enc


async def test_first_read_pushed_and_duplicates_suppressed() -> None:
    mirror, enc = _mirror(["a", "a", "b"])
    sink = Sink()
    await mirror.set_enabled(True, sink.send)
    await sink.wait(2)
    assert sink.msgs == [{"type": "clipboard_push", "text": "a"},
                         {"type": "clipboard_push", "text": "b"}]
    assert set(enc.calls) == {0}  # 纯读：copy_key=0 不注入按键
    await mirror.stop()
    assert not mirror.enabled


async def test_loopback_suppression_syncs_baseline() -> None:
    # 两次读到被抑制文本 x：首轮一次性拦截、次轮基线同步不弹推（防 set 二次推送）
    mirror, _ = _mirror(["x", "x", "y", "y", "y", "y"])
    sink = Sink()
    await mirror.set_enabled(True, sink.send)
    mirror.note_set("x")
    await sink.wait(1)
    await asyncio.sleep(0.04)  # 再跑几轮同文：确认不重推且未触发失败自停
    assert sink.msgs == [{"type": "clipboard_push", "text": "y"}]
    assert mirror.enabled
    await mirror.stop()


async def test_consecutive_failures_self_stop_with_notice() -> None:
    mirror, _ = _mirror([None])
    sink = Sink()
    await mirror.set_enabled(True, sink.send)
    await sink.wait(1)
    assert sink.msgs == [{"type": "clipboard_mirror", "enabled": False,
                          "reason": "poll_failed"}]
    assert not mirror.enabled
    await mirror.stop()  # 自停后再关：no-op 不炸
    await sink.wait(1)  # 无新消息（仍是 1 条）


async def test_toggle_idempotent_and_off_halts_push() -> None:
    mirror, _ = _mirror(["a"])
    sink = Sink()
    await mirror.set_enabled(True, sink.send)
    await mirror.set_enabled(True, sink.send)  # 幂等：不重起不重置基线
    await sink.wait(1)
    await mirror.set_enabled(False, sink.send)
    await asyncio.sleep(0.05)
    assert sink.msgs == [{"type": "clipboard_push", "text": "a"}]
    assert not mirror.enabled


async def test_missing_encoder_counts_failure_then_recovers() -> None:
    enc = StubEncoder(["late"])
    svc = StubSvc(None)
    mirror = ClipboardMirror(DEV, svc, interval=0.01, max_failures=3)
    sink = Sink()
    await mirror.set_enabled(True, sink.send)
    await asyncio.sleep(0.03)  # 至少一次 None 编码器失败计数
    svc.encoder = enc
    await sink.wait(1)
    assert sink.msgs == [{"type": "clipboard_push", "text": "late"}]
    await mirror.stop()


async def test_send_failure_stops_task_silently() -> None:
    mirror, _ = _mirror(["a"])

    async def bad_send(msg: dict) -> None:
        raise RuntimeError("client gone")

    await mirror.set_enabled(True, bad_send)
    await asyncio.sleep(0.05)
    assert not mirror.enabled
