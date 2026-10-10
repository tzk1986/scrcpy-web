"""
设备可达性预检
===============

层：基础设施 → ADB。

在调用 adb connect / adb disconnect 之前，以纯 TCP 探测判断 host:port
能否建立连接，避免 adb 在「静默不可达」主机上等待 SYN 重传阶梯 + 命令级超时。

另提供 scan_open_hosts（方案 36）：网段级并发探测，供设备扫描连接使用。
"""

import asyncio
import socket
import time
from ipaddress import IPv4Network

from app.core.config import settings
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


async def scan_open_hosts(
    network: IPv4Network,
    port: int = 5555,
    timeout: float | None = None,
    concurrency: int | None = None,
) -> list[str]:
    """
    并发 TCP 探测网段内所有主机（network.hosts()，不含网络/广播地址）。

    返回开放 port 的主机 IP 列表（ipaddress 数值序，gather 保序）。
    单台失败（超时/拒绝/DNS/网络错误）静默吞掉——不逐台打日志（复用
    probe_tcp 会让 /24 扫描产生上百条 warning），结束仅一条汇总日志。
    timeout/concurrency 为 None 时惰性读取 settings（测试可逃离全局配置）。
    """
    if timeout is None:
        timeout = settings().adb.probe_timeout
    if concurrency is None:
        concurrency = settings().adb.scan_concurrency
    semaphore = asyncio.Semaphore(concurrency)

    async def probe(host: str) -> str | None:
        async with semaphore:
            try:
                _, writer = await asyncio.wait_for(
                    asyncio.open_connection(host, port), timeout=timeout
                )
            except (asyncio.TimeoutError, ConnectionRefusedError, socket.gaierror, OSError):
                return None
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
            return host

    hosts = [str(ip) for ip in network.hosts()]
    started = time.perf_counter()
    results = await asyncio.gather(*(probe(host) for host in hosts))
    open_hosts = [host for host in results if host is not None]
    logger.info(
        "scan_completed",
        cidr=str(network),
        port=port,
        probed=len(hosts),
        found=len(open_hosts),
        elapsed_ms=round((time.perf_counter() - started) * 1000, 1),
    )
    return open_hosts