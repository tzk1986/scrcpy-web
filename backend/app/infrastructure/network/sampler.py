"""
网络数据采集器
==============

层：基础设施层。

通过 ADB 命令采集 Android 设备的网络状态：
- 网络流量统计（接收/发送字节数、速率）
- 活跃连接列表（TCP/UDP）
- WiFi 状态
- 网络接口信息

参考：
- /proc/net/dev：网络接口流量统计
- /proc/net/tcp、/proc/net/udp：TCP/UDP 连接表
"""

import asyncio
import re
import struct
import time
from dataclasses import dataclass
from typing import AsyncIterator

from app.core.logging import get_logger
from app.domain.ports import AdbDriver

logger = get_logger(__name__)


@dataclass
class NetworkStats:
    """网络统计快照。"""

    ts: float
    rx_bytes: int          # 接收字节数（累计）
    tx_bytes: int          # 发送字节数（累计）
    rx_rate_kbps: float    # 接收速率 KB/s
    tx_rate_kbps: float    # 发送速率 KB/s
    active_connections: int  # 活跃连接数
    wifi_connected: bool
    wifi_ssid: str | None


@dataclass
class NetworkConnection:
    """网络连接信息。"""

    protocol: str          # tcp/udp
    local_addr: str
    local_port: int
    remote_addr: str
    remote_port: int
    state: str             # ESTABLISHED/LISTEN/CLOSE_WAIT/...
    uid: int | None


class NetworkSampler:
    """
    网络数据采集器。

    通过 ADB 命令周期性采集设备的网络状态。

    使用示例：
        sampler = NetworkSampler(adb, "192.168.1.100")
        stats = await sampler.get_stats("192.168.1.100")
        print(f"RX: {stats.rx_bytes}, TX: {stats.tx_bytes}")
    """

    def __init__(self, adb: AdbDriver, device_id: str):
        """
        初始化采样器。

        参数：
            adb: ADB 驱动实例。
            device_id: 设备 ID（ADB 序列号）。
        """
        self.adb = adb
        self.device_id = device_id
        self._prev_rx: int | None = None
        self._prev_tx: int | None = None
        self._prev_ts: float | None = None

    async def get_stats(self, device_id: str) -> NetworkStats:
        """
        获取当前网络统计。

        参数：
            device_id: 设备 ID。

        返回：
            NetworkStats 对象。
        """
        now = time.time()

        # 获取流量统计
        rx_bytes, tx_bytes = await self._get_traffic_stats(device_id)

        # 计算速率
        rx_rate = 0.0
        tx_rate = 0.0
        if self._prev_rx is not None and self._prev_ts is not None:
            elapsed = now - self._prev_ts
            if elapsed > 0:
                rx_rate = ((rx_bytes - self._prev_rx) / 1024) / elapsed
                tx_rate = ((tx_bytes - self._prev_tx) / 1024) / elapsed

        self._prev_rx = rx_bytes
        self._prev_tx = tx_bytes
        self._prev_ts = now

        # 获取连接数
        connections = await self.get_connections(device_id)
        active_count = len([c for c in connections if c.state == "ESTABLISHED"])

        # 获取 WiFi 状态
        wifi_connected, wifi_ssid = await self._get_wifi_status(device_id)

        return NetworkStats(
            ts=now,
            rx_bytes=rx_bytes,
            tx_bytes=tx_bytes,
            rx_rate_kbps=round(rx_rate, 1),
            tx_rate_kbps=round(tx_rate, 1),
            active_connections=active_count,
            wifi_connected=wifi_connected,
            wifi_ssid=wifi_ssid,
        )

    async def _get_traffic_stats(self, device_id: str) -> tuple[int, int]:
        """
        获取网络流量统计。

        从 /proc/net/dev 读取 wlan0（WiFi）的收发字节数。
        如果没有 WiFi，则使用所有接口的总和。

        返回：
            (rx_bytes, tx_bytes) 元组。
        """
        try:
            output = await self.adb.shell(device_id, "cat /proc/net/dev")

            total_rx = 0
            total_tx = 0
            wlan_rx = 0
            wlan_tx = 0

            for line in output.splitlines():
                line = line.strip()
                if ":" not in line:
                    continue

                iface, stats = line.split(":", 1)
                iface = iface.strip()
                parts = stats.split()

                if len(parts) >= 10:
                    rx = int(parts[0])
                    tx = int(parts[8])

                    total_rx += rx
                    total_tx += tx

                    if iface.startswith("wlan") or iface.startswith("eth"):
                        wlan_rx = rx
                        wlan_tx = tx

            # 优先使用 WiFi/有线接口数据
            if wlan_rx > 0 or wlan_tx > 0:
                return wlan_rx, wlan_tx
            return total_rx, total_tx

        except Exception as e:
            logger.warning("get_traffic_stats_failed", device=device_id, error=str(e))
            return 0, 0

    async def get_connections(self, device_id: str) -> list[NetworkConnection]:
        """
        获取活跃连接列表。

        从 /proc/net/tcp 和 /proc/net/udp 解析连接信息。

        参数：
            device_id: 设备 ID。

        返回：
            NetworkConnection 列表。
        """
        connections: list[NetworkConnection] = []

        try:
            # 解析 TCP 连接
            tcp_output = await self.adb.shell(device_id, "cat /proc/net/tcp")
            connections.extend(self._parse_proc_net(tcp_output, "tcp"))

            # 解析 TCP6 连接
            tcp6_output = await self.adb.shell(device_id, "cat /proc/net/tcp6")
            connections.extend(self._parse_proc_net(tcp6_output, "tcp6"))

            # 解析 UDP 连接
            udp_output = await self.adb.shell(device_id, "cat /proc/net/udp")
            connections.extend(self._parse_proc_net(udp_output, "udp"))

        except Exception as e:
            logger.warning("get_connections_failed", device=device_id, error=str(e))

        return connections

    def _parse_proc_net(self, output: str, protocol: str) -> list[NetworkConnection]:
        """解析 /proc/net/tcp 或 /proc/net/udp 输出。"""
        connections: list[NetworkConnection] = []

        # TCP 状态码映射
        tcp_states = {
            "01": "ESTABLISHED",
            "02": "SYN_SENT",
            "03": "SYN_RECV",
            "04": "FIN_WAIT1",
            "05": "FIN_WAIT2",
            "06": "TIME_WAIT",
            "07": "CLOSE",
            "08": "CLOSE_WAIT",
            "09": "LAST_ACK",
            "0A": "LISTEN",
            "0B": "CLOSING",
        }

        lines = output.splitlines()
        for line in lines[1:]:  # 跳过表头
            parts = line.split()
            if len(parts) < 10:
                continue

            try:
                local = parts[1]
                remote = parts[2]
                state_hex = parts[3] if protocol.startswith("tcp") else "01"
                uid = int(parts[7]) if len(parts) > 7 else None

                local_addr, local_port = self._parse_address(local)
                remote_addr, remote_port = self._parse_address(remote)

                state = tcp_states.get(state_hex, f"UNKNOWN({state_hex})")

                connections.append(NetworkConnection(
                    protocol=protocol,
                    local_addr=local_addr,
                    local_port=local_port,
                    remote_addr=remote_addr,
                    remote_port=remote_port,
                    state=state,
                    uid=uid,
                ))
            except Exception:
                continue

        return connections

    def _parse_address(self, addr: str) -> tuple[str, int]:
        """
        解析 /proc/net 中的地址格式。

        格式：hex_ip:hex_port
        例如：0100007F:0050 → 127.0.0.1:80
        """
        parts = addr.split(":")
        if len(parts) != 2:
            return "0.0.0.0", 0

        ip_hex = parts[0]
        port_hex = parts[1]

        # 解析端口
        port = int(port_hex, 16)

        # 解析 IP（小端序）
        if len(ip_hex) == 8:
            # IPv4
            ip_int = int(ip_hex, 16)
            ip_bytes = struct.pack("<I", ip_int)
            ip_addr = f"{ip_bytes[0]}.{ip_bytes[1]}.{ip_bytes[2]}.{ip_bytes[3]}"
        else:
            # IPv6（简化处理）
            ip_addr = f"[{ip_hex}]"

        return ip_addr, port

    async def _get_wifi_status(self, device_id: str) -> tuple[bool, str | None]:
        """
        获取 WiFi 连接状态。

        返回：
            (connected, ssid) 元组。
        """
        try:
            output = await self.adb.shell(device_id, "dumpsys wifi | grep 'mWifiInfo'")

            if "SSID:" not in output:
                return False, None

            # 提取 SSID
            ssid_match = re.search(r"SSID: ([^,]+)", output)
            ssid = ssid_match.group(1).strip() if ssid_match else None

            # 检查是否已连接
            connected = "Wi-Fi is enabled" in output or "state: CONNECTED" in output

            return connected, ssid

        except Exception as e:
            logger.warning("get_wifi_status_failed", device=device_id, error=str(e))
            return False, None

    async def stream_stats(
        self,
        device_id: str,
        interval: float = 1.0,
    ) -> AsyncIterator[NetworkStats]:
        """
        周期性采集网络统计。

        参数：
            device_id: 设备 ID。
            interval: 采样间隔（秒）。

        产出：
            NetworkStats 对象。
        """
        while True:
            try:
                stats = await self.get_stats(device_id)
                yield stats
            except Exception as e:
                logger.error("network_sample_failed", device=device_id, error=str(e))

            await asyncio.sleep(interval)
