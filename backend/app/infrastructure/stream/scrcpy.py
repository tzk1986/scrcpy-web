"""
scrcpy-server 视频编码器
========================

层：基础设施 → 视频流。

通过直接启动 scrcpy-server.jar（而非 scrcpy.exe）实现视频流传输。

工作流程：
    1. 将 scrcpy-server.jar 推送到设备（server 退出时会自动删除 JAR，因此每次都要推送）
    2. 使用 adb forward 建立端口转发：tcp:PORT → localabstract:scrcpy
    3. 通过 adb shell 启动 scrcpy-server（tunnel_forward=true 模式）
    4. Python 作为 TCP 客户端连接到 127.0.0.1:PORT → adb 隧道 → 设备端 server 接受
    5. 建立双连接：视频 socket + 控制 socket（server 待两者就绪才开流）
    6. 视频 socket 按 12 字节头协议切包（stream_protocol.read_packets），
       yield 完整包载荷给上层（video.py 中 H264Parser 负责解析为 NALU）
    7. 停止时 kill 进程并清理端口转发

控制输入：
    - 通过控制 socket 发送二进制控制消息（scrcpy 协议）
    - 延迟 <5ms（对比 adb shell input 的 50-200ms）
    - 控制 socket 不可用时自动回退到 adb shell input

视频流协议（scrcpy-server v4.1，官方源码 fa57d7c6 逐字段核对）：
    - 默认 send_stream_meta/send_frame_meta/send_device_meta/send_dummy_byte
      均开启（服务端点 Options/Streamer.java）
    - 建连后客户端先收 1B dummy byte + 64B 设备名
    - 随后 4B codec id（大端，"h264" = 0x68323634；0=禁用流、1=配置错误）
    - 12B session 包：bit63 置位 + 宽高（大端 uint32）；编码器重启时再现
    - 媒体/配置包：12B 头 = 8B PTS/flags（bit62=config、bit61=关键帧）
      + 4B 载荷长度（大端）+ 载荷；config 包载荷为 Annex B SPS/PPS
    - 分辨率取自 session 包，不再依赖 adb shell wm size 子进程
    - 兜底：stream.raw_stream_fallback=true 时回退 raw_stream 裸流
      （64KB 块读 + 上层启发式解析，方案 17 实施项 1a 路径）

参考：
    - scrcpy 官方源码: https://github.com/Genymobile/scrcpy
"""

import asyncio
import time
from typing import Any, AsyncIterator

from app.core.config import settings
from app.core.logging import get_logger
from app.domain.ports import EncoderOpts
from app.scrcpy.server_manager import ServerManager
from app.scrcpy.control_sender import ControlSender, ACTION_DOWN, ACTION_UP, ACTION_MOVE
from app.scrcpy.constants import (
    SCRCPY_SERVER_REMOTE_PATH,
    SCRCPY_SERVER_CLASS,
    SCRCPY_SERVER_VERSION,
    VIDEO_STREAM_FRAME_SIZE,
    ADB_TIMEOUT_SECONDS,
)
from app.scrcpy.stream_protocol import (
    SessionEvent,
    StreamConfigError,
    StreamDisabled,
    UnsupportedCodecError,
    read_packets,
)

logger = get_logger(__name__)


class EncoderStalledError(RuntimeError):
    """编码器卡死：发出 RESET_VIDEO 探针后仍超过 idle_reset 秒无数据
    （E009/E021 同族「活着不产帧」故障，方案 19 实施项 1b）。"""


class ScrcpyEncoder:
    """
    基于 scrcpy-server.jar 的视频编码器。

    实现 domain.ports.VideoEncoder 协议。
    yield 的是原始 H.264 字节流（可能跨多个 NALU），
    上层使用 H264Parser 解析为完整的 NALU。

    同时提供 send_input() 方法，通过控制 socket 发送二进制控制消息。
    """

    def __init__(self) -> None:
        """初始化编码器状态。"""
        self.process: asyncio.subprocess.Process | None = None
        self._running = False
        self._device_id: str = ""
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._control_writer: asyncio.StreamWriter | None = None  # 控制连接
        self._control_sender: ControlSender | None = None  # 控制消息发送器
        self._resolution: tuple[int, int] = (0, 0)  # 屏幕分辨率
        self._server_manager = ServerManager()
        self._local_port = 27183
        self._socket_name: str = "scrcpy"  # 固定 socket 名称
        self._data_queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=100)

    @property
    def resolution(self) -> tuple[int, int]:
        """获取屏幕分辨率。"""
        return self._resolution

    async def start(self, device_id: str, opts: EncoderOpts) -> AsyncIterator[bytes]:
        """
        启动 scrcpy-server 并 yield 原始 H.264 字节。

        注意：max_size 强制设为 0（不缩放），保证视频帧尺寸 == 设备物理分辨率。
        这样前端 canvas 坐标（基于视频帧尺寸）== 设备坐标（scrcpy-server 需要），
        避免坐标映射错位导致点击位置不准。
        """
        # 强制禁用缩放：视频帧尺寸 = 设备物理分辨率
        # 如果不这样做，max_size=1080 会把 1280x720 视频缩小为 1080x607，
        # 导致前端坐标（0-1080）与 scrcpy-server 期望的设备坐标（0-1280）不一致
        opts = EncoderOpts(
            max_size=0,
            bit_rate=opts.bit_rate,
            codec=opts.codec,
            fps=opts.fps,
        )
        logger.info(
            "starting_scrcpy_server_encoder",
            device=device_id,
            max_size=opts.max_size,
            bit_rate=opts.bit_rate,
            fps=opts.fps,
        )

        self._device_id = device_id

        # 基于 device_id 计算唯一端口
        port_offset = hash(device_id) % 100
        self._local_port = 27183 + port_offset

        # socket 名称使用固定的 "scrcpy"（py-scrcpy-client 的做法）
        self._socket_name = "scrcpy"

        # 1. 推送 JAR（server 退出时会删除 JAR，每次必须推送）
        if not await self._server_manager.push_server(device_id):
            raise RuntimeError("Failed to push scrcpy-server.jar to device")

        # 2. 清除旧的端口转发（避免冲突）
        try:
            cleanup = await asyncio.create_subprocess_exec(
                "adb", "-s", device_id, "forward", "--remove",
                f"tcp:{self._local_port}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(cleanup.communicate(), timeout=5)
        except Exception:
            pass  # 忽略清理错误（可能本来就不存在）

        # 3. 转换 bit_rate 格式
        bit_rate_value: int | str = opts.bit_rate
        if isinstance(bit_rate_value, str):
            if bit_rate_value.endswith("M"):
                bit_rate_value = int(float(bit_rate_value[:-1]) * 1000000)
            elif bit_rate_value.endswith("K"):
                bit_rate_value = int(float(bit_rate_value[:-1]) * 1000)
            else:
                bit_rate_value = int(bit_rate_value)

        # 4. 设置端口转发（必须在 server 启动前完成）
        logger.info(
            "setting_up_port_forward",
            device=device_id,
            port=self._local_port,
            socket_name=self._socket_name,
        )
        forward_proc = await asyncio.create_subprocess_exec(
            "adb", "-s", device_id, "forward",
            f"tcp:{self._local_port}",
            f"localabstract:{self._socket_name}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, fwd_err = await asyncio.wait_for(
            forward_proc.communicate(),
            timeout=ADB_TIMEOUT_SECONDS,
        )
        if forward_proc.returncode != 0:
            error_msg = fwd_err.decode().strip()
            logger.error("port_forward_failed", device=device_id, error=error_msg)
            raise RuntimeError(f"adb forward failed: {error_msg}")

        # 5. 构建 scrcpy-server 启动命令
        # 12 字节头协议（方案 17 实施项 1b）：恢复默认 send_stream_meta/
        # send_frame_meta/send_device_meta/send_dummy_byte（全 true），
        # 由服务端按 size 字段切帧、config/keyframe 标志随包携带。
        # 兜底（stream.raw_stream_fallback=true）：raw_stream 裸流 +
        # 上层启发式解析（实施项 1a 路径）。
        raw_fallback = settings().stream.raw_stream_fallback
        cmd = [
            "adb", "-s", device_id, "shell",
            f"CLASSPATH={SCRCPY_SERVER_REMOTE_PATH}",
            "app_process", "/",
            SCRCPY_SERVER_CLASS,
            SCRCPY_SERVER_VERSION,
            "log_level=info",
            f"max_size={opts.max_size}",
            f"max_fps={opts.fps}",
            f"video_bit_rate={bit_rate_value}",
            "video_codec=h264",
            "tunnel_forward=true",
            "control=true",
            "audio=false",
            "show_touches=false",
            "stay_awake=false",
            "power_off_on_close=false",
            "clipboard_autosync=false",
        ]
        if raw_fallback:
            cmd += [
                "send_frame_meta=false",
                "raw_stream=true",   # 禁用 dummy byte + device meta + codec header，直接输出原始 H264
            ]

        logger.info("starting_scrcpy_server", device=device_id)

        # 6. 启动 scrcpy-server 子进程
        self.process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._running = True

        # stderr 固定为 PIPE（上面创建时指定），绑定局部变量便于类型收窄
        assert self.process.stderr is not None
        server_stderr = self.process.stderr

        # 7. 读取 server 启动日志（stderr），等待 "Device:" 确认启动成功
        server_ready = False
        try:
            for _ in range(30):  # 最多等待 3 秒
                if self.process.returncode is not None:
                    stderr_data = await server_stderr.read()
                    error_msg = stderr_data.decode().strip()
                    logger.error("scrcpy_server_start_failed",
                                 device=device_id, error=error_msg)
                    raise RuntimeError(f"scrcpy-server failed: {error_msg}")
                try:
                    line = await asyncio.wait_for(
                        server_stderr.readline(),
                        timeout=0.1
                    )
                    if line:
                        decoded = line.decode().strip()
                        if decoded:
                            logger.debug("scrcpy_server_log",
                                        device=device_id, log=decoded)
                        if "Device:" in decoded:
                            server_ready = True
                            break
                except asyncio.TimeoutError:
                    continue
        except Exception as e:
            if "scrcpy-server failed" in str(e):
                raise
            logger.warning("stderr_read_error", device=device_id, error=str(e))

        if not server_ready:
            logger.warning("server_ready_timeout", device=device_id)

        # 7.5 启动后台任务持续读取 server stderr 日志
        async def read_server_logs() -> None:
            """持续读取 server stderr 输出"""
            try:
                while self._running and self.process and self.process.returncode is None:
                    try:
                        line = await asyncio.wait_for(
                            server_stderr.readline(),
                            timeout=1.0
                        )
                        if line:
                            decoded = line.decode().strip()
                            if decoded:
                                # 使用 info 级别以便调试
                                logger.info("scrcpy_server_log",
                                          device=device_id, log=decoded)
                        elif self.process.returncode is not None:
                            logger.info("server_process_ended_during_log_read",
                                       device=device_id,
                                       returncode=self.process.returncode)
                            break
                    except asyncio.TimeoutError:
                        continue
                    except Exception as e:
                        if "not connected" not in str(e).lower():
                            logger.warning("server_log_read_error",
                                       device=device_id, error=str(e))
                        break
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.warning("server_log_task_error",
                           device=device_id, error=str(e))

        log_task = asyncio.create_task(read_server_logs())

        # 7.6 检查 server 进程状态
        if self.process.returncode is not None:
            stderr_data = await server_stderr.read()
            error_msg = stderr_data.decode().strip()
            logger.error("scrcpy_server_exited_early",
                        device=device_id,
                        returncode=self.process.returncode,
                        stderr=error_msg)
            raise RuntimeError(f"scrcpy-server exited early: {error_msg}")

        # 8. 连接到 scrcpy-server 的 socket（视频 socket）
        logger.info("connecting_to_scrcpy_server", device=device_id)
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", self._local_port),
                timeout=5.0,
            )
        except Exception as e:
            logger.error("socket_connect_failed", device=device_id, error=str(e))
            raise RuntimeError(f"Failed to connect to scrcpy-server: {e}")

        logger.info("connected_to_scrcpy_server", device=device_id)

        # 9. 获取设备分辨率
        # 12B 头协议：分辨率由流内 session 包提供（read_socket 中解析），
        # 此步骤跳过，不再依赖 adb shell wm size 子进程。
        # raw_stream 兜底模式保留 adb wm size 路径。
        if raw_fallback:
            try:
                resolution_proc = await asyncio.create_subprocess_exec(
                    "adb", "-s", device_id, "shell", "wm", "size",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await asyncio.wait_for(resolution_proc.communicate(), timeout=5.0)
                # 解析输出，如 "Physical size: 1080x1920"
                output = stdout.decode().strip()
                for size_line in output.splitlines():
                    if "size" in size_line.lower():
                        # 提取 "1080x1920" 部分
                        parts = size_line.split(":")
                        if len(parts) >= 2:
                            size_str = parts[-1].strip()
                            if "x" in size_str:
                                w, h = size_str.split("x")
                                self._resolution = (int(w), int(h))
                                logger.info("scrcpy_resolution",
                                           device=device_id,
                                           width=self._resolution[0],
                                           height=self._resolution[1])
                                break
            except Exception as e:
                logger.warning("resolution_adb_error", device=device_id, error=str(e))

        # 10. 建立控制 socket
        # ControlSender 需要分辨率：12B 协议下分辨率来自流内 session 包，
        # 由 read_socket 在收到首个 session 时创建；raw 兜底模式分辨率
        # 已在上一步由 adb 获取，直接创建。
        logger.info("establishing_control_connection", device=device_id)
        try:
            _, control_writer = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", self._local_port),
                timeout=3.0,
            )
            self._control_writer = control_writer

            if raw_fallback:
                # 创建控制消息发送器（需要分辨率）
                if self._resolution != (0, 0):
                    self._control_sender = ControlSender(
                        self._control_writer, self._resolution)
                    logger.info("control_sender_created",
                               device=device_id,
                               resolution=self._resolution)
                else:
                    logger.warning("resolution_unknown_control_sender_not_created",
                                  device=device_id)

        except Exception as e:
            logger.warning("control_connection_failed", device=device_id, error=str(e))
            # 控制连接失败不是致命的，会回退到 adb shell input

        # 11. 后台任务：从 socket 读取数据到队列
        # 12B 头协议：按包切分（stream_protocol.read_packets），一包一
        # yield；config/session 包永不丢，媒体包队列满时丢整包保时延
        # （包边界对齐，丢包不产生半帧损坏）。
        async def read_socket() -> None:
            try:
                if raw_fallback:
                    while self._running and self._reader:
                        chunk = await self._reader.read(VIDEO_STREAM_FRAME_SIZE)
                        if not chunk:
                            logger.info("socket_closed", device=device_id)
                            break
                        try:
                            await asyncio.wait_for(
                                self._data_queue.put(chunk),
                                timeout=1.0,
                            )
                        except asyncio.TimeoutError:
                            logger.warning("socket_queue_full_dropping_chunk",
                                           device=device_id, chunk_size=len(chunk))
                else:
                    assert self._reader is not None
                    async for event in read_packets(self._reader):
                        if isinstance(event, SessionEvent):
                            self._handle_session_event(device_id, event)
                        elif event.is_config:
                            # config 包（SPS/PPS）是解码前提，宁可阻塞也不丢
                            await self._data_queue.put(event.payload)
                        else:
                            try:
                                await asyncio.wait_for(
                                    self._data_queue.put(event.payload),
                                    timeout=1.0,
                                )
                            except asyncio.TimeoutError:
                                logger.warning(
                                    "socket_queue_full_dropping_packet",
                                    device=device_id,
                                    packet_size=len(event.payload))
            except StreamDisabled:
                logger.warning("video_stream_disabled_by_device", device=device_id)
            except StreamConfigError:
                logger.error("video_stream_config_error_on_device", device=device_id)
            except UnsupportedCodecError as e:
                logger.error("video_stream_unsupported_codec",
                             device=device_id, error=str(e))
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error("socket_read_error", device=device_id, error=str(e))
            finally:
                try:
                    await self._data_queue.put(None)
                except Exception:
                    pass

        read_task = asyncio.create_task(read_socket())

        # 12. 从队列 yield 数据
        idle_reset = float(settings().stream.idle_reset_seconds)
        tick = min(2.0, idle_reset / 2) if idle_reset > 0 else 2.0
        last_data_at = time.monotonic()
        reset_sent_at: float | None = None
        try:
            while self._running:
                try:
                    data = await asyncio.wait_for(
                        self._data_queue.get(), timeout=tick
                    )
                    if data is None:
                        logger.info("queue_sentinel_received", device=device_id)
                        break
                    last_data_at = time.monotonic()
                    reset_sent_at = None
                    yield data
                except asyncio.TimeoutError:
                    if self.process and self.process.returncode is not None:
                        logger.info("server_process_ended",
                                    device=device_id,
                                    returncode=self.process.returncode)
                        break
                    if idle_reset <= 0 or self._control_sender is None:
                        continue
                    now = time.monotonic()
                    if reset_sent_at is None:
                        if now - last_data_at >= idle_reset:
                            reset_sent_at = now
                            try:
                                await self._control_sender.reset_video()
                                logger.info("idle_reset_video_sent",
                                            device=device_id,
                                            idle_s=round(now - last_data_at, 1))
                            except Exception as e:
                                logger.warning("idle_reset_video_failed",
                                               device=device_id, error=str(e))
                                # 发送失败时清除锁存，下一个 tick 重试
                                # （粒度 idle_reset/2，风暴安全）
                                reset_sent_at = None
                    elif now - reset_sent_at >= idle_reset:
                        raise EncoderStalledError(
                            f"no data {now - reset_sent_at:.1f}s after RESET_VIDEO")
                    continue
        except EncoderStalledError as e:
            # 探针异常不能被 stream_error 吞掉，必须上抛给 StreamService 自愈
            logger.error("encoder_stalled", device=device_id, error=str(e))
            raise
        except Exception as e:
            logger.error("stream_error", device=device_id, error=str(e))
        finally:
            # 取消日志读取任务
            log_task.cancel()
            try:
                await log_task
            except asyncio.CancelledError:
                pass
            # 取消数据读取任务
            read_task.cancel()
            try:
                await read_task
            except asyncio.CancelledError:
                pass
            await self.stop()

    # =========================================================================
    # 协议事件处理
    # =========================================================================

    def _handle_session_event(self, device_id: str, event: SessionEvent) -> None:
        """
        处理 12B 协议 session 包：更新分辨率并（首包时）创建 ControlSender。

        session 包出现于流起始与编码器重启（resize/码率切换），
        分辨率以此为准（替代 adb shell wm size）。
        """
        self._resolution = (event.width, event.height)
        logger.info("session_meta_received", device=device_id,
                    width=event.width, height=event.height,
                    client_resized=event.client_resized)

        if self._control_sender is not None:
            self._control_sender.update_resolution(self._resolution)
        elif self._control_writer is not None:
            self._control_sender = ControlSender(
                self._control_writer, self._resolution)
            logger.info("control_sender_created",
                       device=device_id, resolution=self._resolution)

    # =========================================================================
    # 控制输入方法
    # =========================================================================

    async def send_input(self, data: dict[str, Any]) -> None:
        """
        通过控制 socket 发送输入事件。

        参数：
            data: JSON 格式的输入数据，包含 action 字段和对应参数。

        支持的 action：
            - "touch": {"action": "touch", "x": 100, "y": 200}
            - "swipe": {"action": "swipe", "x1": 100, "y1": 200, "x2": 300, "y2": 400, "duration": 300}
            - "long_press": {"action": "long_press", "x": 100, "y": 200, "duration": 1000}
            - "key": {"action": "key", "keycode": 4}
            - "text": {"action": "text", "text": "hello"}

        如果控制 socket 不可用，自动回退到 adb shell input。
        """
        logger.info("send_input_called", device=self._device_id,
                    action=data.get("action"),
                    has_control_sender=self._control_sender is not None,
                    resolution=self._resolution)

        if not self._control_sender or self._resolution == (0, 0):
            logger.warning("control_socket_unavailable_fallback_to_adb",
                          device=self._device_id,
                          has_control_sender=self._control_sender is not None,
                          resolution=self._resolution)
            await self._fallback_adb_input(data)
            return

        action = data.get("action")

        try:
            if action == "touch":
                x, y = data["x"], data["y"]
                logger.info("sending_touch", device=self._device_id, x=x, y=y)
                await self._control_sender.touch(x, y, ACTION_DOWN)
                await self._control_sender.touch(x, y, ACTION_UP)

            elif action == "swipe":
                await self._send_swipe(data)

            elif action == "long_press":
                x, y = data["x"], data["y"]
                duration = data.get("duration", 1000)
                logger.info("sending_long_press", device=self._device_id,
                            x=x, y=y, duration=duration)
                await self._control_sender.touch(x, y, ACTION_DOWN)
                await asyncio.sleep(duration / 1000.0)
                await self._control_sender.touch(x, y, ACTION_UP)

            elif action == "key":
                keycode = data["keycode"]
                await self._control_sender.keycode(keycode, ACTION_DOWN)
                await self._control_sender.keycode(keycode, ACTION_UP)

            elif action == "text":
                text = data["text"]
                await self._control_sender.text(text)

            else:
                logger.warning("unknown_input_action", action=action)
                await self._fallback_adb_input(data)

        except Exception as e:
            logger.error("control_send_failed", device=self._device_id, error=str(e))
            # 回退到 adb shell input
            await self._fallback_adb_input(data)

    async def request_keyframe(self) -> None:
        """立即催出新 IDR（前端回切场景，方案 19 实施项 3）；无控制通道时 no-op。"""
        if self._control_sender is not None:
            await self._control_sender.reset_video()

    async def _send_swipe(self, data: dict[str, Any]) -> None:
        """
        连续 MOVE 事件滑动（参考 py-scrcpy-client swipe()）。

        参数：
            data: 滑动数据，包含 x1, y1, x2, y2, duration
        """
        x1, y1 = data["x1"], data["y1"]
        x2, y2 = data["x2"], data["y2"]
        duration = data.get("duration", 300)

        assert self._control_sender is not None

        await self._control_sender.touch(x1, y1, ACTION_DOWN)

        # 短距离或短时间：直接 UP
        distance = ((x2-x1)**2 + (y2-y1)**2) ** 0.5
        if distance < 10 or duration < 100:
            await self._control_sender.touch(x2, y2, ACTION_UP)
            return

        # 限制最大步数，避免过多 MOVE 事件
        step_length = 5
        total_steps = min(int(distance / step_length), 100)
        if total_steps < 1:
            total_steps = 1
        step_delay = duration / 1000.0 / total_steps

        dx = x2 - x1
        dy = y2 - y1
        for i in range(1, total_steps + 1):
            ratio = i / total_steps
            nx = x1 + int(dx * ratio)
            ny = y1 + int(dy * ratio)
            await self._control_sender.touch(nx, ny, ACTION_MOVE)
            await asyncio.sleep(step_delay)

        await self._control_sender.touch(x2, y2, ACTION_UP)

    async def _fallback_adb_input(self, data: dict[str, Any]) -> None:
        """
        回退到 adb shell input（控制 socket 不可用时）。

        参数：
            data: JSON 格式的输入数据
        """
        action = data.get("action")

        try:
            if action == "touch":
                x, y = data["x"], data["y"]
                await asyncio.create_subprocess_exec(
                    "adb", "-s", self._device_id, "shell", "input", "tap", str(x), str(y))

            elif action == "swipe":
                x1, y1 = data["x1"], data["y1"]
                x2, y2 = data["x2"], data["y2"]
                duration = data.get("duration", 300)
                await asyncio.create_subprocess_exec(
                    "adb", "-s", self._device_id, "shell", "input", "swipe",
                    str(x1), str(y1), str(x2), str(y2), str(duration))

            elif action == "long_press":
                x, y = data["x"], data["y"]
                duration = data.get("duration", 1000)
                # 同点长时 swipe 是 adb 模拟长按的标准方式
                await asyncio.create_subprocess_exec(
                    "adb", "-s", self._device_id, "shell", "input", "swipe",
                    str(x), str(y), str(x), str(y), str(duration))

            elif action == "key":
                keycode = data["keycode"]
                await asyncio.create_subprocess_exec(
                    "adb", "-s", self._device_id, "shell", "input", "keyevent", str(keycode))

            elif action == "text":
                text = data["text"]
                await asyncio.create_subprocess_exec(
                    "adb", "-s", self._device_id, "shell", "input", "text", text)

            else:
                logger.warning("unknown_input_action_fallback", action=action)

        except Exception as e:
            logger.error("fallback_adb_input_failed", device=self._device_id, error=str(e))

    async def stop(self) -> None:
        """停止编码并释放资源。"""
        logger.info("stopping_scrcpy_encoder", device=self._device_id)
        self._running = False

        # 关闭控制 socket
        if self._control_writer:
            try:
                self._control_writer.close()
                await self._control_writer.wait_closed()
            except Exception:
                pass
            self._control_writer = None
            self._control_sender = None

        # 关闭视频 socket
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
            self._writer = None
            self._reader = None

        # 终止 server 进程
        if self.process:
            try:
                self.process.terminate()
                try:
                    await asyncio.wait_for(self.process.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    self.process.kill()
                    await self.process.wait()
            except Exception as e:
                logger.warning("terminate_error", error=str(e))
            finally:
                self.process = None

        # 清理端口转发
        if self._device_id:
            try:
                cleanup = await asyncio.create_subprocess_exec(
                    "adb", "-s", self._device_id, "forward", "--remove",
                    f"tcp:{self._local_port}",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await asyncio.wait_for(cleanup.communicate(), timeout=5)
            except Exception:
                pass

        # 清空队列
        while not self._data_queue.empty():
            try:
                self._data_queue.get_nowait()
            except Exception:
                break

        logger.info("scrcpy_encoder_stopped", device=self._device_id)
        self._device_id = ""
        self._socket_name = "scrcpy"
        self._resolution = (0, 0)
