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
import os
import secrets
from typing import AsyncIterator

from app.core.config import settings
from app.core.logging import get_logger
from app.domain.ports import EncoderOpts
from app.scrcpy.server_manager import ServerManager
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
    """

    def __init__(self):
        """初始化编码器状态。"""
        self.process: asyncio.subprocess.Process | None = None
        self._running = False
        self._device_id: str = ""
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._control_writer: asyncio.StreamWriter | None = None  # 控制连接
        self._server_manager = ServerManager()
        self._local_port = 27183
        self._socket_name: str = "scrcpy"  # 固定 socket 名称
        self._data_queue: asyncio.Queue = asyncio.Queue(maxsize=100)

    async def start(self, device_id: str, opts: EncoderOpts) -> AsyncIterator[bytes]:
        """
        启动 scrcpy-server 并 yield 原始 H.264 字节。
        """
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
        # 注意：scrcpy v2.x 使用固定 "scrcpy"，v4.x 使用 "scrcpy_<scid>"
        # 但我们使用的 server.jar 需要匹配客户端协议
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
        # adb forward: tcp:PORT → localabstract:scrcpy_<scid_hex>
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
        # 参考 py-scrcpy-client，但不指定 video_encoder（让服务器自动选择）
        # 因为不同设备支持的编码器不同（如 RK3288 使用 OMX.rk.video_encoder.avc）
        # tunnel_forward=true：server 在设备端作为 LocalServerSocket 监听
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
            "video_codec=h264",                        # 指定编码格式
            # 不指定 video_encoder，让服务器自动选择可用的编码器
            "tunnel_forward=true",                     # server 监听模式
            "send_frame_meta=false",                   # 不发送帧元数据
            "control=true",                            # 必须启用控制（即使不用）
            "audio=false",
            "show_touches=false",
            "stay_awake=false",
            "power_off_on_close=false",
            "clipboard_autosync=false",
        ]

        logger.info(
            "starting_scrcpy_server",
            device=device_id,
            socket_name=self._socket_name,
        )

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
                            logger.info("scrcpy_server_log",
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
                                logger.info("scrcpy_server_log",
                                          device=device_id, log=decoded)
                        elif self.process.returncode is not None:
                            break
                    except asyncio.TimeoutError:
                        continue
                    except Exception as e:
                        if "not connected" not in str(e).lower():
                            logger.debug("server_log_read_error",
                                       device=device_id, error=str(e))
                        break
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.debug("server_log_task_error",
                           device=device_id, error=str(e))

        log_task = asyncio.create_task(read_server_logs())

        # 8. 连接到 scrcpy-server 的 socket
        # Python 作为 TCP 客户端，连接到本地端口
        # adb forward 将连接转发到设备端 server 的监听 socket
        logger.info(
            "connecting_to_scrcpy_server",
            device=device_id,
            port=self._local_port,
            socket_name=self._socket_name,
        )
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", self._local_port),
                timeout=5.0,
            )
        except Exception as e:
            logger.error("socket_connect_failed", device=device_id, error=str(e))
            raise RuntimeError(f"Failed to connect to scrcpy-server: {e}")

        logger.info("connected_to_scrcpy_server", device=device_id)

        # 8.5 建立第二个连接（控制 socket）
        # py-scrcpy-client 协议需要两个连接：视频和控制
        logger.info("establishing_control_connection", device=device_id)
        try:
            _, control_writer = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", self._local_port),
                timeout=3.0,
            )
            logger.info("control_connection_established", device=device_id)
            # 保存控制连接以便后续清理
            self._control_writer = control_writer
        except Exception as e:
            logger.warning("control_connection_failed", device=device_id, error=str(e))
            # 控制连接失败不是致命的，继续尝试

        # 9. 读取协议数据（完全按照 scrcpy v2.4 协议实现）
        # 协议流程：
        #   1. 读取 1 字节 dummy byte (应该是 0x00)
        #   2. 读取 64 字节设备名
        #   3. 读取 4 字节 codec 名称（ASCII 字符串，如 "h264"）
        #   4. 读取 4 字节宽度 (uint32, 大端序)
        #   5. 读取 4 字节高度 (uint32, 大端序)
        import struct
        try:
            # 读取 dummy byte
            dummy_byte = await asyncio.wait_for(
                self._reader.readexactly(1),
                timeout=2.0,
            )
            if dummy_byte != b"\x00":
                logger.warning("unexpected_dummy_byte",
                             device=device_id,
                             byte=dummy_byte.hex())

            # 读取设备名
            device_name_data = await asyncio.wait_for(
                self._reader.readexactly(64),
                timeout=3.0,
            )
            device_name = device_name_data.decode("utf-8").rstrip("\x00")
            logger.info("scrcpy_device_name", device=device_id, name=device_name)

            # 读取 codec 名称（4 字节 ASCII 字符串）
            codec_data = await asyncio.wait_for(
                self._reader.readexactly(4),
                timeout=2.0,
            )
            codec_name = codec_data.decode("ascii")
            logger.info("scrcpy_codec", device=device_id, codec=codec_name)

            # 读取宽度（4 字节 uint32，大端序）
            width_data = await asyncio.wait_for(
                self._reader.readexactly(4),
                timeout=2.0,
            )
            width = struct.unpack(">I", width_data)[0]

            # 读取高度（4 字节 uint32，大端序）
            height_data = await asyncio.wait_for(
                self._reader.readexactly(4),
                timeout=2.0,
            )
            height = struct.unpack(">I", height_data)[0]

            logger.info("scrcpy_resolution",
                       device=device_id,
                       width=width,
                       height=height)

        except asyncio.TimeoutError:
            logger.warning("device_info_timeout", device=device_id)
        except Exception as e:
            logger.warning("device_info_error", device=device_id, error=str(e))

        # 10. 后台任务：从 socket 读取数据到队列
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
                        pass
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

        # 11. 从队列 yield 数据
        try:
            while self._running:
                try:
                    data = await asyncio.wait_for(
                        self._data_queue.get(), timeout=2.0
                    )
                    if data is None:
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
