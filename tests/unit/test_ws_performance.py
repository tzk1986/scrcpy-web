"""WS perf 端点（interfaces/ws/performance.py）消息形状测试（方案 24 §6-10）。

样式沿用 test_ws_debug.py：手写 Fake（不依赖真机 / Starlette 运行时），直调 handler。
"""

from app.interfaces.ws.performance import stream_metrics


class FakeMetrics:
    """PerformanceMetrics 最小替身（handler 仅读属性）。"""

    def __init__(self, ts: float = 1726000000.5) -> None:
        self.ts = ts
        self.cpu_percent = 87.2
        self.total_memory_mb = 4096.0
        self.used_memory_mb = 2048.0
        self.fps = 59.0
        self.jank_count = 0
        self.current_activity = "com.example/.MainActivity"
        self.top_package = "com.example"
        self.fps_fresh = True


class FakePerfService:
    def __init__(self, metrics_list, alerts=None) -> None:
        self._metrics = metrics_list
        self._alerts = alerts if alerts is not None else []
        self.alert_calls: list[tuple[str, float]] = []

    async def stream_metrics(self, device_id: str):
        for m in self._metrics:
            yield m

    def get_alerts(self, device_id: str, sample_ts: float) -> list:
        self.alert_calls.append((device_id, sample_ts))
        return self._alerts


class FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.accepted = False

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        pass


async def test_ws_message_includes_alerts_key():
    """样本消息携带活动告警快照；以样本 ts 取快照；fps_fresh 不透出（方案 §4.4）。"""
    alert = {
        "id": "cpu_percent",
        "value": 87.2,
        "threshold": 80.0,
        "direction": "above",
        "since": 1726000000.0,
        "notify": True,
    }
    svc = FakePerfService([FakeMetrics()], alerts=[alert])
    ws = FakeWebSocket()

    await stream_metrics(ws, "dev-1", service=svc)

    assert ws.accepted is True
    assert len(ws.sent) == 1
    msg = ws.sent[0]
    assert msg["alerts"] == [alert]
    assert msg["cpu_percent"] == 87.2                  # 既有字段不变（向后兼容）
    assert svc.alert_calls == [("dev-1", 1726000000.5)]
    assert "fps_fresh" not in msg                      # 服务端判定字段不透出


async def test_ws_message_alerts_empty_without_alerts():
    """无活动告警时为 []（既有前端忽略多余键）。"""
    svc = FakePerfService([FakeMetrics()])
    ws = FakeWebSocket()

    await stream_metrics(ws, "dev-1", service=svc)

    assert ws.sent[0]["alerts"] == []
