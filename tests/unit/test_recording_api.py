"""
录屏 HTTP 端点（backend/app/interfaces/http/recording.py）行为测试
=================================================================

用 FakeRecordingService 经 app.dependency_overrides 注入（仿
test_devices_api.py 模式），覆盖：
    - POST /api/recording/{device_id}/start   启动（200 会话）/ 互斥（409）
    - POST /api/recording/{device_id}/stop    收尾终态（200）/ 无会话（404）
    - GET  /api/recording/{device_id}         会话透传 / 无会话（404 NO_RECORDING）
    - GET  /api/recording/                    active 列表（终态会话不出现）
    - GET  /api/recording/{device_id}/file    产物下载（字节一致 / 越界 404 / 无会话 404）

不触碰真实设备。
"""

from dataclasses import asdict
from pathlib import Path

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
        # 端点产物 containment 校验基准目录（测试指定 tmp_path）
        self.dir: Path = Path("data/recordings")

    def resolve_dir(self) -> Path:
        """镜像 RecordingService.resolve_dir：返回落盘目录。"""
        return self.dir

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


# ---------------------------------------------------------------------------
# 文件下载（方案 37 B5：B4 补 file 端点）
# ---------------------------------------------------------------------------

def _done_session_with_file(fake: FakeRecordingService, path: Path) -> None:
    """落一条 done 态会话，local_path 指向指定落盘文件。"""
    fake.sessions["d1"] = RecordingSession(
        id="rec-d1",
        device_id="d1",
        state=RecordingState.DONE.value,
        local_path=str(path),
    )


def test_file_endpoint_streams_mp4_bytes(client, fake: FakeRecordingService, tmp_path) -> None:
    """file 端点：done 会话 + 目录内产物 → 200 字节一致 + video/mp4。"""
    mp4 = tmp_path / "rec-d1.mp4"
    mp4.write_bytes(b"FAKE_MP4_BYTES")
    fake.dir = tmp_path
    _done_session_with_file(fake, mp4)
    app.dependency_overrides[get_recording_service] = lambda: fake
    try:
        response = client.get("/api/recording/d1/file")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.content == b"FAKE_MP4_BYTES"
    assert response.headers["content-type"] == "video/mp4"


def test_file_endpoint_missing_session_404(client, fake: FakeRecordingService) -> None:
    """file 端点：无会话 → 404 NO_RECORDING。"""
    app.dependency_overrides[get_recording_service] = lambda: fake
    try:
        response = client.get("/api/recording/dx/file")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NO_RECORDING"


def test_file_endpoint_not_done_404(client, fake: FakeRecordingService) -> None:
    """file 端点：会话未到 done（recording）→ 404 NO_RECORDING。"""
    fake.sessions["d1"] = make_session("d1", RecordingState.RECORDING.value)
    app.dependency_overrides[get_recording_service] = lambda: fake
    try:
        response = client.get("/api/recording/d1/file")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NO_RECORDING"


def test_file_endpoint_done_without_local_path_404(client, fake: FakeRecordingService) -> None:
    """file 端点：done 但 local_path 为空 → 404 NO_RECORDING。"""
    fake.sessions["d1"] = make_session("d1", RecordingState.DONE.value)
    app.dependency_overrides[get_recording_service] = lambda: fake
    try:
        response = client.get("/api/recording/d1/file")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NO_RECORDING"


def test_file_endpoint_path_escape_404(client, fake: FakeRecordingService, tmp_path) -> None:
    """file 端点：local_path 越出录制目录（containment）→ 404 拒读。"""
    outside = tmp_path / "evil.mp4"
    outside.write_bytes(b"EVIL")
    fake.dir = tmp_path / "recordings"
    fake.dir.mkdir()
    _done_session_with_file(fake, outside)
    app.dependency_overrides[get_recording_service] = lambda: fake
    try:
        response = client.get("/api/recording/d1/file")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NO_RECORDING"


def test_file_endpoint_missing_file_on_disk_404(client, fake: FakeRecordingService, tmp_path) -> None:
    """file 端点：done 但落盘文件已被双限清理 → 404 NO_RECORDING。"""
    fake.dir = tmp_path
    _done_session_with_file(fake, tmp_path / "gone.mp4")
    app.dependency_overrides[get_recording_service] = lambda: fake
    try:
        response = client.get("/api/recording/d1/file")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NO_RECORDING"


# ---------------------------------------------------------------------------
# done 态完备化（方案 42 §4：file_size 信息 + 失效收敛）
# ---------------------------------------------------------------------------

def test_get_done_returns_file_size(client, fake: FakeRecordingService, tmp_path) -> None:
    """GET done 会话且产物在位 → 200 + file_size（面板信息行数据源）。"""
    mp4 = tmp_path / "rec-d1.mp4"
    mp4.write_bytes(b"12345")
    fake.dir = tmp_path
    _done_session_with_file(fake, mp4)
    app.dependency_overrides[get_recording_service] = lambda: fake
    try:
        response = client.get("/api/recording/d1")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["file_size"] == 5


def test_get_done_missing_file_converges_404(client, fake: FakeRecordingService, tmp_path) -> None:
    """产物已被清理（双限/关服清盘）但会话滞留 done → 收敛 failed 并 404（前端静默 idle）。"""
    fake.dir = tmp_path
    _done_session_with_file(fake, tmp_path / "gone.mp4")
    app.dependency_overrides[get_recording_service] = lambda: fake
    try:
        response = client.get("/api/recording/d1")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NO_RECORDING"
    assert fake.sessions["d1"].state == RecordingState.FAILED.value


def test_stop_done_includes_file_size(client, fake: FakeRecordingService, tmp_path) -> None:
    """stop 返回 done 的响应即带 file_size——前端无需二次请求拿大小。"""
    mp4 = tmp_path / "rec-d1.mp4"
    mp4.write_bytes(b"abcdef")
    fake.sessions["d1"] = RecordingSession(
        id="rec-d1",
        device_id="d1",
        state=RecordingState.RECORDING.value,
        local_path=str(mp4),
    )
    app.dependency_overrides[get_recording_service] = lambda: fake
    try:
        response = client.post("/api/recording/d1/stop")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["session"]["state"] == RecordingState.DONE.value
    assert response.json()["file_size"] == 6