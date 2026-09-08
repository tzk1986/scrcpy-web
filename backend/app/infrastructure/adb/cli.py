"""
ADB CLI 驱动 — 基于子进程的 ADB 实现
========================================

层：基础设施 → ADB。

通过 asyncio.create_subprocess_exec 调用 `adb` 命令行工具
实现 AdbDriver 协议（domain/ports.py）。

这是最简单、最可移植的实现——适用于任何 ADB 版本，
不需要额外的 Python 库。权衡是每个命令有子进程开销，
对于当前用例（设备管理，非高频 I/O）是可以接受的。

关键实现细节：
    - 所有命令通过 _run() 或 _run_serial()，处理
      stdout/stderr 捕获和超时
    - stream_logcat() 使用长运行子进程逐行读取
    - screenshot() 使用主机上的临时文件中转 PNG 数据
    - ADB 二进制路径从配置读取（settings().adb.path）

安全考虑：
    - 用户输入的 shell 命令直接传递给 `adb shell`。
      在生产环境中，应添加输入验证/清理。
    - ADB 路径来自配置（非用户输入），所以是可信的。
"""

import asyncio
import tempfile
from pathlib import Path
from typing import AsyncIterator

from app.core.config import settings
from app.core.exceptions import AdbError
from app.core.logging import get_logger
from app.domain.device import DeviceInfo

logger = get_logger(__name__)


class AdbCliDriver:
    """
    基于子进程 CLI 调用的 ADB 驱动。

    实现 domain.ports.AdbDriver 协议。
    """

    def __init__(self):
        """从配置初始化 ADB 二进制路径。"""
        self.adb_path = settings().adb.path

    async def _run(self, *args: str, timeout: int | None = None) -> str:
        """
        执行 ADB 命令并返回 stdout。

        参数：
            *args: 命令参数（如 "devices"、"-s"、"serial"、"shell"、"ls"）。
            timeout: 覆盖配置中的默认超时（秒）。

        返回：
            去除首尾空白的 stdout 字符串。

        异常：
            AdbError: 命令以非零返回码退出时。
            AdbError: ADB 未安装时。
            AdbError: 命令超过超时时。
        """
        timeout = timeout or settings().adb.timeout
        cmd = [self.adb_path, *args]
        logger.debug("adb_command", cmd=cmd)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            logger.error("adb_not_found", path=self.adb_path)
            raise AdbError(
                f"ADB not found at '{self.adb_path}'. "
                "Please install Android SDK Platform Tools and ensure it's in PATH."
            )

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            logger.error("adb_command_timeout", cmd=cmd, timeout=timeout)
            raise AdbError(f"ADB command timed out after {timeout}s: {' '.join(cmd)}")

        if proc.returncode != 0:
            error_msg = stderr.decode().strip()
            logger.error("adb_command_failed", cmd=cmd, returncode=proc.returncode, error=error_msg)
            raise AdbError(f"ADB command failed: {error_msg}")

        return stdout.decode().strip()

    async def _run_serial(self, device_id: str, *args: str) -> str:
        """
        执行针对特定设备的 ADB 命令。

        在命令前添加 "-s {device_id}" 以选择目标设备。

        参数：
            device_id: ADB 序列号。
            *args: 设备选择器之后的命令参数。

        返回：
            去除首尾空白的 stdout 字符串。
        """
        return await self._run("-s", device_id, *args)

    async def list_devices(self) -> list[str]:
        """
        列出已连接设备的序列号。

        解析 `adb devices` 的输出，格式如：
            List of devices attached
            emulator-5554    device
            192.168.1.5:5555 device

        返回：
            序列号列表（只包含有 "device" 的行）。
        """
        output = await self._run("devices")
        lines = output.split("\n")[1:]  # 跳过标题行
        return [line.split()[0] for line in lines if "device" in line]

    async def get_device_info(self, device_id: str) -> DeviceInfo:
        """
        通过 ADB shell 查询设备属性并构建 DeviceInfo。

        执行多个 `adb shell getprop` 和 `adb shell wm size` 命令
        来收集型号、OS 版本、分辨率和电量。

        参数：
            device_id: ADB 序列号。

        返回：
            填充了设备属性的 DeviceInfo。
        """
        model = await self._run_serial(device_id, "shell", "getprop", "ro.product.model")
        os_version = await self._run_serial(
            device_id, "shell", "getprop", "ro.build.version.release"
        )
        wm_size = await self._run_serial(device_id, "shell", "wm", "size")
        resolution = self._parse_resolution(wm_size)
        battery = await self._get_battery_level(device_id)

        return DeviceInfo(
            id=device_id,
            model=model,
            os_version=os_version,
            resolution=resolution,
            battery=battery,
            status="online",
        )

    def _parse_resolution(self, output: str) -> tuple[int, int]:
        """
        从 `wm size` 输出解析屏幕分辨率。

        预期输入格式：
            "Physical size: 1080x1920"
        或有覆盖时：
            "Physical size: 1080x1920\\nOverride size: 720x1280"

        参数：
            output: `adb shell wm size` 的原始输出。

        返回：
            (宽, 高) 元组。解析失败时返回 (0, 0)。
        """
        for line in output.split("\n"):
            if "Physical size" in line:
                size = line.split(":")[1].strip()
                w, h = size.split("x")
                return int(w), int(h)
        return 0, 0

    async def _get_battery_level(self, device_id: str) -> int:
        """
        从 `dumpsys battery` 获取电量。

        解析输出中的 "level: N" 行。

        参数：
            device_id: ADB 序列号。

        返回：
            电量 0-100。解析失败时返回 0。
        """
        output = await self._run_serial(device_id, "shell", "dumpsys", "battery")
        for line in output.split("\n"):
            if "level" in line:
                return int(line.split(":")[1].strip())
        return 0

    async def shell(self, device_id: str, cmd: str) -> str:
        """
        在设备上执行 shell 命令。

        参数：
            device_id: ADB 序列号。
            cmd: 要执行的 shell 命令。

        返回：
            命令 stdout 字符串。
        """
        return await self._run_serial(device_id, "shell", cmd)

    async def check_connection(self, device_id: str) -> bool:
        """
        检查设备是否仍然连接。

        通过执行一个简单的 shell 命令来检测设备是否在线。

        参数：
            device_id: ADB 序列号。

        返回：
            True 表示设备在线，False 表示设备离线。
        """
        try:
            await self._run_serial(device_id, "shell", "echo", "ping")
            return True
        except AdbError:
            logger.debug("device_not_connected", device=device_id)
            return False

    async def connect_tcp(self, ip: str, port: int = 5555) -> str:
        """
        通过 TCP/IP 连接到设备。

        用于无线调试或 USB 连接不稳定时的备用方案。

        参数：
            ip: 设备的 IP 地址。
            port: ADB 端口（默认 5555）。

        返回：
            设备 ID（格式为 "ip:port"）。

        异常：
            AdbError: 连接失败时。
        """
        device_id = f"{ip}:{port}"
        logger.info("connecting_tcp", ip=ip, port=port)

        try:
            output = await self._run("connect", device_id)
            if "connected" in output.lower():
                logger.info("tcp_connected", device=device_id)
                return device_id
            else:
                raise AdbError(f"Failed to connect to {device_id}: {output}")
        except AdbError as e:
            logger.error("tcp_connect_failed", ip=ip, port=port, error=str(e))
            raise

    async def disconnect_tcp(self, ip: str, port: int = 5555):
        """
        断开 TCP/IP 连接。

        参数：
            ip: 设备的 IP 地址。
            port: ADB 端口（默认 5555）。
        """
        device_id = f"{ip}:{port}"
        logger.info("disconnecting_tcp", ip=ip, port=port)

        try:
            await self._run("disconnect", device_id)
            logger.info("tcp_disconnected", device=device_id)
        except AdbError as e:
            logger.warning("tcp_disconnect_failed", device=device_id, error=str(e))
            # 断开失败不是严重错误，只记录警告

    async def stream_logcat(self, device_id: str) -> AsyncIterator[str]:
        """
        逐行流式输出 logcat。

        启动长运行的 `adb logcat -v threadtime` 子进程并
        在每行到达时产出。当生成器关闭时
        （如通过 asyncio.Task.cancel()），子进程被 kill。

        参数：
            device_id: ADB 序列号。

        产出：
            每次迭代产出一行 logcat（UTF-8 字符串）。
        """
        proc = await asyncio.create_subprocess_exec(
            self.adb_path,
            "-s",
            device_id,
            "logcat",
            "-v",
            "threadtime",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                yield line.decode("utf-8", errors="replace")
        finally:
            proc.kill()

    async def push(self, device_id: str, local: str, remote: str):
        """
        将文件从主机推送到设备。

        参数：
            device_id: ADB 序列号。
            local: 主机文件路径。
            remote: 设备文件路径。
        """
        await self._run_serial(device_id, "push", local, remote)

    async def pull(self, device_id: str, remote: str, local: str):
        """
        将文件从设备拉到主机。

        参数：
            device_id: ADB 序列号。
            remote: 设备文件路径。
            local: 主机文件路径。
        """
        await self._run_serial(device_id, "pull", remote, local)

    async def install(self, device_id: str, apk_path: str):
        """
        在设备上安装 APK（使用 -r 参数替换已存在的）。

        参数：
            device_id: ADB 序列号。
            apk_path: APK 文件的主机路径。
        """
        await self._run_serial(device_id, "install", "-r", apk_path)

    async def screenshot(self, device_id: str) -> bytes:
        """
        截取屏幕截图并返回 PNG 字节。

        流程：
            1. 在设备上运行 `screencap -p /sdcard/screenshot.png`
            2. 将文件拉到主机上的临时文件
            3. 清理设备端的文件
            4. 读取并返回临时文件内容

        参数：
            device_id: ADB 序列号。

        返回：
            PNG 图像数据字节。
        """
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            await self._run_serial(
                device_id, "shell", "screencap", "-p", "/sdcard/screenshot.png"
            )
            await self._run_serial(
                device_id, "pull", "/sdcard/screenshot.png", tmp_path
            )
            await self._run_serial(device_id, "shell", "rm", "/sdcard/screenshot.png")

            return Path(tmp_path).read_bytes()
        finally:
            Path(tmp_path).unlink(missing_ok=True)
