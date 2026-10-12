"""
录屏 HTTP 端点（backend/app/interfaces/http/recording.py）行为测试
=================================================================

用 FakeRecordingService 经 app.dependency_overrides 注入（仿
test_devices_api.py 模式），覆盖：
    - POST /api/recording/{device_id}/start   启动（200 会话）/ 互斥（409）
    - POST /api/recording/{device_id}/stop    收尾终态（200）/ 无会话（404）
    - GET  /api/recording/{device_id}         会话透传 / 无会话（404 NO_RECORDING）
    - GET  /api/recording/                    active 列表（终态会话不出现）

不触碰真实设备。
"""

from dataclasses import asdict

import pytest

from app.core.exceptions import ConflictError
from app.deps import get_recording_service
from app.domain.recording import RecordingSession, RecordingState
from app.main import app


def make_session(device_id: str, state: str) -> RecordingSession:
    """构造真 RecordingSession 实例（结构同 RecordingService）。"""
    return RecordingSession(id=f"rec-{device_id}", device_id=device_id, state=state)


class FakeRecordingService:
    """替身：内存会话表（key=device_id）+ 单设备互斥，镜像 RecordingService 语义。"""

    def __init__(self) -> None:
        self.sessions: dict[str, RecordingSession] = {}

    async def start(self, device_id: str) -> RecordingSession:
        existing = self.sessions.get(device_id)
        if existing is not None and existing.state in (
            RecordingState.RECORDING.value,
            RecordingState.STOPPING.value,
            RecordingState.PULLING.value,
        ):
            raise ConflictError()
        session = make_session(device_id, RecordingState.RECORDING.value)
        self.sessions[device_id] = session
        return session

    async def stop(self, device_id: str) -> RecordingSession | None:
        session = self.sessions.get(device_id)
        if session is None:
            return None
        if session.state in (RecordingState.DONE.value, RecordingState.FAILED.value):
            return session
        session.state = RecordingState.DONE.value
        return session

    def get(self, device_id: str) -> RecordingSession | None:
        return self.sessions.get(device_id)

    def list_all(self) -> list[RecordingSession]:
        return list(self.sessions.values())


@pytest.fixture
def fake() -> FakeRecordingService:
    return FakeRecordingService()


# ---------------------------------------------------------------------------
# 启动
# ---------------------------------------------------------------------------

def test_start_returns_session_id_and_state(client, fake: FakeRecordingService) -> None:
    """启动成功返回 200 + recording 状态会话（含 id/device_id）。"""
    app.dependency_overrides[get_recording_service] = lambda: fake
    try:
        response = client.post("/api/recording/d1/start")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()["session"]
    assert body["state"] == RecordingState.RECORDING.value
    assert body["device_id"] == "d1"
    assert body["id"]


def test_start_conflict_409(client, fake: FakeRecordingService) -> None:
    """同一设备进行态会话互斥：第二次 start 返回 409 RECORDING_IN_PROGRESS。"""
    app.dependency_overrides[get_recording_service] = lambda: fake
    try:
        assert client.post("/api/recording/d1/start").status_code == 200
        response = client.post("/api/recording/d1/start")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "RECORDING_IN_PROGRESS"


# ---------------------------------------------------------------------------
# 停止
# ---------------------------------------------------------------------------

def test_stop_returns_terminal_state(client, fake: FakeRecordingService) -> None:
    """停止返回 200 + 终态会话（done/failed）。"""
    app.dependency_overrides[get_recording_service] = lambda: fake
    try:
        client.post("/api/recording/d1/start")
        response = client.post("/api/recording/d1/stop")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["session"]["state"] in (
        RecordingState.DONE.value,
        RecordingState.FAILED.value,
    )


def test_stop_missing_404(client, fake: FakeRecordingService) -> None:
    """无会话停止返回 404 NO_RECORDING（服务 stop 返回 None 语义）。"""
    app.dependency_overrides[get_recording_service] = lambda: fake
    try:
        response = client.post("/api/recording/dx/stop")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NO_RECORDING"


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------

def test_get_session_passthrough(client, fake: FakeRecordingService) -> None:
    """查询命中：200 原样透传会话。"""
    session = make_session("d1", RecordingState.RECORDING.value)
    fake.sessions["d1"] = session
    app.dependency_overrides[get_recording_service] = lambda: fake
    try:
        response = client.get("/api/recording/d1")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {"session": asdict(session)}


def test_get_missing_404(client, fake: FakeRecordingService) -> None:
    """查询无会话返回 404 NO_RECORDING。"""
    app.dependency_overrides[get_recording_service] = lambda: fake
    try:
        response = client.get("/api/recording/dx")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "NO_RECORDING"
    assert "dx" in body["error"]["message"]


# ---------------------------------------------------------------------------
# 列表
# ---------------------------------------------------------------------------

def test_list_active_sessions(client, fake: FakeRecordingService) -> None:
    """列表仅含进行态会话：终态会话不出现（历史以落盘文件为准）。

    注：请求路径不带尾斜杠——SPA fallback catch-all（main.py）会先于
    Starlette redirect_slashes 吞掉尾斜杠路径（/api/devices/ 同理 404），
    与既有列表端点测试先例一致。
    """
    fake.sessions["d1"] = make_session("d1", RecordingState.RECORDING.value)
    fake.sessions["d2"] = make_session("d2", RecordingState.DONE.value)
    app.dependency_overrides[get_recording_service] = lambda: fake
    try:
        response = client.get("/api/recording")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    recordings = response.json()["recordings"]
    assert [item["device_id"] for item in recordings] == ["d1"]