"""
设备管理服务测试
==================

测试 DeviceService 的所有用例。

测试内容：
    - 基础设备查询（list_devices, get_device）
    - APK 安装（install_apk, batch_install）
    - 截图（screenshot）
    - TCP/IP 连接管理（connect_tcp, disconnect_tcp）
    - 网段扫描连接（scan_and_connect，方案 36）
    - 后台设备刷新（start, stop, _refresh_loop）
    - 事件回调机制（on_device_connected, on_device_disconnected）
"""

import asyncio
import io
import time
from ipaddress import ip_network

import pytest
from PIL import Image
from unittest.mock import AsyncMock, MagicMock

from app.application.device_service import (
    MAX_CONNECT,
    DeviceService,
    _SCAN_CONNECT_TIMEOUT,
)
from app.core.exceptions import AdbError, DeviceUnreachableError, ScanBusyError
from app.domain.device import DeviceInfo


# ---------------------------------------------------------------------------
# 测试 Fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_adb():
    """
    模拟 AdbDriver 协议。

    返回：
        MagicMock: 所有方法都是 AsyncMock 的 ADB 驱动模拟。
    """
    adb = MagicMock()
    adb.list_devices = AsyncMock(return_value=[])
    adb.list_device_states = AsyncMock(return_value={})
    adb.get_device_info = AsyncMock()
    adb.shell = AsyncMock(return_value="")
    adb.install = AsyncMock()
    adb.screenshot = AsyncMock(return_value=b"\x89PNG\r\n\x1a\n")
    adb.screenshot_raw_gzip = AsyncMock(return_value=b"\x1f\x8bGZIP")
    adb.get_rotation = AsyncMock(return_value=0)
    adb.connect_tcp = AsyncMock(return_value="192.168.1.5:5555")
    adb.disconnect_tcp = AsyncMock()
    adb.scan_hosts = AsyncMock(return_value=[])
    return adb


@pytest.fixture
def mock_repo():
    """
    模拟 DeviceRepository 协议。

    返回：
        MagicMock: 所有方法都是 AsyncMock 的设备仓库模拟。
    """
    repo = MagicMock()
    repo.save = AsyncMock()
    repo.get = AsyncMock(return_value=None)
    repo.list_all = AsyncMock(return_value=[])
    repo.delete = AsyncMock()
    return repo


@pytest.fixture
def service(mock_adb, mock_repo):
    """
    构建测试用 DeviceService。

    返回：
        DeviceService: 注入模拟依赖的服务实例。
    """
    return DeviceService(adb=mock_adb, repo=mock_repo)


def _make_device(device_id: str = "emulator-5554", model: str = "Pixel 6") -> DeviceInfo:
    """
    创建测试用 DeviceInfo。

    参数：
        device_id: 设备 ID。
        model: 设备型号。

    返回：
        DeviceInfo: 填充了测试数据的设备信息。
    """
    return DeviceInfo(
        id=device_id,
        model=model,
        os_version="13",
        resolution=(1080, 2400),
        battery=85,
        status="online",
    )


# ---------------------------------------------------------------------------
# 基础设备查询测试
# ---------------------------------------------------------------------------

class TestListDevices:
    """测试 list_devices 方法"""

    @pytest.mark.asyncio
    async def test_list_devices_returns_all(self, service, mock_adb, mock_repo):
        """测试列出所有连接的设备"""
        device1 = _make_device("device-1")
        device2 = _make_device("device-2")

        mock_adb.list_device_states.return_value = {"device-1": "device", "device-2": "device"}
        mock_adb.get_device_info.side_effect = [device1, device2]

        result = await service.list_devices()

        assert len(result) == 2
        assert result[0].id == "device-1"
        assert result[1].id == "device-2"

    @pytest.mark.asyncio
    async def test_list_devices_persists_each(self, service, mock_adb, mock_repo):
        """测试每个设备都被持久化到仓库"""
        device = _make_device("device-1")
        mock_adb.list_device_states.return_value = {"device-1": "device"}
        mock_adb.get_device_info.return_value = device

        await service.list_devices()

        mock_repo.save.assert_called_once_with(device)

    @pytest.mark.asyncio
    async def test_list_devices_attaches_alias(self, service, mock_adb, mock_repo, monkeypatch):
        """方案 50 T1：列表条目按 device_alias 单源填充备注（瞬态字段，入库仅 9 列不受影响）。"""
        device = _make_device("device-1")
        mock_adb.list_device_states.return_value = {"device-1": "device", "device-2": "device"}
        mock_adb.get_device_info.side_effect = [device, _make_device("device-2")]
        monkeypatch.setattr(
            "app.application.device_service.device_alias",
            lambda device_id: {"device-1": "主力机"}.get(device_id, ""),
        )

        result = await service.list_devices()

        assert result[0].alias == "主力机"
        assert result[1].alias == ""

    @pytest.mark.asyncio
    async def test_list_devices_empty(self, service, mock_adb):
        """测试没有连接设备时返回空列表"""
        mock_adb.list_device_states.return_value = {}

        result = await service.list_devices()

        assert result == []

    @pytest.mark.asyncio
    async def test_list_devices_runs_concurrently(self, service, mock_adb):
        """3 台设备并发查询（方案 35 D1）——串行实现峰值恒为 1 且总时长 ~3×0.3s"""
        in_flight = 0
        peak = 0

        async def fake_get_device_info(device_id):
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.3)
            in_flight -= 1
            return _make_device(device_id)

        mock_adb.list_device_states.return_value = {"d1": "device", "d2": "device", "d3": "device"}
        mock_adb.get_device_info.side_effect = fake_get_device_info

        started = time.monotonic()
        result = await service.list_devices()
        elapsed = time.monotonic() - started

        assert [d.id for d in result] == ["d1", "d2", "d3"]
        assert peak >= 2
        assert elapsed < 0.6
        assert all(d.slow is False for d in result)

    @pytest.mark.asyncio
    async def test_list_devices_keeps_broken_device_without_cache(self, service, mock_adb, mock_repo):
        """信息获取失败的设备保留在列表中（最小信息 + slow 标记），不落库（方案 35 D3/D7）"""
        good = _make_device("device-good")
        mock_adb.list_device_states.return_value = {"device-good": "device", "device-broken": "device"}
        mock_adb.get_device_info.side_effect = [good, AdbError("device offline")]

        result = await service.list_devices()

        assert [d.id for d in result] == ["device-good", "device-broken"]
        broken = result[1]
        assert broken.model == ""
        assert broken.resolution == (0, 0)
        assert broken.battery == 0
        assert broken.status == "online"
        assert broken.slow is True
        assert result[0].slow is False
        mock_repo.save.assert_called_once_with(good)

    @pytest.mark.asyncio
    async def test_list_devices_keeps_broken_device_with_cache(self, service, mock_adb, mock_repo):
        """有缓存的失败设备：返回缓存内容（逐字段一致）但 slow 标记，且不覆写仓库"""
        cached = _make_device("device-broken", model="Cached Model")
        mock_adb.list_device_states.return_value = {"device-broken": "device"}
        mock_adb.get_device_info.side_effect = AdbError("timeout")
        mock_repo.get.return_value = cached

        result = await service.list_devices()

        assert len(result) == 1
        broken = result[0]
        assert broken.id == cached.id
        assert broken.model == cached.model
        assert broken.os_version == cached.os_version
        assert broken.resolution == cached.resolution
        assert broken.battery == cached.battery
        assert broken.status == cached.status
        assert broken.slow is True
        mock_repo.save.assert_not_called()

    @pytest.mark.asyncio
    async def test_list_devices_cooldown_after_failure(self, service, mock_adb, monkeypatch):
        """失败后 30s 冷却（方案 35 D4）：期间不发起查询，到期恢复重试"""
        class FakeTime:
            now = 1000.0

            @staticmethod
            def monotonic():
                return FakeTime.now

        monkeypatch.setattr("app.application.device_service.time", FakeTime)
        mock_adb.list_device_states.return_value = {"device-flaky": "device"}
        mock_adb.get_device_info.side_effect = AdbError("slow device")

        # 第一次：查询失败 → 兜底 + slow
        result1 = await service.list_devices()
        assert result1[0].slow is True
        assert mock_adb.get_device_info.call_count == 1

        # 冷却期内：不再调用查询
        result2 = await service.list_devices()
        assert result2[0].slow is True
        assert mock_adb.get_device_info.call_count == 1

        # 推进 30s：恢复重试（这次成功 → 清冷却、slow 消失）
        FakeTime.now += 31
        good = _make_device("device-flaky")
        mock_adb.get_device_info.side_effect = None
        mock_adb.get_device_info.return_value = good
        result3 = await service.list_devices()
        assert result3[0].slow is False
        assert mock_adb.get_device_info.call_count == 2

        # 成功后冷却已清：下次列表刷新立即重查（无 30s 等待）
        await service.list_devices()
        assert mock_adb.get_device_info.call_count == 3

    @pytest.mark.asyncio
    async def test_list_devices_reuses_inflight_task(self, service, mock_adb):
        """并发两个 list_devices：同设备查询只发起一次（方案 35 D5 任务复用）"""
        async def slow_get_device_info(device_id):
            await asyncio.sleep(0.1)
            return _make_device(device_id)

        mock_adb.list_device_states.return_value = {"d1": "device", "d2": "device"}
        mock_adb.get_device_info.side_effect = slow_get_device_info

        r1, r2 = await asyncio.gather(service.list_devices(), service.list_devices())

        assert mock_adb.get_device_info.call_count == 2  # 复用而非 2×2
        assert sorted(d.id for d in r1) == ["d1", "d2"]
        assert sorted(d.id for d in r2) == ["d1", "d2"]


class TestGetDevice:
    """测试 get_device 方法"""

    @pytest.mark.asyncio
    async def test_get_device_found(self, service, mock_repo):
        """测试找到设备时返回 DeviceInfo"""
        device = _make_device()
        mock_repo.get.return_value = device

        result = await service.get_device("emulator-5554")

        assert result == device
        mock_repo.get.assert_called_once_with("emulator-5554")

    @pytest.mark.asyncio
    async def test_get_device_attaches_alias(self, service, mock_repo, monkeypatch):
        """方案 50 T1：详情读取同样按 device_alias 单源填充备注。"""
        device = _make_device()
        mock_repo.get.return_value = device
        monkeypatch.setattr(
            "app.application.device_service.device_alias", lambda device_id: "备用机"
        )

        result = await service.get_device("emulator-5554")

        assert result is not None
        assert result.alias == "备用机"

    @pytest.mark.asyncio
    async def test_get_device_not_found(self, service, mock_repo):
        """测试设备不存在时返回 None"""
        mock_repo.get.return_value = None

        result = await service.get_device("nonexistent")

        assert result is None


# ---------------------------------------------------------------------------
# APK 安装测试
# ---------------------------------------------------------------------------

class TestInstallApk:
    """测试 install_apk 方法"""

    @pytest.mark.asyncio
    async def test_install_success(self, service, mock_adb):
        """测试成功安装 APK"""
        result = await service.install_apk("emulator-5554", "/path/to/app.apk")

        mock_adb.install.assert_called_once_with("emulator-5554", "/path/to/app.apk")
        assert "emulator-5554" in result
        assert "installed" in result.lower()

    @pytest.mark.asyncio
    async def test_install_failure_propagates(self, service, mock_adb):
        """测试安装失败时异常被传播"""
        mock_adb.install.side_effect = AdbError("INSTALL_FAILED")

        with pytest.raises(AdbError, match="INSTALL_FAILED"):
            await service.install_apk("emulator-5554", "/path/to/app.apk")


class TestBatchInstall:
    """测试 batch_install 方法"""

    @pytest.mark.asyncio
    async def test_batch_install_all_success(self, service, mock_adb):
        """测试所有设备安装成功"""
        result = await service.batch_install(
            ["device-1", "device-2", "device-3"],
            "/path/to/app.apk"
        )

        assert len(result) == 3
        assert all("success" in r for r in result)
        assert mock_adb.install.call_count == 3

    @pytest.mark.asyncio
    async def test_batch_install_partial_failure(self, service, mock_adb):
        """测试部分设备安装失败不影响其他"""
        async def install_side_effect(device_id, apk_path):
            if device_id == "device-2":
                raise AdbError("INSTALL_FAILED")

        mock_adb.install.side_effect = install_side_effect

        result = await service.batch_install(
            ["device-1", "device-2", "device-3"],
            "/path/to/app.apk"
        )

        assert len(result) == 3
        assert "success" in result[0]
        assert "failed" in result[1]
        assert "success" in result[2]

    @pytest.mark.asyncio
    async def test_batch_install_empty_list(self, service):
        """测试空设备列表"""
        result = await service.batch_install([], "/path/to/app.apk")
        assert result == []


# ---------------------------------------------------------------------------
# 截图测试
# ---------------------------------------------------------------------------

class TestScreenshot:
    """测试 screenshot 方法"""

    @pytest.mark.asyncio
    async def test_screenshot_delegates_to_adb(self, service, mock_adb):
        """测试截图委托给 ADB 驱动"""
        mock_adb.screenshot.return_value = b"\x89PNG\r\n\x1a\nfake_png_data"

        result = await service.screenshot("emulator-5554")

        mock_adb.screenshot.assert_called_once_with("emulator-5554")
        assert result == b"\x89PNG\r\n\x1a\nfake_png_data"

    @pytest.mark.asyncio
    async def test_screenshot_raw_gzip_delegates_to_adb(self, service, mock_adb):
        """测试 raw+gzip 截图委托给 ADB 驱动（方案 29）"""
        mock_adb.screenshot_raw_gzip.return_value = b"GZIP_FRAME"

        result = await service.screenshot_raw_gzip("emulator-5554")

        mock_adb.screenshot_raw_gzip.assert_called_once_with("emulator-5554")
        assert result == b"GZIP_FRAME"

    @pytest.mark.asyncio
    async def test_screenshot_applies_rotation_180(self, service, mock_adb):
        """rotation=2 → PNG 经 180° 补偿（方案 38 B4）：行主序像素全反序。"""
        png, src_pixels = _make_png_2x2()
        mock_adb.screenshot.return_value = png
        mock_adb.get_rotation = AsyncMock(return_value=2)

        result = await service.screenshot("emulator-5554")

        with Image.open(io.BytesIO(result)) as img:
            img.load()
            assert list(img.getdata()) == src_pixels[::-1]


def _make_png_2x2() -> tuple[bytes, list[tuple[int, int, int]]]:
    """生成 2×2 逐像素异色 PNG 与行主序像素列表（rotation 测试锚点）。"""
    img = Image.new("RGB", (2, 2))
    px = img.load()
    for x in range(2):
        for y in range(2):
            px[x, y] = (x * 200 + 10, y * 200 + 20, 40)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue(), [px[x, y] for y in range(2) for x in range(2)]


# ---------------------------------------------------------------------------
# rotation 查询与 TTL 缓存测试（方案 38 B3）
# ---------------------------------------------------------------------------

class TestRotationCache:
    """测试 get_rotation 方法"""

    @pytest.mark.asyncio
    async def test_ttl_cache_reuses_within_window(self, service, mock_adb):
        """TTL 内二次调用复用缓存，adb 只查一次。"""
        mock_adb.get_rotation = AsyncMock(return_value=2)

        assert await service.get_rotation("emulator-5554") == 2
        assert await service.get_rotation("emulator-5554") == 2

        mock_adb.get_rotation.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_ttl_expires_then_requeries(self, service, mock_adb, monkeypatch):
        """TTL 过期后重查 adb（monkeypatch 时钟前推 31s）。"""
        mock_adb.get_rotation = AsyncMock(return_value=2)
        now = [0.0]
        monkeypatch.setattr(
            "app.application.device_service.time.monotonic", lambda: now[0]
        )

        assert await service.get_rotation("emulator-5554") == 2
        now[0] = 31.0
        assert await service.get_rotation("emulator-5554") == 2

        assert mock_adb.get_rotation.await_count == 2

    @pytest.mark.asyncio
    async def test_failure_cached_as_zero(self, service, mock_adb):
        """查询失败 → 返回 0 且同 TTL 缓存（不重试风暴）。"""
        mock_adb.get_rotation = AsyncMock(side_effect=AdbError("dumpsys timeout"))

        assert await service.get_rotation("emulator-5554") == 0
        assert await service.get_rotation("emulator-5554") == 0

        mock_adb.get_rotation.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reconnect_requeries_rotation_after_failure(self, service, mock_adb, mock_repo):
        """方案 41：断开瞬间失败缓存的 0 不得污染重连后的判据。

        行为链：连接 I 的 dumpsys 失败 → 0 入缓存（.24 断开竞态）→
        断开 + 重连（新生命周期）→ 首截图真查得 2 → 施加 180° 补偿。
        """
        png, src_pixels = _make_png_2x2()
        mock_adb.get_rotation = AsyncMock(side_effect=AdbError("device gone"))
        assert await service.get_rotation("192.168.1.5:5555") == 0
        assert "192.168.1.5:5555" in service._rotation_cache

        await service.disconnect_tcp("192.168.1.5", 5555)
        mock_adb.get_device_info.return_value = _make_device("192.168.1.5:5555")
        await service.connect_tcp("192.168.1.5", 5555)

        mock_adb.get_rotation = AsyncMock(return_value=2)
        mock_adb.screenshot.return_value = png
        result = await service.screenshot("192.168.1.5:5555")

        with Image.open(io.BytesIO(result)) as img:
            img.load()
            assert list(img.getdata()) == src_pixels[::-1]


# ---------------------------------------------------------------------------
# TCP/IP 连接测试
# ---------------------------------------------------------------------------

class TestConnectTcp:
    """测试 connect_tcp 方法"""

    @pytest.mark.asyncio
    async def test_connect_success(self, service, mock_adb, mock_repo):
        """测试成功通过 TCP/IP 连接"""
        device = _make_device("192.168.1.5:5555")
        mock_adb.connect_tcp.return_value = "192.168.1.5:5555"
        mock_adb.get_device_info.return_value = device

        result = await service.connect_tcp("192.168.1.5", 5555)

        assert result == "192.168.1.5:5555"
        mock_adb.connect_tcp.assert_called_once_with("192.168.1.5", 5555, timeout=None)
        mock_adb.get_device_info.assert_called_once_with("192.168.1.5:5555")
        mock_repo.save.assert_called_once_with(device)

    @pytest.mark.asyncio
    async def test_connect_failure_propagates(self, service, mock_adb):
        """测试连接失败时异常被传播"""
        mock_adb.connect_tcp.side_effect = AdbError("Connection refused")

        with pytest.raises(AdbError, match="Connection refused"):
            await service.connect_tcp("192.168.1.5", 5555)

    @pytest.mark.asyncio
    async def test_connect_tolerates_info_failure(self, service, mock_adb, mock_repo):
        """adb connect 成功但信息查询失败：仍返回 device_id、不落库、不上抛（方案 35）"""
        mock_adb.connect_tcp.return_value = "192.168.1.5:5555"
        mock_adb.get_device_info.side_effect = AdbError("ADB command timed out after 5s")

        result = await service.connect_tcp("192.168.1.5", 5555)

        assert result == "192.168.1.5:5555"
        mock_repo.save.assert_not_called()

    @pytest.mark.asyncio
    async def test_connect_clears_rotation_cache(self, service, mock_adb):
        """方案 41：连接成功即清 rotation 缓存（新生命周期首查必真查）。"""
        service._rotation_cache["192.168.1.5:5555"] = (0, time.monotonic())
        mock_adb.get_device_info.return_value = _make_device("192.168.1.5:5555")

        await service.connect_tcp("192.168.1.5", 5555)

        assert "192.168.1.5:5555" not in service._rotation_cache


class TestDisconnectTcp:
    """测试 disconnect_tcp 方法"""

    @pytest.mark.asyncio
    async def test_disconnect_removes_from_repo(self, service, mock_adb, mock_repo):
        """测试断开连接后从仓库删除设备"""
        await service.disconnect_tcp("192.168.1.5", 5555)

        mock_adb.disconnect_tcp.assert_called_once_with("192.168.1.5", 5555)
        mock_repo.delete.assert_called_once_with("192.168.1.5:5555")

    @pytest.mark.asyncio
    async def test_disconnect_clears_query_cache(self, service, mock_adb, mock_repo):
        """断开时清理该设备的冷却与任务表条目（方案 35 D4/D5）"""
        service._info_cooldown["192.168.1.5:5555"] = 12345.0
        service._info_tasks["192.168.1.5:5555"] = None  # 占位条目

        await service.disconnect_tcp("192.168.1.5", 5555)

        assert "192.168.1.5:5555" not in service._info_cooldown
        assert "192.168.1.5:5555" not in service._info_tasks

    @pytest.mark.asyncio
    async def test_disconnect_clears_rotation_cache(self, service, mock_adb, mock_repo):
        """方案 41：断开即清 rotation 缓存（失败收敛值不跨生命周期存活）。"""
        service._rotation_cache["192.168.1.5:5555"] = (0, time.monotonic())

        await service.disconnect_tcp("192.168.1.5", 5555)

        assert "192.168.1.5:5555" not in service._rotation_cache

    @pytest.mark.asyncio
    async def test_disconnect_cancels_inflight_fetch_info(self, service, mock_adb, mock_repo):
        """方案 51 A02：断开须 cancel 在飞 _fetch_info——晚到 save 不得复活已删行。"""
        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_info(device_id):
            started.set()
            await release.wait()
            return _make_device(device_id)

        mock_adb.get_device_info = AsyncMock(side_effect=slow_info)
        waiter = asyncio.create_task(service._get_info_cached("192.168.1.5:5555"))
        await started.wait()

        await service.disconnect_tcp("192.168.1.5", 5555)
        release.set()
        await asyncio.sleep(0.05)  # 给在飞任务响应 cancel 的窗口

        mock_repo.save.assert_not_called()
        # 等待者不被误取消：内务任务被回收时回落 None 走兜底
        assert await asyncio.wait_for(waiter, timeout=2.0) is None

    @pytest.mark.asyncio
    async def test_disconnect_waiter_not_cancelled_by_reclaim(self, service, mock_adb, mock_repo):
        """方案 51 A02 对偶：cancel 的是内务任务而非等待者本身——
        并发 list 的 await 应拿到 None 兜底而非 CancelledError 炸穿请求。"""
        started = asyncio.Event()

        async def slow_info(device_id):
            started.set()
            await asyncio.Event().wait()  # 永不自然完成，只能被 cancel
            return _make_device(device_id)

        mock_adb.get_device_info = AsyncMock(side_effect=slow_info)
        waiter = asyncio.create_task(service._get_info_cached("192.168.1.5:5555"))
        await started.wait()

        await service.disconnect_tcp("192.168.1.5", 5555)

        # 等待者存活且拿到 None：若被 cancel 误伤，wait_for 会抛 CancelledError
        result = await asyncio.wait_for(waiter, timeout=2.0)
        assert result is None


# ---------------------------------------------------------------------------
# 网段扫描连接测试（方案 36）
# ---------------------------------------------------------------------------

def _scan_args(cidr: str) -> tuple[str, list[str]]:
    """CIDR → (label, targets)：label 取归一化网段字符串，targets 数值升序展开。"""
    network = ip_network(cidr)
    return str(network), [str(ip) for ip in network.hosts()]


class TestScanAndConnect:
    """测试 scan_and_connect 编排（D2）"""

    @pytest.mark.asyncio
    async def test_scan_classifies_connect_results(self, service, mock_adb):
        """4 成功 / 2 不可达 / 2 其余失败 → ok=4、unreachable=2、connect_failed=2。"""
        hosts = [f"192.168.8.{i}" for i in range(1, 9)]
        mock_adb.scan_hosts.return_value = hosts
        mock_adb.get_device_info.side_effect = lambda device_id: _make_device(device_id)
        mock_adb.connect_tcp.side_effect = [
            "192.168.8.1:5555",
            "192.168.8.2:5555",
            "192.168.8.3:5555",
            "192.168.8.4:5555",
            DeviceUnreachableError("192.168.8.5", 5555, "连接超时"),
            DeviceUnreachableError("192.168.8.6", 5555, "连接被拒绝"),
            AdbError("failed to connect to 192.168.8.7:5555"),
            AdbError("ADB command timed out after 5s"),
        ]

        result = await service.scan_and_connect(*_scan_args("192.168.8.0/28"))

        assert result.label == "192.168.8.0/28"
        assert result.probed == 14
        assert result.open_hosts == hosts
        assert result.truncated is False

        oks = [r for r in result.connect_results if r.ok]
        unreachable = [r for r in result.connect_results if r.reason == "unreachable"]
        failed = [r for r in result.connect_results if r.reason == "connect_failed"]
        assert len(oks) == 4
        assert len(unreachable) == 2
        assert len(failed) == 2
        assert all(r.device_id == f"{r.ip}:5555" for r in oks)
        assert all(r.message for r in unreachable + failed)

    @pytest.mark.asyncio
    async def test_scan_truncates_at_max_connect(self, service, mock_adb):
        """开放主机超过 MAX_CONNECT：仅连前 64 台，truncated=True，open_hosts 保留全量。"""
        assert MAX_CONNECT == 64
        hosts = [f"192.168.8.{i}" for i in range(1, 66)]  # 65 台
        mock_adb.scan_hosts.return_value = hosts
        mock_adb.get_device_info.side_effect = lambda device_id: _make_device(device_id)

        result = await service.scan_and_connect(*_scan_args("192.168.8.0/25"))

        assert result.truncated is True
        assert result.open_hosts == hosts
        assert len(result.connect_results) == MAX_CONNECT
        assert mock_adb.connect_tcp.await_count == MAX_CONNECT
        assert [r.ip for r in result.connect_results] == hosts[:MAX_CONNECT]

    @pytest.mark.asyncio
    async def test_scan_busy_then_recovers(self, service, mock_adb):
        """互斥：扫描进行中重复请求抛 ScanBusyError；结束后可再次扫描（finally 复位）。"""
        release = asyncio.Event()

        async def fake_scan(hosts, port=5555, timeout=None, concurrency=None):
            await release.wait()
            return []

        mock_adb.scan_hosts.side_effect = fake_scan

        first = asyncio.create_task(service.scan_and_connect(*_scan_args("192.168.8.0/29")))
        await asyncio.sleep(0)  # 首个任务进入并置位 _scan_inflight
        with pytest.raises(ScanBusyError):
            await service.scan_and_connect(*_scan_args("192.168.8.0/29"))

        release.set()
        result = await first
        assert result.open_hosts == []

        result2 = await service.scan_and_connect(*_scan_args("192.168.8.0/29"))
        assert result2.open_hosts == []

    @pytest.mark.asyncio
    async def test_scan_connect_false_skips_connect(self, service, mock_adb):
        """connect=False：仅探测返回，connect_tcp 零调用、不截断。"""
        mock_adb.scan_hosts.return_value = ["192.168.8.1", "192.168.8.2", "192.168.8.3"]

        result = await service.scan_and_connect(*_scan_args("192.168.8.0/29"), connect=False)

        assert result.open_hosts == ["192.168.8.1", "192.168.8.2", "192.168.8.3"]
        assert result.connect_results == []
        assert result.truncated is False
        mock_adb.connect_tcp.assert_not_called()

    @pytest.mark.asyncio
    async def test_scan_connect_timeout_passthrough(self, service, mock_adb):
        """连接阶段 adb connect 全部带短超时（_SCAN_CONNECT_TIMEOUT=5），防拖满 30s。"""
        assert _SCAN_CONNECT_TIMEOUT == 5
        mock_adb.scan_hosts.return_value = ["192.168.8.1", "192.168.8.2"]
        mock_adb.get_device_info.side_effect = lambda device_id: _make_device(device_id)

        await service.scan_and_connect(*_scan_args("192.168.8.0/29"))

        assert mock_adb.connect_tcp.await_count == 2
        for c in mock_adb.connect_tcp.await_args_list:
            assert c.kwargs["timeout"] == _SCAN_CONNECT_TIMEOUT

    @pytest.mark.asyncio
    async def test_scan_stage2_failures_do_not_upset(self, service, mock_adb):
        """连接阶段全部抛异常：仍正常返回，逐台 connect_failed（return_exceptions 语义）。"""
        mock_adb.scan_hosts.return_value = ["192.168.8.1", "192.168.8.2"]
        mock_adb.connect_tcp.side_effect = AdbError("boom")

        result = await service.scan_and_connect(*_scan_args("192.168.8.0/29"))

        assert len(result.connect_results) == 2
        assert all(
            (not r.ok) and r.reason == "connect_failed" for r in result.connect_results
        )

    @pytest.mark.asyncio
    async def test_scan_probed_boundary_prefixes(self, service, mock_adb):
        """/22 上界 1022 台、/32 退化 1 台：probed 与 hosts() 语义一致（终评 M-7/A-5）。"""
        mock_adb.scan_hosts.return_value = []

        r22 = await service.scan_and_connect(*_scan_args("192.168.8.0/22"), connect=False)
        r32 = await service.scan_and_connect(*_scan_args("192.168.8.5/32"), connect=False)

        assert r22.probed == 1022
        assert r32.probed == 1

    @pytest.mark.asyncio
    async def test_scan_message_truncated_to_200(self, service, mock_adb):
        """connect_failed 的 message 截断 200 字符（终评 M-8）：长 adb 输出不入契约。"""
        mock_adb.scan_hosts.return_value = ["192.168.8.1"]
        mock_adb.connect_tcp.side_effect = AdbError("x" * 300)

        result = await service.scan_and_connect(*_scan_args("192.168.8.0/24"))

        attempt = result.connect_results[0]
        assert attempt.reason == "connect_failed"
        assert attempt.message == "x" * 200

    @pytest.mark.asyncio
    async def test_scan_dropped_attempt_logs_warning(self, service, mock_adb, monkeypatch):
        """BaseException 穿透 connect_one 被 gather 过滤：留一条 warning 痕迹（终评 M-5）。"""

        class _Boom(BaseException):
            pass

        warnings: list[str] = []

        class _FakeLogger:
            def warning(self, event: str, **kw: object) -> None:
                warnings.append(event)

            def info(self, event: str, **kw: object) -> None:
                pass

            def error(self, event: str, **kw: object) -> None:
                pass

            def debug(self, event: str, **kw: object) -> None:
                pass

        monkeypatch.setattr("app.application.device_service.logger", _FakeLogger())
        mock_adb.scan_hosts.return_value = ["192.168.8.1"]
        mock_adb.connect_tcp.side_effect = _Boom()

        result = await service.scan_and_connect(*_scan_args("192.168.8.0/24"))

        assert result.connect_results == []
        assert warnings.count("scan_attempt_dropped") == 1


# ---------------------------------------------------------------------------
# 后台刷新测试
# ---------------------------------------------------------------------------

class TestStartStop:
    """测试 start/stop 方法"""

    @pytest.mark.asyncio
    async def test_start_creates_task(self, service):
        """测试启动创建后台任务"""
        await service.start()

        assert service._refresh_task is not None
        assert not service._refresh_task.done()

        # 清理
        await service.stop()

    @pytest.mark.asyncio
    async def test_start_idempotent(self, service):
        """测试重复启动不会创建多个任务"""
        await service.start()
        first_task = service._refresh_task

        await service.start()  # 第二次调用应该是 no-op
        assert service._refresh_task is first_task

        await service.stop()

    @pytest.mark.asyncio
    async def test_stop_cancels_task(self, service):
        """测试停止取消后台任务"""
        await service.start()
        task = service._refresh_task

        await service.stop()

        assert task.cancelled() or task.done()
        assert service._refresh_task is None

    @pytest.mark.asyncio
    async def test_stop_when_not_running(self, service):
        """测试未启动时停止是安全的"""
        await service.stop()  # 不应抛异常
        assert service._refresh_task is None


# ---------------------------------------------------------------------------
# 刷新循环测试
# ---------------------------------------------------------------------------

class TestRefreshLoop:
    """测试 _refresh_loop 方法"""

    @pytest.mark.asyncio
    async def test_detects_new_device(self, service, mock_adb, mock_repo):
        """测试检测到新连接的设备"""
        device = _make_device("new-device")

        # 第一次调用返回新设备，第二次抛 CancelledError 退出循环
        call_count = 0

        async def list_device_states_side_effect():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"new-device": "device"}
            # 第二次循环前等待时会被取消
            await asyncio.sleep(100)
            return {}

        mock_adb.list_device_states.side_effect = list_device_states_side_effect
        mock_adb.get_device_info.return_value = device

        # 手动运行一次刷新
        # 模拟 _refresh_interval 非常短
        service._refresh_interval = 0.01
        task = asyncio.create_task(service._refresh_loop())

        # 等待足够时间让第一次刷新完成
        await asyncio.sleep(0.05)

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        mock_repo.save.assert_called()

    @pytest.mark.asyncio
    async def test_detects_disconnected_device(self, service, mock_adb, mock_repo):
        """测试检测到断开的设备"""
        # 初始状态有一个设备
        service._previous_states = {"old-device": "device"}

        # 下次刷新时设备消失
        mock_adb.list_device_states.return_value = {}

        service._refresh_interval = 0.01
        task = asyncio.create_task(service._refresh_loop())

        await asyncio.sleep(0.05)

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        mock_repo.delete.assert_called_with("old-device")

    @pytest.mark.asyncio
    async def test_refresh_error_does_not_stop_loop(self, service, mock_adb):
        """测试单次刷新失败不会停止整个循环"""
        call_count = 0

        async def list_device_states_side_effect():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("ADB crashed")
            elif call_count == 2:
                # 第二次循环前等待时会被取消
                await asyncio.sleep(100)
            return {}

        mock_adb.list_device_states.side_effect = list_device_states_side_effect

        service._refresh_interval = 0.01
        task = asyncio.create_task(service._refresh_loop())

        await asyncio.sleep(0.05)

        # 循环应该仍在运行（尽管第一次失败了）
        assert not task.done()

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


# ---------------------------------------------------------------------------
# 事件回调测试
# ---------------------------------------------------------------------------

class TestEventCallbacks:
    """测试设备事件回调机制"""

    @pytest.mark.asyncio
    async def test_register_connected_callback(self, service):
        """测试注册设备连接回调"""
        callback = AsyncMock()

        service.on_device_connected(callback)

        assert callback in service._on_device_connected

    @pytest.mark.asyncio
    async def test_register_disconnected_callback(self, service):
        """测试注册设备断开回调"""
        callback = AsyncMock()

        service.on_device_disconnected(callback)

        assert callback in service._on_device_disconnected

    @pytest.mark.asyncio
    async def test_trigger_connected_callbacks(self, service):
        """测试触发所有设备连接回调"""
        callback1 = AsyncMock()
        callback2 = AsyncMock()
        service.on_device_connected(callback1)
        service.on_device_connected(callback2)

        device = _make_device()
        await service._trigger_connected_callbacks(device)

        callback1.assert_called_once_with(device)
        callback2.assert_called_once_with(device)

    @pytest.mark.asyncio
    async def test_trigger_disconnected_callbacks(self, service):
        """测试触发所有设备断开回调"""
        callback1 = AsyncMock()
        callback2 = AsyncMock()
        service.on_device_disconnected(callback1)
        service.on_device_disconnected(callback2)

        await service._trigger_disconnected_callbacks("emulator-5554")

        callback1.assert_called_once_with("emulator-5554")
        callback2.assert_called_once_with("emulator-5554")

    @pytest.mark.asyncio
    async def test_callback_error_does_not_propagate(self, service):
        """测试单个回调失败不影响其他回调"""
        failing_callback = AsyncMock(side_effect=RuntimeError("oops"))
        good_callback = AsyncMock()

        service.on_device_connected(failing_callback)
        service.on_device_connected(good_callback)

        device = _make_device()
        await service._trigger_connected_callbacks(device)

        # 即使第一个失败，第二个也应该被调用
        good_callback.assert_called_once_with(device)

    @pytest.mark.asyncio
    async def test_new_device_triggers_callback(self, service, mock_adb, mock_repo):
        """测试刷新循环中新设备触发连接回调"""
        device = _make_device("new-device")
        callback = AsyncMock()
        service.on_device_connected(callback)

        # 第一次返回新设备，后续挂起
        call_count = 0

        async def list_device_states_side_effect():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"new-device": "device"}
            await asyncio.sleep(100)
            return {}

        mock_adb.list_device_states.side_effect = list_device_states_side_effect
        mock_adb.get_device_info.return_value = device

        service._refresh_interval = 0.01
        task = asyncio.create_task(service._refresh_loop())

        await asyncio.sleep(0.05)

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        callback.assert_called()

    @pytest.mark.asyncio
    async def test_disconnected_device_triggers_callback(self, service, mock_adb, mock_repo):
        """测试刷新循环中断开设备触发断开回调"""
        callback = AsyncMock()
        service.on_device_disconnected(callback)

        # 初始有一个设备
        service._previous_states = {"old-device": "device"}
        mock_adb.list_device_states.return_value = {}

        service._refresh_interval = 0.01
        task = asyncio.create_task(service._refresh_loop())

        await asyncio.sleep(0.05)

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        callback.assert_called_with("old-device")


# ---------------------------------------------------------------------------
# 失联设备可见性（路线图 B5：offline/unauthorized 不再静默）
# ---------------------------------------------------------------------------

class TestUnreachableVisibility:
    """非 device 态行的列表呈现与事件播报。"""

    @pytest.mark.asyncio
    async def test_list_devices_includes_offline_and_unauthorized_rows(
        self, service, mock_adb, mock_repo
    ):
        """adb 表中的 offline/unauthorized 行以独立状态入列，不再消失"""
        mock_adb.list_device_states.return_value = {
            "dev-online": "device",
            "dev-off": "offline",
            "dev-auth": "unauthorized",
        }
        mock_adb.get_device_info.return_value = _make_device("dev-online")

        devices = await service.list_devices()

        by_id = {d.id: d for d in devices}
        assert by_id["dev-online"].status == "online"
        assert by_id["dev-off"].status == "offline"
        assert by_id["dev-auth"].status == "unauthorized"
        # 失联行不查设备信息（查了也拿不到），也不落库
        mock_adb.get_device_info.assert_called_once_with("dev-online")
        mock_repo.save.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_list_devices_offline_prefers_cache(self, service, mock_adb, mock_repo):
        """offline 行有缓存时带出缓存型号，仅状态覆写为失联态"""
        mock_adb.list_device_states.return_value = {"dev-off": "offline"}
        mock_repo.get.return_value = _make_device("dev-off", model="Pixel 7")

        devices = await service.list_devices()

        assert len(devices) == 1
        assert devices[0].model == "Pixel 7"
        assert devices[0].status == "offline"
        mock_repo.save.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_device_to_offline_triggers_unreachable_not_disconnect(
        self, service, mock_adb, mock_repo
    ):
        """在线→offline：播 unreachable、保缓存、不播 disconnected"""
        disconnected_cb = AsyncMock()
        unreachable_cb = AsyncMock()
        service.on_device_disconnected(disconnected_cb)
        service.on_device_unreachable(unreachable_cb)
        service._previous_states = {"dev1": "device"}
        mock_adb.list_device_states.return_value = {"dev1": "offline"}

        service._refresh_interval = 0.01
        task = asyncio.create_task(service._refresh_loop())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        unreachable_cb.assert_called_once_with("dev1", "offline")
        disconnected_cb.assert_not_called()
        mock_repo.delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_offline_to_device_triggers_connected(self, service, mock_adb, mock_repo):
        """offline→device（恢复上线）：刷新信息并播 connected"""
        connected_cb = AsyncMock()
        service.on_device_connected(connected_cb)
        device = _make_device("dev1")
        service._previous_states = {"dev1": "offline"}
        mock_adb.list_device_states.return_value = {"dev1": "device"}
        mock_adb.get_device_info.return_value = device

        service._refresh_interval = 0.01
        task = asyncio.create_task(service._refresh_loop())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        connected_cb.assert_called_once()
        assert connected_cb.call_args[0][0].id == "dev1"

    @pytest.mark.asyncio
    async def test_new_offline_serial_announces_unreachable(
        self, service, mock_adb, mock_repo
    ):
        """首次出现即 offline 的序列号：播 unreachable，不查信息不落库"""
        unreachable_cb = AsyncMock()
        connected_cb = AsyncMock()
        service.on_device_unreachable(unreachable_cb)
        service.on_device_connected(connected_cb)
        mock_adb.list_device_states.return_value = {"dev-new-off": "unauthorized"}

        service._refresh_interval = 0.01
        task = asyncio.create_task(service._refresh_loop())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        unreachable_cb.assert_called_once_with("dev-new-off", "unauthorized")
        connected_cb.assert_not_called()
        mock_repo.save.assert_not_awaited()

    def test_register_and_unregister_unreachable(self, service):
        """unreachable 回调注册与公开注销 API（L7：接口层不再触碰私有列表）"""
        cb = AsyncMock()
        service.on_device_unreachable(cb)
        assert cb in service._on_device_unreachable

        service.off_device_unreachable(cb)
        assert cb not in service._on_device_unreachable

        # 注销不存在的回调不抛
        service.off_device_unreachable(cb)
        service.off_device_connected(cb)
        service.off_device_disconnected(cb)

    def test_sse_client_count(self, service):
        """连接计数 = connected 回调注册数（SSE 每连接注册一对）"""
        assert service.sse_client_count() == 0
        cb1 = AsyncMock()
        cb2 = AsyncMock()
        service.on_device_connected(cb1)
        service.on_device_connected(cb2)
        assert service.sse_client_count() == 2
        service.off_device_connected(cb1)
        assert service.sse_client_count() == 1
