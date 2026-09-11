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
    5. 建立双连接：视频 socket + 控制 socket
    6. 协议握手：读取 dummy byte (0x00) + 设备名 (64字节) + 分辨率 (4字节)
    7. 从视频 socket 读取原始 H.264 字节流
    8. yield 原始字节给上层（video.py 中 H264Parser 负责解析为 NALU）
    9. 停止时 kill 进程并清理端口转发

控制输入：
    - 通过控制 socket 发送二进制控制消息（scrcpy 协议）
    - 延迟 <5ms（对比 adb shell input 的 50-200ms）
    - 控制 socket 不可用时自动回退到 adb shell input

关键细节（参考 py-scrcpy-client 实现）：
    - 使用 scrcpy-server v2.4
    - socket 名称固定为 "scrcpy"（v2.x 协议）
    - 必须建立双连接（视频 + 控制），即使不使用控制功能
    - 必须指定 video_encoder 和 video_codec 参数
    - tunnel_forward=true：server 在设备端监听（LocalServerSocket），等待连接
    - adb forward：Python 连接本地端口 → adb 隧道 → 设备端 server 接受

协议握手流程（scrcpy v2.4）：
    1. 服务器发送 1 字节 dummy byte (0x00)
    2. 服务器发送 64 字节设备名（UTF-8，null 填充）
    3. 服务器发送 4 字节 codec 名称（ASCII 字符串，如 "h264"）
    4. 服务器发送 4 字节宽度（uint32，大端序）
    5. 服务器发送 4 字节高度（uint32，大端序）
    6. 之后开始发送 H.264 视频数据

参考：
    - py-scrcpy-client: https://github.com/leng-yue/py-scrcpy-client
    - scrcpy 官方源码: https://github.com/Genymobile/scrcpy
"""

import asyncio
import struct
from typing import AsyncIterator

from app.core.config import settings
from app.core.logging import get_logger
from app.domain.ports import EncoderOpts
from app.scrcpy.server_manager import ServerManager
from app.scrcpy.control_sender import ControlSender, ACTION_DOWN, ACTION_UP, ACTION_MOVE
from app.scrcpy.constants import (
    SCRCPY_SERVER_REMOTE_PATH,
    SCRCPY_SERVER_CLASS,
    SCRCPY_SERVER_VERSION,
    SCRCPY_SOCKET_NAME_TEMPLATE,
    VIDEO_STREAM_FRAME_SIZE,
    ADB_TIMEOUT_SECONDS,
)

logger = get_logger(__name__)


class ScrcpyEncoder:
    """
    基于 scrcpy-server.jar 的视频编码器。

    实现 domain.ports.VideoEncoder 协议。
    yield 的是原始 H.264 字节流（可能跨多个 NALU），
    上层使用 H264Parser 解析为完整的 NALU。

    同时提供 send_input() 方法，通过控制 socket 发送二进制控制消息。
    """

    def __init__(self):
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
        self._data_queue: asyncio.Queue = asyncio.Queue(maxsize=100)

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
        bit_rate_value = opts.bit_rate
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
            "send_frame_meta=false",
            "raw_stream=true",       # 禁用 dummy byte + device meta + codec header，直接输出原始 H264
            "control=true",
            "audio=false",
            "show_touches=false",
            "stay_awake=false",
            "power_off_on_close=false",
            "clipboard_autosync=false",
        ]

        logger.info("starting_scrcpy_server", device=device_id)

        # 6. 启动 scrcpy-server 子进程
        self.process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._running = True

        # 7. 读取 server 启动日志（stderr），等待 "Device:" 确认启动成功
        server_ready = False
        try:
            for _ in range(30):  # 最多等待 3 秒
                if self.process.returncode is not None:
                    stderr_data = await self.process.stderr.read()
                    error_msg = stderr_data.decode().strip()
                    logger.error("scrcpy_server_start_failed",
                                 device=device_id, error=error_msg)
                    raise RuntimeError(f"scrcpy-server failed: {error_msg}")
                try:
                    line = await asyncio.wait_for(
                        self.process.stderr.readline(),
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
        async def read_server_logs():
            """持续读取 server stderr 输出"""
            try:
                while self._running and self.process and self.process.returncode is None:
                    try:
                        line = await asyncio.wait_for(
                            self.process.stderr.readline(),
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
            stderr_data = await self.process.stderr.read()
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

        # 9. 获取设备分辨率（通过 adb，因为 raw_stream=true 跳过协议握手）
        # raw_stream=true 禁用了 dummy byte / device meta / codec header，
        # 视频 socket 直接输出原始 H264 数据，无需读取协议字段。
        try:
            resolution_proc = await asyncio.create_subprocess_exec(
                "adb", "-s", device_id, "shell", "wm", "size",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(resolution_proc.communicate(), timeout=5.0)
            # 解析输出，如 "Physical size: 1080x1920"
            output = stdout.decode().strip()
            for line in output.splitlines():
                if "size" in line.lower():
                    # 提取 "1080x1920" 部分
                    parts = line.split(":")
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

        # 10. 建立控制 socket 并创建 ControlSender
        logger.info("establishing_control_connection", device=device_id)
        try:
            _, control_writer = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", self._local_port),
                timeout=3.0,
            )
            self._control_writer = control_writer

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
        async def read_socket():
            try:
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
        try:
            while self._running:
                try:
                    data = await asyncio.wait_for(
                        self._data_queue.get(), timeout=2.0
                    )
                    if data is None:
                        logger.info("queue_sentinel_received", device=device_id)
                        break
                    yield data
                except asyncio.TimeoutError:
                    if self.process and self.process.returncode is not None:
                        logger.info("server_process_ended",
                                    device=device_id,
                                    returncode=self.process.returncode)
                        break
                    continue
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
    # 控制输入方法
    # =========================================================================

    async def send_input(self, data: dict):
        """
        通过控制 socket 发送输入事件。

        参数：
            data: JSON 格式的输入数据，包含 action 字段和对应参数。

        支持的 action：
            - "touch": {"action": "touch", "x": 100, "y": 200}
            - "swipe": {"action": "swipe", "x1": 100, "y1": 200, "x2": 300, "y2": 400, "duration": 300}
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

    async def _send_swipe(self, data: dict):
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

    async def _fallback_adb_input(self, data: dict):
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

    async def stop(self):
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
