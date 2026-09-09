"""
scrcpy-server 部署管理器
=========================

管理 scrcpy-server.jar 在 Android 设备上的部署。

职责：
    - 检查设备上是否已存在 scrcpy-server.jar
    - 如果不存在，从项目目录推送到设备
    - 验证推送成功
    - 获取 server 版本信息

scrcpy-server 工作流程：
    1. 将 scrcpy-server.jar 推送到 /data/local/tmp/
    2. 通过 ADB shell 启动 server
    3. server 将屏幕编码为 H264 流
    4. 通过 stdout 输出原始 H264 数据

使用示例：
    ```python
    manager = ServerManager()

    # 检查并推送 server（如果不存在）
    await manager.ensure_server(device_id)

    # 强制重新推送
    await manager.push_server(device_id)

    # 获取 server 版本
    version = await manager.get_server_version()
    ```

文件路径：
    本地：backend/app/scrcpy/scrcpy-server.jar
    远程：/data/local/tmp/scrcpy-server.jar
"""

import asyncio
from pathlib import Path

from app.core.logging import get_logger
from .constants import (
    SCRCPY_SERVER_REMOTE_PATH,
    SCRCPY_SERVER_VERSION,
    ADB_TIMEOUT_SECONDS,
)

logger = get_logger(__name__)


class ServerManager:
    """
    scrcpy-server.jar 部署管理器。

    负责将 server 文件推送到 Android 设备并验证。
    """

    def __init__(self):
        """
        初始化 ServerManager。

        确定本地 scrcpy-server.jar 的路径（与当前文件同目录）。
        """
        # scrcpy-server.jar 与当前文件在同一目录
        self._jar_path = Path(__file__).parent / "scrcpy-server.jar"

    async def ensure_server(self, device_id: str) -> bool:
        """
        确保设备上已部署 scrcpy-server.jar。

        检查设备是否已有 server 文件，如果没有则推送。

        参数：
            device_id: 设备的 ADB 序列号。

        返回：
            True 表示 server 已就绪，False 表示推送失败。

        实现逻辑：
            1. 检查本地 jar 文件是否存在
            2. 检查设备上是否已有 server
            3. 如果没有，执行推送
            4. 验证推送成功
        """
        # 检查本地 jar 文件
        if not self._jar_path.exists():
            logger.error(
                "scrcpy_server_jar_not_found",
                path=str(self._jar_path)
            )
            return False

        # 检查设备上是否已存在
        if await self._server_exists(device_id):
            logger.info("scrcpy_server_already_exists", device=device_id)
            return True

        # 推送 server
        return await self.push_server(device_id)

    async def push_server(self, device_id: str) -> bool:
        """
        强制推送 scrcpy-server.jar 到设备。

        参数：
            device_id: 设备的 ADB 序列号。

        返回：
            True 表示推送成功，False 表示失败。

        实现：
            adb push <local_path> /data/local/tmp/scrcpy-server.jar
        """
        logger.info(
            "pushing_scrcpy_server",
            device=device_id,
            local_path=str(self._jar_path)
        )

        try:
            process = await asyncio.create_subprocess_exec(
                "adb", "-s", device_id, "push",
                str(self._jar_path),
                SCRCPY_SERVER_REMOTE_PATH,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=ADB_TIMEOUT_SECONDS * 3  # 推送文件可能需要更长时间
            )

            if process.returncode != 0:
                error_msg = stderr.decode().strip()
                logger.error(
                    "push_server_failed",
                    device=device_id,
                    error=error_msg
                )
                return False

            # 注意：不再调用 _server_exists() 验证
            # 因为在 Windows 上 adb shell 的路径可能被 Git Bash 转义，
            # 导致验证误报失败。adb push 的返回码已足够可靠。

            logger.info("scrcpy_server_pushed", device=device_id)
            return True

        except asyncio.TimeoutError:
            logger.error("push_server_timeout", device=device_id)
            return False
        except Exception as e:
            logger.error("push_server_error", device=device_id, error=str(e))
            return False

    async def _server_exists(self, device_id: str) -> bool:
        """
        检查设备上是否已存在 scrcpy-server.jar。

        参数：
            device_id: 设备的 ADB 序列号。

        返回：
            True 表示文件存在，False 表示不存在或检查失败。

        实现：
            adb shell ls -l /data/local/tmp/scrcpy-server.jar
        """
        try:
            process = await asyncio.create_subprocess_exec(
                "adb", "-s", device_id, "shell",
                "ls", "-l", SCRCPY_SERVER_REMOTE_PATH,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=ADB_TIMEOUT_SECONDS
            )

            # 如果 ls 命令成功（返回码 0），文件存在
            return process.returncode == 0

        except Exception as e:
            logger.debug("check_server_exists_error", device=device_id, error=str(e))
            return False

    async def get_server_version(self, device_id: str) -> str | None:
        """
        获取设备上 scrcpy-server 的版本。

        参数：
            device_id: 设备的 ADB 序列号。

        返回：
            版本号字符串（如 "2.4"），如果无法获取返回 None。

        注意：
            当前实现返回常量版本号。
            未来可以通过启动 server 并解析输出来获取实际版本。
        """
        if await self._server_exists(device_id):
            return SCRCPY_SERVER_VERSION
        return None

    async def delete_server(self, device_id: str) -> bool:
        """
        删除设备上的 scrcpy-server.jar。

        参数：
            device_id: 设备的 ADB 序列号。

        返回：
            True 表示删除成功，False 表示失败。

        实现：
            adb shell rm /data/local/tmp/scrcpy-server.jar
        """
        logger.info("deleting_scrcpy_server", device=device_id)

        try:
            process = await asyncio.create_subprocess_exec(
                "adb", "-s", device_id, "shell",
                "rm", SCRCPY_SERVER_REMOTE_PATH,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=ADB_TIMEOUT_SECONDS
            )

            if process.returncode != 0:
                error_msg = stderr.decode().strip()
                logger.error(
                    "delete_server_failed",
                    device=device_id,
                    error=error_msg
                )
                return False

            logger.info("scrcpy_server_deleted", device=device_id)
            return True

        except Exception as e:
            logger.error("delete_server_error", device=device_id, error=str(e))
            return False

    def get_local_jar_path(self) -> Path:
        """
        获取本地 scrcpy-server.jar 的路径。

        返回：
            Path 对象，指向本地的 jar 文件。
        """
        return self._jar_path
