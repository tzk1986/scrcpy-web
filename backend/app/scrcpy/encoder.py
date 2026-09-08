"""
scrcpy-server 视频编码器
=========================

通过启动 scrcpy-server 子进程，将 Android 设备屏幕编码为 H264 视频流。

工作流程：
    1. 检查并部署 scrcpy-server.jar 到设备
    2. 通过 ADB shell 启动 scrcpy-server 子进程
    3. 建立 adb forward 端口转发
    4. 通过 socket 连接到 scrcpy-server
    5. 从 socket 读取原始 H264 数据
    6. 使用 H264Parser 解析为完整的 NALU
    7. 异步 yield 给上层（StreamService → WebSocket）
    8. 收到停止信号时，kill 子进程并清理资源

scrcpy-server 启动命令：
    adb shell CLASSPATH=/data/local/tmp/scrcpy-server.jar \
        app_process / com.genymobile.scrcpy.Server 2.4 \
        max_size=1080 max_fps=30 video_codec=h264 \
        video_bit_rate=4000000 send_frame_meta=false raw_stream=true

参数说明：
    - max_size: 最大帧尺寸（宽或高，取较大值）
    - max_fps: 最大帧率
    - video_codec: 视频编码格式（h264）
    - video_bit_rate: 目标码率（单位：bps）
    - send_frame_meta=false: 不发送帧元数据
    - raw_stream=true: 输出原始 H264 流

使用示例：
    ```python
    from app.scrcpy import ScrcpyEncoder, EncoderOpts

    encoder = ScrcpyEncoder()
    opts = EncoderOpts(
        max_size=1080,
        bit_rate="4M",
        codec="h264",
        fps=30,
    )

    async for nalu in encoder.start(device_id, opts):
        # nalu 是完整的 H264 NALU（包含起始码）
        await websocket.send_bytes(nalu)

    await encoder.stop()
    ```

注意事项：
    - 每个设备同时只能有一个 scrcpy-server 实例
    - 输出的数据需要通过 H264Parser 解析为完整 NALU
    - scrcpy-server.jar 需要先推送到设备（自动完成）
    - 编码器是异步生成器，支持 async for 迭代
"""

import asyncio
from typing import AsyncIterator

from app.core.logging import get_logger
from .constants import (
    SCRCPY_SERVER_REMOTE_PATH,
    SCRCPY_SERVER_CLASS,
    SCRCPY_SERVER_VERSION,
    VIDEO_STREAM_FRAME_SIZE,
    ADB_TIMEOUT_SECONDS,
)
from .h264_parser import H264Parser
from .server_manager import ServerManager

logger = get_logger(__name__)


class ScrcpyEncoder:
    """
    基于 scrcpy-server 的视频编码器。

    将 Android 设备屏幕编码为 H264 视频流。
    """

    def __init__(self):
        """
        初始化编码器。

        创建子进程引用、运行状态标志、H264 解析器和 server 管理器。
        """
        self.process: asyncio.subprocess.Process | None = None
        self._running = False
        self._device_id: str | None = None
        self._parser = H264Parser()
        self._server_manager = ServerManager()
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._local_port = 27183  # scrcpy 默认端口

    async def start(
        self,
        device_id: str,
        opts,  # EncoderOpts 类型
    ) -> AsyncIterator[bytes]:
        """
        启动 scrcpy-server 并 yield H264 NALU。

        参数：
            device_id: 目标设备的 ADB 序列号。
            opts: 编码器配置（分辨率、码率、编码格式、帧率）。

        返回：
            异步生成器，每次 yield 一个完整的 H264 NALU（包含起始码）。

        实现逻辑：
            1. 检查并部署 scrcpy-server.jar
            2. 构建启动命令
            3. 启动子进程
            4. 建立 adb forward 端口转发
            5. 通过 socket 连接到 scrcpy-server
            6. 循环读取 socket 数据
            7. 使用 H264Parser 解析为完整 NALU
            8. yield 每个 NALU
            9. 在 finally 块中调用 stop() 清理资源

        异常：
            RuntimeError: 如果 scrcpy-server 启动失败。
        """
        logger.info(
            "starting_scrcpy_encoder",
            device=device_id,
            max_size=opts.max_size,
            bit_rate=opts.bit_rate,
            fps=opts.fps,
        )

        self._device_id = device_id

        # 检查并部署 scrcpy-server.jar
        if not await self._server_manager.ensure_server(device_id):
            raise RuntimeError("Failed to deploy scrcpy-server.jar")

        # 转换 bit_rate 格式："4M" -> 4000000
        bit_rate_value = opts.bit_rate
        if isinstance(bit_rate_value, str):
            if bit_rate_value.endswith("M"):
                bit_rate_value = int(float(bit_rate_value[:-1]) * 1000000)
            elif bit_rate_value.endswith("K"):
                bit_rate_value = int(float(bit_rate_value[:-1]) * 1000)
            else:
                bit_rate_value = int(bit_rate_value)

        # 构建启动命令
        cmd = [
            "adb", "-s", device_id, "shell",
            f"CLASSPATH={SCRCPY_SERVER_REMOTE_PATH}",
            "app_process", "/", SCRCPY_SERVER_CLASS,
            SCRCPY_SERVER_VERSION,
            f"max_size={opts.max_size}",
            f"max_fps={opts.fps}",
            f"video_codec={opts.codec}",
            f"video_bit_rate={bit_rate_value}",
            "send_frame_meta=false",
            f"tunnel_forward=true",  # 使用端口转发模式
        ]

        try:
            # 启动子进程
            self.process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            self._running = True

            logger.info(
                "scrcpy_encoder_started",
                device=device_id,
                pid=self.process.pid,
            )

            # 等待服务器启动并读取 stderr
            await asyncio.sleep(0.5)

            # 检查是否启动成功
            if self.process.returncode is not None:
                # 进程已退出，说明启动失败
                stderr_data = await self.process.stderr.read()
                error_msg = stderr_data.decode().strip()
                logger.error(
                    "scrcpy_encoder_start_failed",
                    device=device_id,
                    error=error_msg,
                )
                raise RuntimeError(f"scrcpy-server failed to start: {error_msg}")

            # 读取启动信息
            logger.info("reading_server_stderr", device=device_id)
            try:
                for i in range(10):
                    if self.process.returncode is not None:
                        break
                    stderr_line = await asyncio.wait_for(
                        self.process.stderr.readline(),
                        timeout=0.2
                    )
                    if stderr_line:
                        line = stderr_line.decode().strip()
                        if line:
                            logger.info("scrcpy_server_log", device=device_id, log=line)
                    else:
                        break
            except asyncio.TimeoutError:
                logger.info("stderr_read_timeout", device=device_id)

            logger.info("server_startup_complete", device=device_id)

            # 设置端口转发
            logger.info("setting_up_port_forward", device=device_id)
            forward_proc = await asyncio.create_subprocess_exec(
                "adb", "-s", device_id, "forward",
                f"tcp:{self._local_port}",
                "localabstract:scrcpy",
            )
            await forward_proc.wait()
            if forward_proc.returncode != 0:
                raise RuntimeError("Failed to setup port forward")

            # 连接到 scrcpy-server
            logger.info("connecting_to_server", device=device_id, port=self._local_port)
            self._reader, self._writer = await asyncio.open_connection(
                "127.0.0.1", self._local_port
            )
            logger.info("connected_to_server", device=device_id)

            # 尝试读取设备信息（scrcpy 协议）
            # 注意：当 raw_stream=true 时，可能没有设备信息
            try:
                # 首先是设备名称（64 字节）
                device_name_data = await asyncio.wait_for(
                    self._reader.readexactly(64),
                    timeout=1.0
                )
                device_name = device_name_data.decode("utf-8").rstrip("\x00")
                logger.info("scrcpy_device_name", device=device_id, name=device_name)

                # 然后是设备 ID（32 字节，对于 scrcpy 2.x）
                device_id_data = await asyncio.wait_for(
                    self._reader.readexactly(32),
                    timeout=1.0
                )
                scrcpy_device_id = device_id_data.hex()
                logger.info("scrcpy_device_id", device=device_id, id=scrcpy_device_id[:16] + "...")
            except asyncio.TimeoutError:
                logger.info("no_device_info_raw_stream", device=device_id)
            except Exception as e:
                logger.warning("device_info_read_error", device=device_id, error=str(e))

            # 循环读取 H264 数据
            while self._running:
                if not self._reader:
                    break

                # 读取数据块
                chunk = await self._reader.read(VIDEO_STREAM_FRAME_SIZE)
                if not chunk:
                    # EOF，连接关闭
                    break

                # 使用 H264Parser 解析为完整 NALU
                nalus = self._parser.feed(chunk)

                # yield 每个完整的 NALU
                for nalu in nalus:
                    yield nalu

        except Exception as e:
            logger.error(
                "scrcpy_encoder_error",
                device=device_id,
                error=str(e),
            )
            raise
        finally:
            await self.stop()

    async def stop(self):
        """
        停止编码并释放资源。

        将运行标志设为 False，关闭 socket 连接，kill 子进程并等待退出。
        重置 H264Parser 状态。
        """
        self._running = False

        # 关闭 socket 连接
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception as e:
                logger.warning("socket_close_error", error=str(e))
            self._writer = None
            self._reader = None

        if self.process:
            logger.info("stopping_scrcpy_encoder", device=self._device_id)

            try:
                # 尝试优雅退出
                self.process.terminate()

                # 等待进程退出
                try:
                    await asyncio.wait_for(
                        self.process.wait(),
                        timeout=2.0
                    )
                except asyncio.TimeoutError:
                    # 如果超时，强制 kill
                    logger.warning(
                        "scrcpy_encoder_force_kill",
                        device=self._device_id,
                    )
                    self.process.kill()
                    await self.process.wait()

            except Exception as e:
                logger.error(
                    "scrcpy_encoder_stop_error",
                    device=self._device_id,
                    error=str(e),
                )

            self.process = None

            # 清理端口转发
            if self._device_id:
                try:
                    await asyncio.create_subprocess_exec(
                        "adb", "-s", self._device_id, "forward", "--remove",
                        f"tcp:{self._local_port}",
                    )
                except Exception as e:
                    logger.warning("port_forward_cleanup_error", error=str(e))

        # 重置解析器
        self._parser.reset()

        logger.info("scrcpy_encoder_stopped", device=self._device_id)
        self._device_id = None

    def is_running(self) -> bool:
        """
        检查编码器是否正在运行。

        返回：
            True 表示正在运行，False 表示已停止。
        """
        return self._running and self.process is not None

    async def get_pid(self) -> int | None:
        """
        获取 scrcpy-server 子进程的 PID。

        返回：
            进程 PID，如果未启动返回 None。
        """
        return self.process.pid if self.process else None
