"""
设备可达性预检
===============

层：基础设施 → ADB。

在调用 adb connect / adb disconnect 之前，以纯 TCP 探测判断 host:port
能否建立连接，避免 adb 在「静默不可达」主机上等待 SYN 重传阶梯 + 命令级超时。
"""

import asyncio
import socket

from app.core.exceptions import DeviceUnreachableError
from app.core.logging import get_logger

logger = get_logger(__name__)


async def probe_tcp(host: str, port: int, timeout: float) -> None:
    """
    探测 host:port 能否建立 TCP 连接。

    成功返回 None；失败抛出 DeviceUnreachableError（message 含中文原因）。
    仅做 TCP 层探测，不验证对端协议。
    """
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout
        )
    except asyncio.TimeoutError:
        logger.warning("probe_timeout", host=host, port=port, timeout=timeout)
        raise DeviceUnreachableError(
            host, port, f"{timeout} 秒内无响应（连接超时），请检查设备是否在线、IP 与端口是否正确"
        )
    except ConnectionRefusedError:
        logger.warning("probe_refused", host=host, port=port)
        raise DeviceUnreachableError(
            host, port, f"连接被拒绝，端口 {port} 未开放（设备可能未开启无线调试或端口不同）"
        )
    except socket.gaierror:
        logger.warning("probe_dns_failed", host=host)
        raise DeviceUnreachableError(host, port, f"无法解析主机名：{host}")
    except OSError as e:
        logger.warning("probe_os_error", host=host, port=port, error=str(e))
        raise DeviceUnreachableError(host, port, f"网络不可达：{e}")

    writer.close()
    try:
        await writer.wait_closed()
    except OSError:
        # 对端 RST 竞态：连接已建立即判定可达，关闭阶段的异常不影响结论
        pass