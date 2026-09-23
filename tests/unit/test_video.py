"""
视频流 WebSocket 端点（interfaces/ws/video.py）行为测试
=======================================================

用 TestClient（隔离 app + dependency_overrides 注入 FakeStreamService）覆盖：

    - packet 模式：SPS/PPS 触发 config 下发、每 chunk 单 AU 发送、
      同包多 NALU 合并、config 前 VCL 丢弃、config 后 SPS/PPS 不再入帧流、
      编码参数变化（SPS/PPS 与已下发不同）重发 config、
      config 透传 idle_reset_seconds（方案 19 实施项 1a）；
    - epoch 变化：解析器重置并重新下发 config（跨重启半截 NALU 丢弃）；
    - 兜底模式：aggregate_aus 聚帧、跨 chunk 悬空前缀 pending 合并、
      config 前 AU 丢弃；
    - 客户端输入：touch 转发编码器、stats 上报（非法值归一为 0.0）、
      restarting 码率预告与同值去重；
    - 生命周期：流正常结束/抛异常/连接断开时 stop_stream 与 error 消息；
    - _send_config codec 提取边界（3/4 字节起始码、SPS 过短回退默认值）。

末尾另有直接调用 video_stream 的底层异常路径用例（send 抛
WebSocketDisconnect、restarting/error 的 send_json 失败被吞掉），
这些在 TestClient 编排下无法稳定复现（客户端退出会取消服务端任务）。

注意：H264Parser 的 NALU 跨 chunk 滞后语义——chunk 内最后一个 NALU 要等
下一个起始码（通常来自下一个 chunk）到达才被产出，故断言按此建模。
"""

import asyncio
import json
import threading
import time
from collections.abc import AsyncIterator, Callable, Sequence
from typing import Any

import pytest
from fastapi import FastAPI, WebSocketDisconnect
from fastapi.testclient import TestClient

from app.deps import get_stream_service
from app.interfaces.ws import video as video_module
from app.interfaces.ws.video import video_stream

DEV = "unit-video-device"


# ---------------------------------------------------------------------------
# H264 测试数据
# ---------------------------------------------------------------------------

def nalu(header: int, tag: bytes) -> bytes:
    """构造 4 字节起始码的 NALU。"""
    return bytes([0, 0, 0, 1, header]) + tag


SPS_AVC = nalu(0x67, b"\x42\xC0\x1E")   # → codec avc1.42C01E
PPS_MAIN = nalu(0x68, b"\xCE\x38\x80")
SPS_HIGH = nalu(0x67, b"\x64\x00\x28")  # → codec avc1.640028
PPS_HIGH = nalu(0x68, b"\xEE\x3C\x80")

IDR_0 = nalu(0x65, b"IDR0")
IDR_A = nalu(0x65, b"IDRA")
IDR_B = nalu(0x65, b"IDRB")
IDR_C = nalu(0x65, b"IDRC")
IDR_1 = nalu(0x65, b"IDR1")
IDR_3 = nalu(0x65, b"IDR3")
IDR_X = nalu(0x65, b"IDRX")
IDR_Y = nalu(0x65, b"IDRY")
IDR_Z = nalu(0x65, b"IDRZ")
P_1 = nalu(0x41, b"P001")
P_2 = nalu(0x41, b"P002")
P_4 = nalu(0x41, b"P004")
P_A = nalu(0x41, b"PA01")
P_C = nalu(0x41, b"PC01")
P_Z = nalu(0x41, b"PZ01")
SEI_A = nalu(0x06, b"SEIA")
SEI_B = nalu(0x06, b"SEIB")


# ---------------------------------------------------------------------------
# 手写替身（风格同 tests/application/test_stream_adaptive.py）
# ---------------------------------------------------------------------------

class FakeEncoder:
    """编码器替身：记录 send_input 调用；_control_sender 供端点日志访问。"""

    def __init__(self, resolution: tuple[int, int] = (1080, 1920)) -> None:
        self.resolution = resolution
        self._control_sender = None
        self.inputs: list[dict[str, Any]] = []

    async def send_input(self, data: dict[str, Any]) -> None:
        self.inputs.append(data)


class FakeStreamService:
    """StreamService 替身：按预设 chunk 序列产流，记录端点调用的方法与参数。

    参数：
        chunks: 依次 yield 的完整包载荷（Annex B）。
        packet_mode: use_packet_protocol() 返回值。
        encoder: get_encoder() 返回值（None 模拟无编码器）。
        fps_to_bitrate: report_client_fps 收到该 fps 时写入的待生效码率，
            供 peek_pending_bitrate 读取以触发 restarting 通知。
        epoch_after: {第 N 个 chunk: 新 epoch}，产出第 N 个 chunk 前切换，
            模拟自适应码率重启（端点读到新 epoch 后重置解析器）。
        hold_until: chunks 耗尽后轮询等待（asyncio.sleep 让出事件循环），
            供测试控制产流结束时机；服务端任务被取消时无残留。
        error_after: 产出前 N 个 chunk 后抛 error（N=0 立即抛）。
    """

    def __init__(
        self,
        chunks: Sequence[bytes],
        *,
        packet_mode: bool = True,
        encoder: FakeEncoder | None = None,
        fps_to_bitrate: dict[float, int] | None = None,
        epoch_after: dict[int, int] | None = None,
        hold_until: Callable[[], bool] | None = None,
        error_after: int | None = None,
        error: Exception | None = None,
    ) -> None:
        self.chunks = list(chunks)
        self.packet_mode = packet_mode
        self.encoder = encoder
        self.fps_to_bitrate = fps_to_bitrate or {}
        self.epoch_after = epoch_after or {}
        self.hold_until = hold_until
        self.error_after = error_after
        self.error = error or RuntimeError("stream failed")
        self.epoch = 0
        self.pending: dict[str, int | None] = {}
        self.reported_fps: list[tuple[str, float]] = []
        self.keyframe_requests: list[str] = []
        self.stop_calls: list[str] = []

    # --- StreamService 接口 -------------------------------------------------

    def get_stream_epoch(self, device_id: str) -> int:
        return self.epoch

    def use_packet_protocol(self) -> bool:
        return self.packet_mode

    def idle_reset_seconds(self) -> float:
        return 5.0

    def get_encoder(self, device_id: str) -> FakeEncoder | None:
        return self.encoder

    def report_client_fps(self, device_id: str, fps: float, now: float | None = None) -> None:
        self.reported_fps.append((device_id, fps))
        if fps in self.fps_to_bitrate:
            self.pending[device_id] = self.fps_to_bitrate[fps]

    def peek_pending_bitrate(self, device_id: str) -> int | None:
        return self.pending.get(device_id)

    async def request_keyframe(self, device_id: str, now: float | None = None) -> bool:
        self.keyframe_requests.append(device_id)
        return True

    async def stop_stream(self, device_id: str) -> None:
        self.stop_calls.append(device_id)

    async def start_stream(self, device_id: str) -> AsyncIterator[bytes]:
        for index, chunk in enumerate(self.chunks):
            if (index + 1) in self.epoch_after:
                self.epoch = self.epoch_after[index + 1]
            if self.error_after is not None and index == self.error_after:
                raise self.error
            yield chunk
        if self.error_after is not None and self.error_after >= len(self.chunks):
            raise self.error
        if self.hold_until is not None:
            while not self.hold_until():
                await asyncio.sleep(0.002)


class ExplodingStatsService(FakeStreamService):
    """report_client_fps 抛异常：模拟自适应决策器内部故障。"""

    def report_client_fps(self, device_id: str, fps: float, now: float | None = None) -> None:
        self.reported_fps.append((device_id, fps))
        raise RuntimeError("advisor exploded")


class FakeWS:
    """直接调用 video_stream 时的 WebSocket 替身（不经 TestClient 编排）。

    可脚本化：预置客户端消息、空队列时抛 WebSocketDisconnect（模拟客户端
    断开）、指定类型的 send_json / 第 N 条起的 send_bytes 抛异常。
    """

    def __init__(
        self,
        incoming: Sequence[dict[str, Any]] = (),
        *,
        disconnect_when_empty: bool = False,
        fail_send_json_types: Sequence[str] = (),
        fail_send_bytes_after: int | None = None,
    ) -> None:
        self.accepted = False
        self.sent_json: list[dict[str, Any]] = []
        self.json_attempts: list[dict[str, Any]] = []
        self.sent_bytes: list[bytes] = []
        self.disconnect_delivered = False
        self._incoming = list(incoming)
        self._disconnect_when_empty = disconnect_when_empty
        self._fail_json_types = set(fail_send_json_types)
        self._fail_bytes_after = fail_send_bytes_after

    async def accept(self) -> None:
        self.accepted = True

    async def receive_json(self) -> dict[str, Any]:
        if self._incoming:
            return self._incoming.pop(0)
        if self._disconnect_when_empty:
            # 置位后同步抛给 handle_input（中间无 await），供测试确定性判停
            self.disconnect_delivered = True
            raise WebSocketDisconnect(1000)
        await asyncio.Event().wait()  # 阻塞至任务被取消（客户端仍在连接）

    async def send_json(self, data: dict[str, Any]) -> None:
        self.json_attempts.append(data)
        if data.get("type") in self._fail_json_types:
            raise RuntimeError(f"send_json failed: {data.get('type')}")
        self.sent_json.append(data)

    async def send_bytes(self, data: bytes) -> None:
        if self._fail_bytes_after is not None and len(self.sent_bytes) >= self._fail_bytes_after:
            raise WebSocketDisconnect(1006)
        self.sent_bytes.append(data)


# ---------------------------------------------------------------------------
# Fixture 与工具
# ---------------------------------------------------------------------------

@pytest.fixture
def video_client():
    """按需构建「只挂 video 路由 + 覆盖 get_stream_service」的隔离 TestClient。"""
    clients: list[TestClient] = []

    def _make(service: FakeStreamService) -> TestClient:
        app = FastAPI()
        app.include_router(video_module.router)
        app.dependency_overrides[get_stream_service] = lambda: service
        client = TestClient(app)
        clients.append(client)
        return client

    yield _make
    for client in clients:
        client.close()


def wait_for(cond: Callable[[], bool], timeout: float = 2.0) -> bool:
    """测试线程轮询等待（服务端任务运行在 TestClient 的内部事件循环）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.01)
    return False


def recv_json(session: Any) -> dict[str, Any]:
    """读取一条 JSON 文本消息。"""
    message = session.receive()
    assert "text" in message, f"期望 JSON 消息，实得 {message}"
    return json.loads(message["text"])


def recv_binary(session: Any) -> bytes:
    """读取一条二进制消息。"""
    message = session.receive()
    assert "bytes" in message, f"期望二进制消息，实得 {message}"
    return message["bytes"]


# ---------------------------------------------------------------------------
# packet 模式（12B 包头协议，实施项 1b）
# ---------------------------------------------------------------------------

def test_packet_mode_config_and_one_au_per_chunk(video_client):
    """SPS+PPS 齐备 → 下发 config（codec 取自 SPS、宽高取自编码器）；
    之后每个 chunk（=一个完整包）是独立二进制消息。"""
    svc = FakeStreamService(
        [SPS_AVC + PPS_MAIN + IDR_0, P_1, P_2],
        encoder=FakeEncoder(resolution=(1080, 1920)),
    )
    with video_client(svc).websocket_connect(f"/ws/video/{DEV}") as session:
        config = recv_json(session)
        assert config == {
            "type": "config",
            "codec": "avc1.42C01E",
            "width": 1080,
            "height": 1920,
            "description": (SPS_AVC + PPS_MAIN).hex(),
            "idle_reset_seconds": 5.0,
        }
        assert recv_binary(session) == IDR_0   # chunk2 的包边界闭合 IDR_0
        assert recv_binary(session) == P_1     # chunk3 的包边界闭合 P_1
        # P_2 在流结束后仍未被包边界闭合，不做发送（parser 滞后语义）
    assert wait_for(lambda: svc.stop_calls == [DEV])


def test_packet_mode_merges_nalus_and_intercepts_sps_pps(video_client):
    """同包多 NALU 合并为单条消息；config 齐备后 SPS/PPS 仍被拦截、不出现在
    二进制流中；SPS/PPS 与已下发集合不同（编码参数变化）→ 重发 config
    （方案 19 实施项 1a + 终审 Minor #3：SPS 分支只做变化检测，实际发送
    统一由 PPS 分支配对，一条 config 携带新 SPS+新 PPS）。"""
    svc = FakeStreamService(
        [
            SPS_AVC + PPS_MAIN + IDR_A,
            IDR_B + P_A,
            SPS_HIGH + PPS_HIGH + IDR_C,
            P_C,
        ],
        encoder=FakeEncoder(),
    )
    with video_client(svc).websocket_connect(f"/ws/video/{DEV}") as session:
        assert recv_json(session)["codec"] == "avc1.42C01E"
        assert recv_binary(session) == IDR_A + IDR_B  # 多 slice 同包 → 单条消息
        resent = recv_json(session)                   # SPS/PPS 均变化 → PPS 分支配对重发
        assert resent["type"] == "config"
        assert resent["codec"] == "avc1.640028"
        assert resent["description"] == (SPS_HIGH + PPS_HIGH).hex()
        au = recv_binary(session)                     # 含 SPS+PPS 的包 → 只发 VCL
        assert au == P_A
        assert SPS_HIGH not in au
        assert PPS_HIGH not in au
        assert recv_binary(session) == IDR_C


def test_packet_mode_config_deferred_when_pps_precedes_sps(video_client):
    """PPS 先于 SPS 到达：config 不再由 SPS 分支触发（终审 Minor #3），
    待下一个 PPS 到达才配对发送；此前的帧因 config 未就绪被丢弃，
    首次 config 最终正常发出。"""
    svc = FakeStreamService(
        [PPS_MAIN + SPS_AVC + IDR_0, P_1 + PPS_MAIN, P_2, P_4],
        encoder=FakeEncoder(),
    )
    with video_client(svc).websocket_connect(f"/ws/video/{DEV}") as session:
        config = recv_json(session)
        assert config["type"] == "config"
        assert config["codec"] == "avc1.42C01E"
        assert config["description"] == (SPS_AVC + PPS_MAIN).hex()
        # 第一条二进制即 P_2：IDR_0/P_1 在 config 未就绪期间被丢弃
        assert recv_binary(session) == P_2


def test_packet_mode_chunk_progress_logging(video_client):
    """第 100 个 chunk 走增量日志分支；每 chunk 单 AU 的字节全程一致。"""
    svc = FakeStreamService(
        [SPS_AVC + PPS_MAIN + IDR_0]
        + [nalu(0x41, b"P%03d" % i) for i in range(1, 100)],
        encoder=FakeEncoder(),
    )
    with video_client(svc).websocket_connect(f"/ws/video/{DEV}") as session:
        assert recv_json(session)["type"] == "config"
        assert recv_binary(session) == IDR_0
        rest = [recv_binary(session) for _ in range(98)]
        assert rest == [nalu(0x41, b"P%03d" % i) for i in range(1, 99)]
    assert wait_for(lambda: svc.stop_calls == [DEV])


def test_packet_mode_drops_vcl_before_config(video_client):
    """config 齐备前到达的 VCL 帧被丢弃；齐备后同 chunk 的 VCL 正常下发。"""
    svc = FakeStreamService(
        [IDR_X, IDR_Y, SPS_AVC + PPS_MAIN + IDR_Z, P_Z],
        encoder=FakeEncoder(),
    )
    with video_client(svc).websocket_connect(f"/ws/video/{DEV}") as session:
        # 首条消息必须是 config：若 IDR_X 被提前发送，这里会拿到二进制
        assert recv_json(session)["type"] == "config"
        assert recv_binary(session) == IDR_Y   # IDR_X 已被丢弃
        assert recv_binary(session) == IDR_Z


def test_config_carries_idle_reset_and_sps_change_defers_resend(video_client):
    """config 含 idle_reset_seconds 字段（前端回退阈值联动，方案 19 实施项 1a）；
    SPS-only 变化不立即重发（终审 Minor #3：避免携带旧 PPS 的错配 config），
    等新 PPS 到达后配对重发一条新组合；期间帧因 config 未就绪被丢弃。"""
    svc = FakeStreamService(
        [SPS_AVC + PPS_MAIN + IDR_0, SPS_HIGH + P_A, PPS_HIGH + P_2, P_C],
        encoder=FakeEncoder(),
    )
    with video_client(svc).websocket_connect(f"/ws/video/{DEV}") as session:
        first = recv_json(session)
        assert first["type"] == "config"
        assert first["idle_reset_seconds"] == 5.0
        # 第二条 config 必须直接是 SPS_HIGH+PPS_HIGH 的完整新组合
        # （旧行为会在此处先发一条 SPS_HIGH+PPS_MAIN 的错配 config）
        second = recv_json(session)
        assert second["type"] == "config"
        assert second["codec"] == "avc1.640028"
        assert second["description"] == (SPS_HIGH + PPS_HIGH).hex()
        # 旧行为会先收到错配 config（SPS_HIGH+PPS_MAIN）后紧跟 IDR_0；
        # 新行为：IDR_0 在 config 未就绪期间被丢弃，chunk3 中 config2 先于
        # 同 chunk 的 P_A 发出，随后 chunk4 的 P_2
        assert recv_binary(session) == P_A
        assert recv_binary(session) == P_2


# ---------------------------------------------------------------------------
# epoch 变化（自适应码率重启）
# ---------------------------------------------------------------------------

def test_epoch_change_resets_parser_and_resends_config(video_client):
    """epoch 改变 → 解析器重建（跨重启半截 NALU 丢弃）、config_sent 复位，
    客户端收到第二次 config（客户端据此重建解码器）。"""
    svc = FakeStreamService(
        [SPS_AVC + PPS_MAIN + IDR_1, P_2, SPS_HIGH + PPS_HIGH + IDR_3, P_4],
        encoder=FakeEncoder(),
        epoch_after={3: 1},
    )
    with video_client(svc).websocket_connect(f"/ws/video/{DEV}") as session:
        first = recv_json(session)
        assert first["type"] == "config"
        assert first["codec"] == "avc1.42C01E"
        assert recv_binary(session) == IDR_1

        second = recv_json(session)
        assert second["type"] == "config"
        assert second["codec"] == "avc1.640028"
        assert second["description"] == (SPS_HIGH + PPS_HIGH).hex()
        # P_2 是重启前留在旧解析器缓冲区的半截 NALU，未发送
        assert recv_binary(session) == IDR_3


# ---------------------------------------------------------------------------
# _send_config codec 提取边界
# ---------------------------------------------------------------------------

def test_config_codec_falls_back_for_short_sps(video_client):
    """SPS 长度 < 起始码+4 → codec 回退默认 avc1.42E01E，description 仍为原始字节。"""
    short_sps = b"\x00\x00\x00\x01\x67"  # 起始码 + NAL 头，无 profile/level
    svc = FakeStreamService([short_sps + PPS_MAIN + IDR_0, P_1], encoder=FakeEncoder())
    with video_client(svc).websocket_connect(f"/ws/video/{DEV}") as session:
        config = recv_json(session)
        assert config["codec"] == "avc1.42E01E"
        assert config["description"] == (short_sps + PPS_MAIN).hex()
        assert config["width"] == 1080
        assert recv_binary(session) == IDR_0


def test_config_codec_from_three_byte_start_code_sps(video_client):
    """3 字节起始码的 SPS：跳过 3 字节起始码后提取 profile/constraint/level。"""
    sps3 = b"\x00\x00\x01\x67\x42\xC0\x1E"
    pps3 = b"\x00\x00\x01\x68\xCE\x38\x80"
    idr3 = b"\x00\x00\x01\x65IDR6"
    svc = FakeStreamService([sps3 + pps3 + idr3, b"\x00\x00\x01\x41P006"],
                            encoder=FakeEncoder())
    with video_client(svc).websocket_connect(f"/ws/video/{DEV}") as session:
        config = recv_json(session)
        assert config["codec"] == "avc1.42C01E"
        assert config["description"] == (sps3 + pps3).hex()
        assert recv_binary(session) == idr3


# ---------------------------------------------------------------------------
# 兜底模式（raw_stream + 启发式聚帧，实施项 1a）
# ---------------------------------------------------------------------------

def test_fallback_mode_merges_pending_prefix_with_next_vcl(video_client):
    """无 VCL 可挂靠的前缀 NALU（SEI）跨 chunk 悬空，由聚帧器交还后
    与下一批 VCL 合并为单条 AU 消息下发。"""
    svc = FakeStreamService(
        [
            SPS_AVC + PPS_MAIN + SEI_A,
            SEI_B,            # 产出 SEI_A：仅前缀 → 悬空 pending
            IDR_A,            # 产出 SEI_B：仍无 VCL → 两条前缀继续悬空
            P_1,              # 产出 IDR_A → SEI_A+SEI_B+IDR_A 合并为一条 AU
            P_2,              # 产出 P_1 → 独立 AU
        ],
        packet_mode=False,
        encoder=FakeEncoder(),
    )
    with video_client(svc).websocket_connect(f"/ws/video/{DEV}") as session:
        assert recv_json(session)["type"] == "config"
        assert recv_binary(session) == SEI_A + SEI_B + IDR_A
        assert recv_binary(session) == P_1
        # P_2 仍悬在 parser 缓冲区，未发送


def test_fallback_mode_drops_aus_before_config(video_client):
    """兜底模式下 config 未齐备时的完整 AU 同样被丢弃；齐备后继续发送。"""
    svc = FakeStreamService(
        [IDR_X, P_2 + IDR_A, SPS_AVC + PPS_MAIN + IDR_B, P_C],
        packet_mode=False,
        encoder=FakeEncoder(),
    )
    with video_client(svc).websocket_connect(f"/ws/video/{DEV}") as session:
        config = recv_json(session)
        assert config["type"] == "config"
        # chunk2 产出 IDR_X、P_2 两个 AU，但 config 未发送 → 均被丢弃
        assert recv_binary(session) == IDR_A   # chunk3 产出 IDR_A，config 已随同下发
        assert recv_binary(session) == IDR_B


# ---------------------------------------------------------------------------
# 客户端输入（touch / stats / restarting 预告）
# ---------------------------------------------------------------------------

def test_input_touch_forwarded_and_restarting_notified(video_client):
    """touch 经编码器 send_input 转发；stats 触发待生效码率时下发 restarting。"""
    gate = threading.Event()
    encoder = FakeEncoder()
    svc = FakeStreamService(
        [SPS_AVC + PPS_MAIN + IDR_0],
        encoder=encoder,
        fps_to_bitrate={25.0: 2_000_000},
        hold_until=gate.is_set,
    )
    with video_client(svc).websocket_connect(f"/ws/video/{DEV}") as session:
        assert recv_json(session)["type"] == "config"
        session.send_json({"action": "touch", "x": 1, "y": 2})
        # stats 是同步点：restarting 到达即证明 touch 已被输入任务处理
        session.send_json({"op": "stats", "fps": 25})
        assert recv_json(session) == {"type": "restarting", "bit_rate": 2_000_000}
        assert encoder.inputs == [{"action": "touch", "x": 1, "y": 2}]
        assert svc.reported_fps == [(DEV, 25.0)]
        gate.set()
    assert wait_for(lambda: svc.stop_calls == [DEV])


def test_request_keyframe_op_dispatched(video_client):
    """客户端发送 {"op":"request_keyframe"} → 服务层 request_keyframe 被调用，
    记录设备 ID（方案 19 实施项 3：resume 回切请求关键帧）。"""
    gate = threading.Event()
    svc = FakeStreamService(
        [SPS_AVC + PPS_MAIN + IDR_0],
        encoder=FakeEncoder(),
        hold_until=gate.is_set,
    )
    with video_client(svc).websocket_connect(f"/ws/video/{DEV}") as session:
        assert recv_json(session)["type"] == "config"
        session.send_json({"op": "request_keyframe"})
        # 输入任务在服务端事件循环处理，测试线程轮询等待记录写入
        assert wait_for(lambda: svc.keyframe_requests == [DEV])
        gate.set()
    assert wait_for(lambda: svc.stop_calls == [DEV])


def test_input_ignored_without_encoder_and_stats_normalized(video_client):
    """无编码器时 touch 静默忽略（流不中断）；fps 非法/缺失归一为 0.0；
    同一待生效码率不重复通知，新值才再次通知。"""
    gate = threading.Event()
    svc = FakeStreamService(
        [SPS_AVC + PPS_MAIN + IDR_0],
        encoder=None,
        fps_to_bitrate={25.0: 2_000_000, 5.0: 500_000},
        hold_until=gate.is_set,
    )
    with video_client(svc).websocket_connect(f"/ws/video/{DEV}") as session:
        config = recv_json(session)
        assert config["width"] == 0        # 无编码器时宽高退化为 0
        assert config["height"] == 0
        session.send_json({"action": "touch", "x": 9, "y": 9})   # 应被忽略
        session.send_json({"op": "stats", "fps": "abc"})         # 非法 → 0.0
        session.send_json({"op": "stats"})                       # 缺失 → 0.0
        session.send_json({"op": "stats", "fps": 25})            # 同步点
        assert recv_json(session) == {"type": "restarting", "bit_rate": 2_000_000}
        assert svc.reported_fps == [(DEV, 0.0), (DEV, 0.0), (DEV, 25.0)]

        session.send_json({"op": "stats", "fps": 25})            # 同值，不重复通知
        session.send_json({"op": "stats", "fps": 5})             # 新值 → 再通知
        assert recv_json(session) == {"type": "restarting", "bit_rate": 500_000}
        assert svc.reported_fps == [(DEV, 0.0), (DEV, 0.0), (DEV, 25.0), (DEV, 25.0),
                                    (DEV, 5.0)]
        gate.set()
    assert wait_for(lambda: svc.stop_calls == [DEV])


def test_input_handler_stops_on_unexpected_error(video_client):
    """输入处理内部意外异常（stats 上报被决策器抛出）→ 仅结束输入任务，
    视频流继续按包下发，不向客户端报错。"""
    svc = ExplodingStatsService([SPS_AVC + PPS_MAIN + IDR_0, P_1], encoder=FakeEncoder())
    svc.hold_until = lambda: bool(svc.reported_fps)  # 等 stats 被处理（触发崩溃）
    with video_client(svc).websocket_connect(f"/ws/video/{DEV}") as session:
        assert recv_json(session)["type"] == "config"
        session.send_json({"op": "stats", "fps": 25})
        assert recv_binary(session) == IDR_0   # 视频流不受输入任务崩溃影响
    assert wait_for(lambda: svc.stop_calls == [DEV])
    assert svc.reported_fps == [(DEV, 25.0)]


# ---------------------------------------------------------------------------
# 生命周期与异常
# ---------------------------------------------------------------------------

def test_stream_error_notifies_client_and_stops_stream(video_client):
    """流中途抛异常 → 客户端收到 {"type":"error"}，随后 stop_stream 被调用。"""
    svc = FakeStreamService(
        [SPS_AVC + PPS_MAIN + IDR_0],
        encoder=FakeEncoder(),
        error_after=1,
        error=RuntimeError("编码器崩溃"),
    )
    with video_client(svc).websocket_connect(f"/ws/video/{DEV}") as session:
        assert recv_json(session)["type"] == "config"
        error = recv_json(session)
        assert error["type"] == "error"
        assert "编码器崩溃" in error["message"]
    assert wait_for(lambda: svc.stop_calls == [DEV])


async def test_stream_ended_notified_after_normal_completion():
    """流自然耗尽（生成器正常返回，非挂起/非异常）→ 帧发送完毕后补一条
    {"type": "stream_ended"}（方案 19 实施项 5：前端据此立即走回退链）。
    直接调用 video_stream：TestClient 编排下服务端流结束后 receive()
    会永久阻塞（starlette 不在 app 返回后向客户端投递 close），无法断言。"""
    ws = FakeWS()
    svc = FakeStreamService(
        [SPS_AVC + PPS_MAIN + IDR_0, P_1, P_2],
        encoder=FakeEncoder(),
    )
    await video_stream(ws, DEV, svc)

    assert ws.sent_json[-1] == {"type": "stream_ended"}
    assert [m["type"] for m in ws.sent_json] == ["config", "stream_ended"]
    assert ws.sent_bytes == [IDR_0, P_1]  # P_2 仍悬在 parser 缓冲区（滞后语义）
    assert svc.stop_calls == [DEV]


# ---------------------------------------------------------------------------
# 底层连接异常路径（直接调用 video_stream，绕过 TestClient）
# ---------------------------------------------------------------------------

async def test_send_bytes_disconnect_breaks_stream():
    """send_bytes 抛 WebSocketDisconnect（连接已断）→ 静默退出主循环，
    不再尝试发送 error，finally 中停止流并清理输入任务。"""
    ws = FakeWS(fail_send_bytes_after=2)
    svc = FakeStreamService(
        [SPS_AVC + PPS_MAIN + IDR_0]
        + [nalu(0x41, b"P%03d" % i) for i in range(2, 8)],
        encoder=FakeEncoder(),
    )
    await video_stream(ws, DEV, svc)

    assert ws.accepted is True
    assert ws.sent_bytes == [IDR_0, nalu(0x41, b"P002")]  # 第 3 条发送失败
    assert [m["type"] for m in ws.sent_json] == ["config"]
    assert svc.stop_calls == [DEV]


async def test_restarting_notify_send_failure_is_swallowed():
    """restarting 发送失败被吞掉：不影响流继续，输入任务仍存活至 finally 取消。"""
    ws = FakeWS(
        incoming=[{"op": "stats", "fps": 25}],
        fail_send_json_types=("restarting",),
    )
    svc = FakeStreamService(
        [SPS_AVC + PPS_MAIN + IDR_0],
        encoder=FakeEncoder(),
        fps_to_bitrate={25.0: 2_000_000},
        hold_until=lambda: any(m.get("type") == "restarting" for m in ws.json_attempts),
    )
    await video_stream(ws, DEV, svc)

    # 流正常结束后补发 stream_ended（实施项 5）；restarting 的失败尝试仍被记录
    assert ws.json_attempts[-2] == {"type": "restarting", "bit_rate": 2_000_000}
    assert [m["type"] for m in ws.sent_json] == ["config", "stream_ended"]
    assert "restarting" not in [m["type"] for m in ws.sent_json]
    assert svc.reported_fps == [(DEV, 25.0)]
    assert svc.stop_calls == [DEV]


async def test_input_handler_exits_on_client_disconnect():
    """客户端断开（receive_json 抛 WebSocketDisconnect）→ 输入任务正常退出，
    流不受影响继续发送，最后 stop_stream 被调用。"""
    ws = FakeWS(
        incoming=[{"op": "stats", "fps": 25}],
        disconnect_when_empty=True,
    )
    svc = FakeStreamService(
        [SPS_AVC + PPS_MAIN + IDR_0, P_1],
        encoder=FakeEncoder(),
        hold_until=lambda: ws.disconnect_delivered,
    )
    await video_stream(ws, DEV, svc)

    assert ws.disconnect_delivered is True
    assert [m["type"] for m in ws.sent_json] == ["config", "stream_ended"]
    assert svc.reported_fps == [(DEV, 25.0)]
    assert ws.sent_bytes == [IDR_0]
    assert svc.stop_calls == [DEV]


async def test_error_notify_send_failure_is_swallowed():
    """流异常且连接已断（error 消息发送也失败）→ 不再抛异常，仍停止流。"""
    ws = FakeWS(fail_send_json_types=("error",))
    svc = FakeStreamService(
        [SPS_AVC + PPS_MAIN + IDR_0],
        encoder=FakeEncoder(),
        error_after=1,
        error=RuntimeError("boom"),
    )
    await video_stream(ws, DEV, svc)

    assert ws.json_attempts[-1] == {"type": "error", "message": "boom"}
    assert "error" not in [m["type"] for m in ws.sent_json]
    assert svc.stop_calls == [DEV]


async def test_stream_ended_send_failure_is_swallowed():
    """流正常结束但 stream_ended 发送失败（连接已断）→ 异常被吞掉，
    客户端已发送的消息不受影响，仍正常停止流。"""
    ws = FakeWS(fail_send_json_types=("stream_ended",))
    svc = FakeStreamService(
        [SPS_AVC + PPS_MAIN + IDR_0, P_1, P_2],
        encoder=FakeEncoder(),
    )
    await video_stream(ws, DEV, svc)

    assert ws.json_attempts[-1] == {"type": "stream_ended"}
    assert [m["type"] for m in ws.sent_json] == ["config"]
    assert ws.sent_bytes == [IDR_0, P_1]
    assert svc.stop_calls == [DEV]