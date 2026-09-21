"""
网络采样器测试（infrastructure/network/sampler.py）
===================================================

用手写 FakeAdb（风格同 tests/unit/test_video.py、tests/application/test_app_service.py）
覆盖：

    - get_stats：首轮零速率、二轮按 (delta/1024)/elapsed 计算、计数器重置归零、
      空数据边界（全零 + 0 连接 + WiFi 未连接）；
    - _get_traffic_stats：wlan 接口优先、无 wlan 时全接口求和、
      头行/无冒号行/字段不足行跳过、ADB 异常返回 (0, 0)；
    - get_connections：tcp/tcp6/udp 三次 shell 合并、中途失败保留已解析结果、
      首个命令失败返回空列表；
    - _parse_proc_net：跳过表头与短行、TCP 状态码映射与 UNKNOWN 回退、
      UDP 状态强制 ESTABLISHED、坏行（非法端口/非法 uid）整行跳过；
    - _parse_address：IPv4 小端序、IPv4 映射 IPv6、纯 IPv6、格式错误回退；
    - _get_wifi_status：SSID 提取、两种已连接判定、SSID 缺失/异常回退；
    - stream_stats：周期产出、单轮失败被吞掉后继续；
    - 时间通过模块级 FakeClock 注入，不做真实等待（interval=0）。

已修复问题（修复后转为回归用例）：
    _parse_address 纯 IPv6 分支原先直接按网络字节序转换，但 /proc/net/tcp6
    与 IPv4 映射地址一致按主机字节序逐 32-bit 字打印，内核形式的 ::1
    （"00000000000000000000000001000000"）会被解成 "::100:0"。
    现统一为逐 32-bit 字反转恢复（见 test_parse_address_ipv6_pure_words_reversed）。
"""

import asyncio

import pytest

from app.core.exceptions import AdbError
import app.infrastructure.network.sampler as sampler_module
from app.infrastructure.network.sampler import NetworkConnection, NetworkSampler

DEV = "net-sampler-device"


# ---------------------------------------------------------------------------
# 测试替身
# ---------------------------------------------------------------------------

class FakeAdb:
    """假 ADB 驱动：按最长命令前缀返回固定输出，可指定命令失败。

    属性：
        outputs: 命令前缀 → shell 输出；无前缀命中时返回空串。
        fail_on: 前缀集合，命中即抛 AdbError（模拟设备失联）。
        calls: (device_id, cmd) 调用历史。
    """

    def __init__(self, outputs: dict[str, str] | None = None):
        self.outputs = dict(outputs or {})
        self.fail_on: set[str] = set()
        self.calls: list[tuple[str, str]] = []

    @property
    def cmds(self) -> list[str]:
        return [cmd for _, cmd in self.calls]

    async def shell(self, device_id: str, cmd: str) -> str:
        self.calls.append((device_id, cmd))
        for prefix in sorted(self.fail_on, key=len, reverse=True):
            if cmd.startswith(prefix):
                raise AdbError(f"ADB command failed: {cmd}")
        for prefix, out in sorted(self.outputs.items(), key=lambda kv: -len(kv[0])):
            if cmd.startswith(prefix):
                return out
        return ""


class FakeClock:
    """可脚本化时钟：time() 依次弹出预设值，保证速率计算可精确断言。"""

    def __init__(self, values: list[float]):
        self._values = list(values)

    def time(self) -> float:
        return self._values.pop(0)


def install_clock(monkeypatch: pytest.MonkeyPatch, values: list[float]) -> FakeClock:
    """把 sampler 模块内的 time 替换为 FakeClock（仅影响被测模块）。"""
    clock = FakeClock(values)
    monkeypatch.setattr(sampler_module, "time", clock)
    return clock


# ---------------------------------------------------------------------------
# /proc/net 测试数据
# ---------------------------------------------------------------------------

def dev_line(iface: str, rx: int, tx: int) -> str:
    """/proc/net/dev 单行：parts[0]=rx，parts[8]=tx。"""
    return f" {iface}: {rx} 12 0 0 0 0 0 0 {tx} 13 0 0 0 0 0 0\n"


DEV_HEADER = (
    "Inter-|   Receive                                                |  Transmit\n"
    " face |bytes    packets errs drop fifo frame compressed multicast"
    "|bytes    packets errs drop fifo colls carrier compressed\n"
)
LO_LINE = dev_line("lo", 102400, 204800)
WLAN_LINE = dev_line("wlan0", 500000, 600000)
ETH_LINE = dev_line("eth0", 123456, 654321)

TCP_OUTPUT = (
    "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt"
    "   uid  timeout inode\n"
    "   0: 0100007F:1F90 00000000:0000 0A 00000000:00000000 00:00000000 00000000"
    "  1000        0 11111 1 0000000000000000 100 0 0 10 0\n"
    "   1: 0100007F:C3B4 5DB8D822:01BB 01 00000000:00000000 00:00000000 00000000"
    "   10123       0 22222 1 0000000000000000 20 4 30 10 -1\n"
    "   2: 0100007F:C3B5 5DB8D822:01BB 01 00000000:00000000 00:00000000 00000000"
    "   10123       0 33333 1 0000000000000000 20 4 30 10 -1\n"
    "   3: 0100007F:C3B6 0A0A0A0A:1F90 FF 00000000:00000000 00:00000000 00000000"
    "   10500       0 44444 1 0000000000000000 20 4 30 10 -1\n"
)

TCP6_OUTPUT = (
    "  sl  local_address                         remote_address"
    "                        st tx_queue rx_queue tr tm->when retrnsmt"
    "   uid  timeout inode\n"
    "   0: 0000000000000000FFFF00000100007F:1F90"
    " 00000000000000000000000000000000:0000 0A 00000000:00000000 00:00000000 00000000"
    "  1000        0 55555 1 0000000000000000 100 0 0 10 0\n"
    "   1: 00000000000000000000000000000000:0050"
    " 00000000000000000000000000000000:0000 01 00000000:00000000 00:00000000 00000000"
    "  1000        0 66666 1 0000000000000000 100 0 0 10 0\n"
)

UDP_OUTPUT = (
    "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt"
    "   uid  timeout inode ref pointer drops\n"
    "   0: 00000000:0035 00000000:0000 07 00000000:00000000 00:00000000 00000000"
    "  1000        0 77777 1 0000000000000000 0\n"
)

WIFI_CONNECTED = "Wi-Fi is enabled\nmWifiInfo SSID: MyNet, BSSID: aa:bb:cc:dd:ee:ff, IP: /192.168.1.5\n"


def std_outputs(dev: str | None = None, wifi: str = WIFI_CONNECTED) -> dict[str, str]:
    """标准命令输出集；dev 传入可覆盖 /proc/net/dev 内容。"""
    return {
        "cat /proc/net/dev": dev if dev is not None else DEV_HEADER + LO_LINE + WLAN_LINE + ETH_LINE,
        "cat /proc/net/tcp": TCP_OUTPUT,
        "cat /proc/net/tcp6": TCP6_OUTPUT,
        "cat /proc/net/udp": UDP_OUTPUT,
        "dumpsys wifi": wifi,
    }


def make_sampler(dev: str | None = None, wifi: str = WIFI_CONNECTED) -> tuple[FakeAdb, NetworkSampler]:
    adb = FakeAdb(std_outputs(dev, wifi))
    return adb, NetworkSampler(adb, DEV)


# ---------------------------------------------------------------------------
# 初始化与 get_stats
# ---------------------------------------------------------------------------

def test_init_stores_deps_and_empty_baseline():
    """构造函数保存依赖，速率基准为空。"""
    adb = FakeAdb()
    sampler = NetworkSampler(adb, DEV)

    assert sampler.adb is adb
    assert sampler.device_id == DEV
    assert sampler._prev_rx is None
    assert sampler._prev_tx is None
    assert sampler._prev_ts is None


async def test_get_stats_first_call_rates_zero(monkeypatch):
    """首轮无基准：速率为 0，但字节数/连接数/WiFi 正常解析。"""
    install_clock(monkeypatch, [1000.0])
    adb, sampler = make_sampler()

    stats = await sampler.get_stats(DEV)

    assert stats.ts == 1000.0
    assert stats.rx_bytes == 500000  # wlan0 接口值
    assert stats.tx_bytes == 600000
    assert stats.rx_rate_kbps == 0.0
    assert stats.tx_rate_kbps == 0.0
    # tcp 2 ESTABLISHED + tcp6 1 ESTABLISHED + udp 1（状态被强制为 ESTABLISHED）
    assert stats.active_connections == 4
    assert stats.wifi_connected is True
    assert stats.wifi_ssid == "MyNet"
    # 基准已写入，供下一轮计算
    assert (sampler._prev_rx, sampler._prev_tx, sampler._prev_ts) == (500000, 600000, 1000.0)


async def test_get_stats_second_call_computes_rates(monkeypatch):
    """二轮按 (delta/1024)/elapsed 计算速率并保留一位小数。"""
    install_clock(monkeypatch, [1000.0, 1002.0])
    adb, sampler = make_sampler()
    await sampler.get_stats(DEV)

    # rx +2048、tx +1024，间隔 2 秒 → 1.0 / 0.5 KB/s
    adb.outputs["cat /proc/net/dev"] = DEV_HEADER + dev_line("wlan0", 502048, 601024)
    stats = await sampler.get_stats(DEV)

    assert stats.rx_bytes == 502048
    assert stats.rx_rate_kbps == 1.0
    assert stats.tx_rate_kbps == 0.5


async def test_get_stats_counter_reset_zeroes_rates(monkeypatch):
    """计数器回绕（负增量）时速率归零，并以新值重建基准。"""
    install_clock(monkeypatch, [0.0, 1.0, 2.0])
    adb, sampler = make_sampler()

    adb.outputs["cat /proc/net/dev"] = DEV_HEADER + dev_line("wlan0", 5000, 5000)
    first = await sampler.get_stats(DEV)

    adb.outputs["cat /proc/net/dev"] = DEV_HEADER + dev_line("wlan0", 10000, 10000)
    second = await sampler.get_stats(DEV)

    adb.outputs["cat /proc/net/dev"] = DEV_HEADER + dev_line("wlan0", 100, 100)
    third = await sampler.get_stats(DEV)

    assert first.rx_rate_kbps == 0.0
    assert second.rx_rate_kbps == 4.9  # 5000B / 1s = 4.8828 → 4.9
    assert third.rx_rate_kbps == 0.0
    assert third.tx_rate_kbps == 0.0
    assert (sampler._prev_rx, sampler._prev_tx, sampler._prev_ts) == (100, 100, 2.0)


async def test_get_stats_handles_empty_device_output(monkeypatch):
    """设备返回空内容（无接口、无连接、无 WiFi）：全部归零且不抛异常。"""
    install_clock(monkeypatch, [7.0])
    adb = FakeAdb({})  # 所有命令返回 ""
    sampler = NetworkSampler(adb, DEV)

    stats = await sampler.get_stats(DEV)

    assert (stats.rx_bytes, stats.tx_bytes) == (0, 0)
    assert (stats.rx_rate_kbps, stats.tx_rate_kbps) == (0.0, 0.0)
    assert stats.active_connections == 0
    assert stats.wifi_connected is False
    assert stats.wifi_ssid is None


# ---------------------------------------------------------------------------
# _get_traffic_stats（/proc/net/dev 解析）
# ---------------------------------------------------------------------------

async def test_traffic_prefers_wlan_interface():
    """存在 wlan 接口时只取 wlan 的收发字节，不做全接口求和。"""
    adb, sampler = make_sampler()

    rx, tx = await sampler._get_traffic_stats(DEV)

    assert (rx, tx) == (500000, 600000)


async def test_traffic_sums_all_interfaces_without_wlan():
    """无 wlan 接口（有线设备）时回退为所有接口求和。"""
    adb, sampler = make_sampler(dev=DEV_HEADER + LO_LINE + ETH_LINE)

    rx, tx = await sampler._get_traffic_stats(DEV)

    assert (rx, tx) == (102400 + 123456, 204800 + 654321)


async def test_traffic_skips_header_and_malformed_lines():
    """表头（无冒号）/无冒号行/字段数不足 10 的行被跳过，不影响正常接口。"""
    dev = (
        DEV_HEADER
        + "garbage-line-without-colon\n"
        + "  sit0: 5 3\n"  # 字段不足 10
        + WLAN_LINE
    )
    adb, sampler = make_sampler(dev=dev)

    rx, tx = await sampler._get_traffic_stats(DEV)

    assert (rx, tx) == (500000, 600000)


async def test_traffic_returns_zero_on_adb_error():
    """ADB 命令失败时返回 (0, 0)，不向上抛异常。"""
    adb, sampler = make_sampler()
    adb.fail_on.add("cat /proc/net/dev")

    rx, tx = await sampler._get_traffic_stats(DEV)

    assert (rx, tx) == (0, 0)


# ---------------------------------------------------------------------------
# get_connections
# ---------------------------------------------------------------------------

async def test_get_connections_merges_tcp_tcp6_udp():
    """按 tcp → tcp6 → udp 顺序调用 3 次 shell 并合并解析结果。"""
    adb, sampler = make_sampler()

    connections = await sampler.get_connections(DEV)

    assert adb.calls == [
        (DEV, "cat /proc/net/tcp"),
        (DEV, "cat /proc/net/tcp6"),
        (DEV, "cat /proc/net/udp"),
    ]
    assert [c.protocol for c in connections] == ["tcp"] * 4 + ["tcp6"] * 2 + ["udp"]

    # tcp：127.0.0.1:8080 LISTEN（uid 1000）
    assert connections[0] == NetworkConnection("tcp", "127.0.0.1", 8080, "0.0.0.0", 0, "LISTEN", 1000)
    # tcp：ESTABLISHED，远端 IP 小端序解析 + uid 解析
    assert connections[1] == NetworkConnection(
        "tcp", "127.0.0.1", 50100, "34.216.184.93", 443, "ESTABLISHED", 10123
    )
    # 未知状态码回退 UNKNOWN
    assert connections[3].state == "UNKNOWN(FF)"
    # tcp6：IPv4 映射地址
    assert connections[4] == NetworkConnection(
        "tcp6", "::ffff:127.0.0.1", 8080, "::", 0, "LISTEN", 1000
    )
    # udp：状态列被强制按 "01" 映射（固定行为，计入活跃连接数）
    assert connections[6] == NetworkConnection("udp", "0.0.0.0", 53, "0.0.0.0", 0, "ESTABLISHED", 1000)


async def test_get_connections_partial_failure_keeps_parsed_results():
    """中途命令失败：已解析的 tcp 结果保留，后续命令不再调用。"""
    adb, sampler = make_sampler()
    adb.fail_on.add("cat /proc/net/tcp6")

    connections = await sampler.get_connections(DEV)

    assert adb.cmds == ["cat /proc/net/tcp", "cat /proc/net/tcp6"]
    assert len(connections) == 4
    assert {c.protocol for c in connections} == {"tcp"}


async def test_get_connections_first_call_failure_returns_empty():
    """首个命令就失败：返回空列表，不抛异常。"""
    adb, sampler = make_sampler()
    adb.fail_on.add("cat /proc/net/tcp")

    connections = await sampler.get_connections(DEV)

    assert connections == []
    assert adb.cmds == ["cat /proc/net/tcp"]


# ---------------------------------------------------------------------------
# _parse_proc_net
# ---------------------------------------------------------------------------

def test_parse_proc_net_skips_header_and_short_lines():
    """表头与字段数不足 10 的行被跳过，仅返回有效行。"""
    _, sampler = make_sampler()
    output = TCP_OUTPUT.splitlines()[0] + "\n   1: 0100007F:1F90 00000000:0000 0A\n"

    assert sampler._parse_proc_net(output, "tcp") == []


def test_parse_proc_net_maps_tcp_states():
    """TCP 状态码按映射表转换，未知名回退 UNKNOWN(<hex>)。"""
    _, sampler = make_sampler()
    output = (
        "  sl  local_address rem_address   st ...\n"  # 首行恒为表头，被跳过
        "   0: 0100007F:0016 00000000:0000 01 00000000:00000000 00:00000000 00000000"
        "  1000 0 1 1 0 0 0\n"
        "   1: 0100007F:0016 00000000:0000 08 00000000:00000000 00:00000000 00000000"
        "  1000 0 1 1 0 0 0\n"
    )

    connections = sampler._parse_proc_net(output, "tcp")

    assert [c.state for c in connections] == ["ESTABLISHED", "CLOSE_WAIT"]
    assert all(c.uid == 1000 for c in connections)


def test_parse_proc_net_udp_state_forced_established():
    """UDP 无 TCP 状态码，实现固定按 "01" 处理（协议保持 udp）。"""
    _, sampler = make_sampler()

    connections = sampler._parse_proc_net(UDP_OUTPUT, "udp")

    assert len(connections) == 1
    assert connections[0].protocol == "udp"
    assert connections[0].state == "ESTABLISHED"
    assert (connections[0].local_addr, connections[0].local_port) == ("0.0.0.0", 53)


def test_parse_proc_net_skips_bad_rows():
    """非法端口（非十六进制）与非法 uid 的行整行跳过，正常行保留。"""
    _, sampler = make_sampler()
    output = (
        "  sl  local_address rem_address   st ...\n"  # 首行恒为表头，被跳过
        "   0: 0100007F:ZZZZ 00000000:0000 01 00000000:00000000 00:00000000 00000000"
        "  1000 0 1 1 0 0 0\n"
        "   1: 0100007F:0016 00000000:0000 01 00000000:00000000 00:00000000 00000000"
        "  notanint 0 1 1 0 0 0\n"
        "   2: 0100007F:0016 00000000:0000 01 00000000:00000000 00:00000000 00000000"
        "  1000 0 1 1 0 0 0\n"
    )

    connections = sampler._parse_proc_net(output, "tcp")

    assert len(connections) == 1
    assert (connections[0].local_addr, connections[0].local_port) == ("127.0.0.1", 22)


# ---------------------------------------------------------------------------
# _parse_address
# ---------------------------------------------------------------------------

def test_parse_address_ipv4_little_endian():
    """IPv4 为小端序十六进制，端口为大端序十六进制。"""
    _, sampler = make_sampler()

    assert sampler._parse_address("0100007F:0050") == ("127.0.0.1", 80)
    assert sampler._parse_address("5DB8D822:01BB") == ("34.216.184.93", 443)
    assert sampler._parse_address("00000000:0000") == ("0.0.0.0", 0)


def test_parse_address_ipv6_mapped():
    """IPv4 映射地址（内核形式 …FFFF0000+反转末段）→ ::ffff:x.x.x.x。"""
    _, sampler = make_sampler()

    assert sampler._parse_address("0000000000000000FFFF00000100007F:1F90") == (
        "::ffff:127.0.0.1",
        8080,
    )


def test_parse_address_ipv6_zero_address():
    """全零纯 IPv6（内核形式的监听地址）→ ::。"""
    _, sampler = make_sampler()

    assert sampler._parse_address("00000000000000000000000000000000:0050") == ("::", 80)


def test_parse_address_mapped_prefix_loose_match_falls_back_to_ipv6_str():
    """以映射前缀开头但第 8/9 字节非零的地址不是映射地址，回退为 IPv6 字符串。"""
    _, sampler = make_sampler()

    assert sampler._parse_address("0000000000000000FFFF123400000000:0050") == (
        "::3412:ffff:0:0",
        80,
    )


def test_parse_address_ipv6_pure_words_reversed():
    """纯 IPv6 与映射地址一致逐 32-bit 字反转：内核形式 ::1、fe80::1 正确解析。"""
    _, sampler = make_sampler()

    assert sampler._parse_address("00000000000000000000000001000000:0050") == ("::1", 80)
    assert sampler._parse_address("000080FE000000000000000001000000:01BB") == ("fe80::1", 443)


def test_parse_address_malformed_fallbacks():
    """地址格式异常：缺少冒号/字段过多 → 0.0.0.0:0；长度异常 IP → [hex] 形式。"""
    _, sampler = make_sampler()

    assert sampler._parse_address("0100007F") == ("0.0.0.0", 0)
    assert sampler._parse_address("A:B:C") == ("0.0.0.0", 0)
    assert sampler._parse_address("ABCD:12") == ("[ABCD]", 0x12)


# ---------------------------------------------------------------------------
# _get_wifi_status
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "output",
    [
        "Wi-Fi is enabled\nmWifiInfo SSID: MyNet, BSSID: aa:bb:cc:dd:ee:ff\n",
        "mWifiInfo SSID: MyNet, BSSID: aa:bb:cc:dd:ee:ff\nstate: CONNECTED\n",
    ],
)
async def test_wifi_status_connected_variants(output):
    """两种已连接判定（Wi-Fi is enabled / state: CONNECTED）都返回 (True, ssid)。"""
    adb, sampler = make_sampler(wifi=output)

    connected, ssid = await sampler._get_wifi_status(DEV)

    assert connected is True
    assert ssid == "MyNet"


@pytest.mark.parametrize(
    "output,expected_ssid",
    [
        ("mWifiInfo SSID: OldNet, BSSID: aa:bb:cc:dd:ee:ff\n", "OldNet"),  # 未连接但 SSID 在
        ("Wi-Fi is enabled\n", None),  # 无 SSID 字段
        ("mWifiInfo SSID:\n", None),  # 有 "SSID:" 但正则匹配不到值
    ],
)
async def test_wifi_status_disconnected_variants(output, expected_ssid):
    """未命中已连接条件时返回 (False, ssid 或 None)。"""
    adb, sampler = make_sampler(wifi=output)

    connected, ssid = await sampler._get_wifi_status(DEV)

    assert connected is False
    assert ssid == expected_ssid


async def test_wifi_status_adb_error_returns_false_none():
    """ADB 失败回退 (False, None)。"""
    adb, sampler = make_sampler()
    adb.fail_on.add("dumpsys wifi")

    result = await sampler._get_wifi_status(DEV)

    assert result == (False, None)


# ---------------------------------------------------------------------------
# stream_stats（生命周期 / 异常吞掉）
# ---------------------------------------------------------------------------

async def test_stream_stats_yields_snapshots():
    """周期产出快照；break 后生成器可正常关闭。"""
    _, sampler = make_sampler()

    results = []
    async for stats in sampler.stream_stats(DEV, interval=0):
        results.append(stats)
        if len(results) >= 3:
            break

    assert len(results) == 3
    assert all(s.rx_bytes == 500000 for s in results)
    assert results[0].rx_rate_kbps == 0.0  # 首轮无基准
    assert [s.ts for s in results] == sorted(s.ts for s in results)


async def test_stream_stats_continues_after_single_failure():
    """单轮采集异常被吞掉（记录日志），下一轮继续产出。"""

    class FlakySampler(NetworkSampler):
        def __init__(self, adb, device_id):
            super().__init__(adb, device_id)
            self.attempts = 0

        async def get_stats(self, device_id):
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError("模拟采集失败")
            return await super().get_stats(device_id)

    adb = FakeAdb(std_outputs())
    sampler = FlakySampler(adb, DEV)

    results = []
    async for stats in sampler.stream_stats(DEV, interval=0):
        results.append(stats)
        break

    assert len(results) == 1
    assert sampler.attempts == 2
    assert results[0].rx_bytes == 500000


async def test_stream_stats_can_be_closed_early():
    """消费方提前 aclose：不再产出、无残留异常。"""
    _, sampler = make_sampler()
    gen = sampler.stream_stats(DEV, interval=0)

    first = await anext(gen)
    await gen.aclose()

    assert first.rx_bytes == 500000
    with pytest.raises(StopAsyncIteration):
        await anext(gen)


async def test_stream_stats_interval_parameter_is_awaited(monkeypatch):
    """interval 被传给 sleep（用哨兵替换 asyncio.sleep 验证，不做真实等待）。"""
    _, sampler = make_sampler()
    slept: list[float] = []

    async def fake_sleep(delay: float) -> None:
        slept.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    gen = sampler.stream_stats(DEV, interval=2.5)
    await anext(gen)  # 第 1 轮产出后挂起，尚未 sleep
    await anext(gen)  # 触发 sleep(interval) 后再产出第 2 轮
    await gen.aclose()

    assert slept == [2.5]