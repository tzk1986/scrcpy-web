"""
视频流服务
===========

层：应用层。

编排从 Android 设备到客户端的视频流。

流式传输管道：
    设备屏幕 → scrcpy-server（H.264 编码器）→ 原始帧 →
    → WebSocket → 浏览器（WebCodecs 解码器）

本服务：
    1. 从设置中读取编码器配置（max_size、bit_rate 等）
    2. 为每个设备创建独立的编码器实例
    3. 将 H.264 帧 yield 给 WebSocket 处理器
    4. 支持优雅停止（编码器循环检查 active_streams 标志）
    5. 支持多设备并发流式传输

依赖（通过 Protocol 注入）：
    - VideoEncoder: 具体实现是 ScrcpyEncoder（scrcpy-server）

多设备支持：
    每个设备有独立的编码器实例和流状态。
    可以同时为多个设备启动视频流。
"""

import asyncio
import time
from typing import Any, AsyncIterator, Callable

from app.application.bitrate_advisor import AdvisorConfig, BitrateAdvisor, parse_bit_rate
from app.core.config import settings
from app.core.logging import get_logger
from app.domain.ports import EncoderOpts
from app.infrastructure.stream.scrcpy import ScrcpyEncoder

logger = get_logger(__name__)


class StreamService:
    """视频流用例。"""

    def __init__(self, encoder_factory: Callable[[], Any] | None = None) -> None:
        """
        初始化视频流服务。

        为每个设备维护独立的编码器实例。

        参数：
            encoder_factory: 编码器工厂（默认 ScrcpyEncoder），
                注入点便于单测替换为假编码器。
        """
        # 按 device_id 跟踪活跃流
        self.active_streams: dict[str, bool] = {}
        # 按 device_id 跟踪编码器实例
        self.encoders: dict[str, ScrcpyEncoder] = {}
        self._encoder_factory = encoder_factory or ScrcpyEncoder
        # 自适应码率：决策器与待生效的码率（由 report_client_fps 写入，帧循环消费）
        self._advisors: dict[str, BitrateAdvisor] = {}
        self._pending_bitrate: dict[str, int] = {}
        # 每次自适应重启 +1，供 WS 层检测轮次变化并重置 H.264 解析器
        self._epoch: dict[str, int] = {}
        # 重启唤醒事件：pending 写入时 set，静止无帧也能立即切换
        self._restart_events: dict[str, asyncio.Event] = {}

    async def start_stream(self, device_id: str) -> AsyncIterator[bytes]:
        """
        为给定设备启动视频流。

        从配置读取编码器选项，创建编码器实例，启动编码，并产出 H.264 帧。
        循环在每次迭代时检查 active_streams[device_id]——
        如果为 False（由 stop_stream 设置），循环优雅退出。

        单客户端假设（设计限制）：同一设备流已活跃时本方法直接返回（不产帧），
        后到的第二连接会拿到空流并被关闭；客户端断开即调用 stop_stream，
        多观看者场景下互相影响。多人同时观看需上层做 fan-out
        （一路编码广播给多个订阅者），当前未实现。

        自适应码率开启时，外层循环支持运行中重启编码器切换码率档：
        report_client_fps 决策出新档位后，帧循环在下一次取帧时停止
        当前编码器并以新码率重建（scrcpy 协议无运行中改码率消息，
        只能重启；每次切换有约 1-3s 黑屏，由决策器的迟滞+冷却控制频率）。

        参数：
            device_id: 要流式传输的设备的 ADB 序列号。

        产出：
            H.264 帧数据（原始字节）。
        """
        logger.info("starting_video_stream", device=device_id)

        # 检查是否已经有流在运行
        if device_id in self.active_streams:
            logger.warning("stream_already_active", device=device_id)
            return

        s = settings()
        base_bps = parse_bit_rate(s.stream.bit_rate)
        current_bps = base_bps
        if s.stream.adaptive_bitrate:
            tiers = tuple(
                parse_bit_rate(t)
                for t in str(s.stream.bitrate_tiers).split(",")
                if t.strip()
            )
            if tiers:
                self._advisors[device_id] = BitrateAdvisor(
                    AdvisorConfig(
                        tiers_bps=tiers,
                        target_fps=float(s.stream.fps),
                        start_bps=base_bps,
                    )
                )
        self.active_streams[device_id] = True

        # 码率重启事件：静止画面下 scrcpy 不出帧，仅靠"下一帧时消费 pending"
        # 会无限挂起，因此取帧协程与本事件赛跑，事件先到也立即重启。
        restart_event = asyncio.Event()
        self._restart_events[device_id] = restart_event

        try:
            while self.active_streams.get(device_id):
                encoder = self._encoder_factory()
                self.encoders[device_id] = encoder
                opts = EncoderOpts(
                    max_size=s.stream.max_size,
                    bit_rate=str(current_bps),
                    codec=s.stream.codec,
                    fps=s.stream.fps,
                )
                restart_bitrate = None
                frame_task = None
                event_task = None
                try:
                    agen = encoder.start(device_id, opts)
                    while self.active_streams.get(device_id):
                        # pending 存在则事件必已置位（见 report_client_fps），
                        # 不清除，让本回合立即走重启分支
                        if self._pending_bitrate.get(device_id) is None:
                            restart_event.clear()
                        frame_task = asyncio.ensure_future(agen.__anext__())
                        event_task = asyncio.ensure_future(restart_event.wait())
                        done, _ = await asyncio.wait(
                            {frame_task, event_task},
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                        if frame_task in done:
                            try:
                                frame = frame_task.result()
                            except StopAsyncIteration:
                                break
                            yield frame
                        else:
                            pend = self._pending_bitrate.pop(device_id, None)
                            if pend is None or pend == current_bps:
                                continue  # 伪唤醒（pending 已被消费），回到取帧
                            restart_bitrate = pend
                            break
                finally:
                    for t in (frame_task, event_task):
                        if t is not None and not t.done():
                            t.cancel()
                    await encoder.stop()
                    self.encoders.pop(device_id, None)
                if restart_bitrate is None:
                    break
                current_bps = restart_bitrate
                self._epoch[device_id] = self._epoch.get(device_id, 0) + 1
                logger.info(
                    "adaptive_bitrate_restarting_encoder",
                    device=device_id,
                    bit_rate=current_bps,
                    epoch=self._epoch[device_id],
                )
        finally:
            self.active_streams.pop(device_id, None)
            self._advisors.pop(device_id, None)
            self._pending_bitrate.pop(device_id, None)
            self._restart_events.pop(device_id, None)
            self._epoch.pop(device_id, None)
            logger.info("video_stream_stopped", device=device_id)

    def get_stream_epoch(self, device_id: str) -> int:
        """当前流的重启轮次（每次自适应码率重启 +1），WS 层据此重置解析器。"""
        return self._epoch.get(device_id, 0)

    def use_packet_protocol(self) -> bool:
        """
        当前推流是否走 12 字节包头协议（方案 17 实施项 1b）。

        False 表示 stream.raw_stream_fallback 兜底模式（裸流 +
        启发式解析）。WS 层据此决定帧聚合策略：协议模式下每次
        yield 恰为服务端一个完整包（= 一个 AU），包内 NALU 直接
        合并发送；兜底模式下沿用 AU 启发式聚合（实施项 1a 尾步骤）。
        """
        return not settings().stream.raw_stream_fallback

    def idle_reset_seconds(self) -> float:
        """当前空闲保活间隔（秒），0=关闭；随 config 消息下发给前端联动回退阈值。"""
        return float(settings().stream.idle_reset_seconds)

    def peek_pending_bitrate(self, device_id: str) -> int | None:
        """查看待生效的码率切换（帧循环消费前可被 WS 层读到以通知客户端）。"""
        return self._pending_bitrate.get(device_id)

    def report_client_fps(self, device_id: str, fps: float, now: float | None = None) -> None:
        """
        接收客户端上报的实测帧率，喂给自适应决策器。

        决策器认为应切换档位时记录 pending 码率，由 start_stream
        的帧循环在下一次取帧时重启编码器。无活跃流或未开启自适应时为空操作。
        """
        advisor = self._advisors.get(device_id)
        if advisor is None:
            return
        if now is None:
            now = time.monotonic()
        advisor.add_sample(now, fps)
        new_bps = advisor.decide(now)
        if new_bps is not None:
            self._pending_bitrate[device_id] = new_bps
            ev = self._restart_events.get(device_id)
            if ev is not None:
                ev.set()
            logger.info(
                "adaptive_bitrate_switch_pending",
                device=device_id,
                fps=fps,
                bit_rate=new_bps,
            )

    async def stop_stream(self, device_id: str) -> None:
        """
        通知编码器停止给定设备的流式传输。

        设置 start_stream 循环检查的标志，使其在下一次迭代时退出。
        实际清理发生在 start_stream 的 finally 块中。

        参数：
            device_id: 要停止的设备的 ADB 序列号。
        """
        logger.info("stopping_video_stream", device=device_id)
        self.active_streams[device_id] = False

    async def stop_all_streams(self) -> None:
        """
        停止所有活跃的视频流。

        用于应用关闭时的清理。
        """
        logger.info("stopping_all_video_streams", count=len(self.active_streams))
        for device_id in list(self.active_streams.keys()):
            await self.stop_stream(device_id)

    def get_active_streams(self) -> list[str]:
        """
        获取当前活跃的视频流设备列表。

        返回：
            正在流式传输的设备 ID 列表。
        """
        return [device_id for device_id, active in self.active_streams.items() if active]

    def get_encoder(self, device_id: str) -> ScrcpyEncoder | None:
        """
        获取设备对应的编码器实例。

        用于 WebSocket 输入处理：通过编码器实例的 send_input() 方法
        发送二进制控制消息（延迟 <5ms），而非 adb shell input（50-200ms）。

        参数：
            device_id: 设备的 ADB 序列号。

        返回：
            ScrcpyEncoder 实例，如果设备没有活跃流则返回 None。
        """
        return self.encoders.get(device_id)
