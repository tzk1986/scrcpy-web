"""
scrcpy-server 视频编码器
=========================

通过启动 scrcpy-server 子进程，将 Android 设备屏幕编码为 H264 视频流。

工作流程：
    1. 检查并部署 scrcpy-server.jar 到设备
    2. 通过 ADB shell 启动 scrcpy-server 子进程
    3. 从子进程的 stdout 管道读取原始 H264 数据
    4. 使用 H264Parser 解析为完整的 NALU
    5. 异步 yield 给上层（StreamService → WebSocket）
    6. 收到停止信号时，kill 子进程并清理资源

scrcpy-server 启动命令：
    adb shell CLASSPATH=/data/local/tmp/scrcpy-server.jar \
        com.genymobile.scrcpy.Server 2.4 \
        max_size=1080 max_fps=30 video_codec=h264 \
        bit_rate=4M send_frame_meta=false raw_stream=true

参数说明：
    - max_size: 最大帧尺寸（宽或高，取较大值）
    - max_fps: 最大帧率
    - video_codec: 视频编码格式（h264）
    - bit_rate: 目标码率
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
            4. 循环读取 stdout 数据
            5. 使用 H264Parser 解析为完整 NALU
            6. yield 每个 NALU
            7. 在 finally 块中调用 stop() 清理资源

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

        # 构建启动命令
        # adb shell CLASSPATH=/data/local/tmp/scrcpy-server.jar \
        #     com.genymobile.scrcpy.Server 2.4 \
        #     max_size=1080 max_fps=30 video_codec=h264 \
        #     bit_rate=4M send_frame_meta=false raw_stream=true
        cmd = [
            "adb", "-s", device_id, "shell",
            f"CLASSPATH={SCRCPY_SERVER_REMOTE_PATH}",
            SCRCPY_SERVER_CLASS,
            SCRCPY_SERVER_VERSION,
            f"max_size={opts.max_size}",
            f"max_fps={opts.fps}",
            f"video_codec={opts.codec}",
            f"bit_rate={opts.bit_rate}",
            "send_frame_meta=false",
            "raw_stream=true",
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

            # 检查是否启动成功
            # 等待一小段时间检查 stderr
            await asyncio.sleep(0.5)
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

            # 循环读取 H264 数据
            while self._running:
                if not self.process.stdout:
                    break

                # 读取数据块
                chunk = await self.process.stdout.read(VIDEO_STREAM_FRAME_SIZE)
                if not chunk:
                    # EOF，进程结束
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

        将运行标志设为 False，kill 子进程并等待退出。
        重置 H264Parser 状态。
        """
        self._running = False

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
