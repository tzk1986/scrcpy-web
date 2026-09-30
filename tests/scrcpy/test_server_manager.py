"""
Server 部署管理器测试
======================

测试 scrcpy-server.jar 部署管理器的功能。

测试内容：
    - 检查 server 是否存在
    - 推送 server 到设备
    - 确保 server 就绪
    - 获取 server 版本
    - 删除 server
    - 错误处理
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

from app.core.config import settings
from app.scrcpy.server_manager import ServerManager
from app.scrcpy.constants import (
    SCRCPY_SERVER_REMOTE_PATH,
    SCRCPY_SERVER_VERSION,
)


# ---------------------------------------------------------------------------
# 检查 server 存在测试
# ---------------------------------------------------------------------------

class TestServerExists:
    """检查 server 是否存在测试"""

    @pytest.mark.asyncio
    async def test_server_exists_true(self, mock_device_id, mock_adb_success):
        """测试 server 存在时返回 True"""
        with patch('asyncio.create_subprocess_exec', side_effect=mock_adb_success):
            manager = ServerManager()
            exists = await manager._server_exists(mock_device_id)
            # 应该返回 True（mock 返回成功）
            assert exists is True

    @pytest.mark.asyncio
    async def test_server_exists_false(self, mock_device_id, mock_adb_failure):
        """测试 server 不存在时返回 False"""
        with patch('asyncio.create_subprocess_exec', side_effect=mock_adb_failure):
            manager = ServerManager()
            exists = await manager._server_exists(mock_device_id)
            # 应该返回 False（mock 返回失败）
            assert exists is False


# ---------------------------------------------------------------------------
# 推送 server 测试
# ---------------------------------------------------------------------------

class TestPushServer:
    """推送 server 测试"""

    @pytest.mark.asyncio
    async def test_push_server_success(self, mock_device_id, mock_adb_success):
        """测试成功推送 server"""
        manager = ServerManager()

        # Mock _server_exists 返回 True（验证成功）
        with patch.object(manager, '_server_exists', return_value=True):
            with patch('asyncio.create_subprocess_exec', side_effect=mock_adb_success):
                success = await manager.push_server(mock_device_id)
                assert success is True

    @pytest.mark.asyncio
    async def test_push_server_failure(self, mock_device_id, mock_adb_failure):
        """测试推送 server 失败"""
        with patch('asyncio.create_subprocess_exec', side_effect=mock_adb_failure):
            manager = ServerManager()
            success = await manager.push_server(mock_device_id)
            assert success is False

    @pytest.mark.asyncio
    async def test_push_server_uses_configured_adb_path(
        self, mock_device_id, mock_adb_success, monkeypatch
    ):
        """push 命令使用配置的 ADB 路径（而非裸 "adb"）——打包硬前置。"""
        monkeypatch.setattr(settings().adb, "adb_path", "/fake/adb")
        manager = ServerManager()

        with patch('asyncio.create_subprocess_exec', side_effect=mock_adb_success) as exec_mock:
            success = await manager.push_server(mock_device_id)

        assert success is True
        assert exec_mock.call_args[0][0] == "/fake/adb"

    @pytest.mark.asyncio
    async def test_push_server_jar_not_found(self, mock_device_id):
        """测试本地 JAR 文件不存在"""
        manager = ServerManager()

        # 临时修改 jar 路径
        original_path = manager._jar_path
        manager._jar_path = Path("/nonexistent/scrcpy-server.jar")

        try:
            success = await manager.push_server(mock_device_id)
            assert success is False
        finally:
            manager._jar_path = original_path


# ---------------------------------------------------------------------------
# push_server bak 化测试（方案 28 D1+D3）
# ---------------------------------------------------------------------------

class TestPushServerBakRestore:
    """push_server 优先从 .bak 恢复主 JAR（D1+D3）"""

    @staticmethod
    def _proc(rc: int, out: bytes = b"", err: bytes = b"") -> MagicMock:
        """构造 mock subprocess：returncode 与 communicate 输出可定制。"""
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_proc.returncode = rc
        mock_proc.stdout = AsyncMock()
        mock_proc.stderr = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(out, err))
        mock_proc.wait = AsyncMock()
        mock_proc.kill = MagicMock()
        mock_proc.terminate = MagicMock()
        return mock_proc

    @staticmethod
    def _argv_list(exec_mock) -> list[tuple]:
        return [c.args for c in exec_mock.call_args_list]

    @staticmethod
    def _ls_bak_ok(size: int) -> MagicMock:
        out = (
            f"-rw-r--r-- 1 shell shell {size} 2026-09-30 12:00 "
            f"/data/local/tmp/scrcpy-server.jar.bak"
        ).encode()
        return TestPushServerBakRestore._proc(0, out=out)

    @pytest.fixture
    def manager_with_jar(self, tmp_path) -> ServerManager:
        """ServerManager 指向 5 字节假 jar（跨平台越过本地 jar 守卫）。"""
        jar = tmp_path / "scrcpy-server.jar"
        jar.write_bytes(b"dummy")
        manager = ServerManager()
        manager._jar_path = jar
        return manager

    @pytest.mark.asyncio
    async def test_bak_valid_restores_without_push(
        self, mock_device_id, manager_with_jar
    ):
        """bak 存在且大小吻合 → 仅 mv 恢复，不 push。"""
        seq = [self._ls_bak_ok(5), self._proc(0)]
        with patch('asyncio.create_subprocess_exec', side_effect=seq) as exec_mock:
            success = await manager_with_jar.push_server(mock_device_id)

        assert success is True
        assert exec_mock.call_count == 2
        argv = self._argv_list(exec_mock)
        assert not any("push" in a for a in argv)
        assert "mv" in argv[1]

    @pytest.mark.asyncio
    async def test_bak_missing_falls_back_to_push_and_cp(
        self, mock_device_id, manager_with_jar
    ):
        """bak 缺失 → push + cp 刷新 bak。"""
        seq = [
            self._proc(1, err=b"ls: scrcpy-server.jar.bak: No such file or directory"),
            self._proc(0),
            self._proc(0),
        ]
        with patch('asyncio.create_subprocess_exec', side_effect=seq) as exec_mock:
            success = await manager_with_jar.push_server(mock_device_id)

        assert success is True
        assert exec_mock.call_count == 3
        argv = self._argv_list(exec_mock)
        assert any("push" in a for a in argv)
        assert "cp" in argv[2]

    @pytest.mark.asyncio
    async def test_bak_size_mismatch_pushes_and_refreshes(
        self, mock_device_id, manager_with_jar
    ):
        """bak 大小不符 → push + cp（覆盖损坏 bak）。"""
        seq = [self._ls_bak_ok(999), self._proc(0), self._proc(0)]
        with patch('asyncio.create_subprocess_exec', side_effect=seq) as exec_mock:
            success = await manager_with_jar.push_server(mock_device_id)

        assert success is True
        assert exec_mock.call_count == 3
        argv = self._argv_list(exec_mock)
        assert any("push" in a for a in argv)
        assert "cp" in argv[2]

    @pytest.mark.asyncio
    async def test_bak_mv_failure_falls_back_to_push(
        self, mock_device_id, manager_with_jar
    ):
        """mv 恢复失败 → 回退 push（D3）。"""
        seq = [
            self._ls_bak_ok(5),
            self._proc(1, err=b"mv: permission denied"),
            self._proc(0),
            self._proc(0),
        ]
        with patch('asyncio.create_subprocess_exec', side_effect=seq) as exec_mock:
            success = await manager_with_jar.push_server(mock_device_id)

        assert success is True
        assert exec_mock.call_count == 4
        argv = self._argv_list(exec_mock)
        assert any("push" in a for a in argv)

    @pytest.mark.asyncio
    async def test_push_failure_returns_false_without_cp(
        self, mock_device_id, manager_with_jar
    ):
        """push 失败 → 返回 False（既有无异常语义），且不 cp。"""
        seq = [
            self._proc(1, err=b"ls: scrcpy-server.jar.bak: No such file or directory"),
            self._proc(1, err=b"adb: error: failed to copy"),
        ]
        with patch('asyncio.create_subprocess_exec', side_effect=seq) as exec_mock:
            success = await manager_with_jar.push_server(mock_device_id)

        assert success is False
        assert exec_mock.call_count == 2
        argv = self._argv_list(exec_mock)
        assert not any("cp" in a for a in argv)

    @pytest.mark.asyncio
    async def test_cp_failure_does_not_block_success(
        self, mock_device_id, manager_with_jar
    ):
        """push 成功后 cp 失败不阻塞（下次仍走 push 而已）。"""
        seq = [
            self._proc(1, err=b"ls: scrcpy-server.jar.bak: No such file or directory"),
            self._proc(0),
            self._proc(1, err=b"cp: bad path"),
        ]
        with patch('asyncio.create_subprocess_exec', side_effect=seq) as exec_mock:
            success = await manager_with_jar.push_server(mock_device_id)

        assert success is True
        assert exec_mock.call_count == 3


# ---------------------------------------------------------------------------
# 确保 server 就绪测试
# ---------------------------------------------------------------------------

class TestEnsureServer:
    """确保 server 就绪测试"""

    @pytest.mark.asyncio
    async def test_ensure_server_already_exists(self, mock_device_id, tmp_path):
        """测试 server 已存在时直接返回 True"""
        manager = ServerManager()
        # ensure_server 首步检查本地 jar 存在（gitignore 的产物，CI 干净 checkout 无），
        # 指向临时假 jar 以跨平台越过该守卫、真正执行被 mock 的就绪分支。
        manager._jar_path = tmp_path / "scrcpy-server.jar"
        manager._jar_path.write_bytes(b"dummy")

        # Mock _server_exists 返回 True
        with patch.object(manager, '_server_exists', return_value=True):
            success = await manager.ensure_server(mock_device_id)
            assert success is True

    @pytest.mark.asyncio
    async def test_ensure_server_push_needed(self, mock_device_id, tmp_path):
        """测试 server 不存在时推送"""
        manager = ServerManager()
        # 同 already_exists：越过本地 jar 存在性守卫
        manager._jar_path = tmp_path / "scrcpy-server.jar"
        manager._jar_path.write_bytes(b"dummy")

        # 第一次调用 _server_exists 返回 False（不存在）
        # 第二次调用返回 True（推送后验证）
        with patch.object(manager, '_server_exists', side_effect=[False, True]):
            with patch.object(manager, 'push_server', return_value=True):
                success = await manager.ensure_server(mock_device_id)
                assert success is True

    @pytest.mark.asyncio
    async def test_ensure_server_push_failed(self, mock_device_id, tmp_path):
        """测试推送失败"""
        manager = ServerManager()
        # 越过本地 jar 守卫，确保 False 来自 push_server 失败分支而非 jar 缺失（否则在
        # 无 jar 的环境下会因提前 return 而假绿）
        manager._jar_path = tmp_path / "scrcpy-server.jar"
        manager._jar_path.write_bytes(b"dummy")

        # Mock _server_exists 返回 False，push_server 也返回 False
        with patch.object(manager, '_server_exists', return_value=False):
            with patch.object(manager, 'push_server', return_value=False):
                success = await manager.ensure_server(mock_device_id)
                assert success is False

    @pytest.mark.asyncio
    async def test_ensure_server_jar_not_found(self, mock_device_id):
        """测试本地 JAR 文件不存在"""
        manager = ServerManager()

        # 临时修改 jar 路径
        original_path = manager._jar_path
        manager._jar_path = Path("/nonexistent/scrcpy-server.jar")

        try:
            success = await manager.ensure_server(mock_device_id)
            assert success is False
        finally:
            manager._jar_path = original_path


# ---------------------------------------------------------------------------
# 获取 server 版本测试
# ---------------------------------------------------------------------------

class TestGetServerVersion:
    """获取 server 版本测试"""

    @pytest.mark.asyncio
    async def test_get_server_version_exists(self, mock_device_id):
        """测试获取已存在 server 的版本"""
        manager = ServerManager()

        with patch.object(manager, '_server_exists', return_value=True):
            version = await manager.get_server_version(mock_device_id)
            assert version == SCRCPY_SERVER_VERSION

    @pytest.mark.asyncio
    async def test_get_server_version_not_exists(self, mock_device_id):
        """测试获取不存在 server 的版本"""
        manager = ServerManager()

        with patch.object(manager, '_server_exists', return_value=False):
            version = await manager.get_server_version(mock_device_id)
            assert version is None


# ---------------------------------------------------------------------------
# 删除 server 测试
# ---------------------------------------------------------------------------

class TestDeleteServer:
    """删除 server 测试"""

    @pytest.mark.asyncio
    async def test_delete_server_success(self, mock_device_id, mock_adb_success):
        """测试成功删除 server"""
        with patch('asyncio.create_subprocess_exec', side_effect=mock_adb_success):
            manager = ServerManager()
            success = await manager.delete_server(mock_device_id)
            assert success is True

    @pytest.mark.asyncio
    async def test_delete_server_failure(self, mock_device_id, mock_adb_failure):
        """测试删除 server 失败"""
        with patch('asyncio.create_subprocess_exec', side_effect=mock_adb_failure):
            manager = ServerManager()
            success = await manager.delete_server(mock_device_id)
            assert success is False


# ---------------------------------------------------------------------------
# 获取本地 JAR 路径测试
# ---------------------------------------------------------------------------

class TestGetLocalJarPath:
    """获取本地 JAR 路径测试"""

    def test_get_local_jar_path(self):
        """测试获取本地 JAR 路径"""
        manager = ServerManager()
        jar_path = manager.get_local_jar_path()

        # 应该返回 Path 对象
        assert isinstance(jar_path, Path)

        # 路径应该以 scrcpy-server.jar 结尾
        assert jar_path.name == "scrcpy-server.jar"

        # 路径应该在 scrcpy 模块目录下
        assert "scrcpy" in str(jar_path)


# ---------------------------------------------------------------------------
# 集成测试（需要真实设备）
# ---------------------------------------------------------------------------

class TestIntegration:
    """集成测试（需要真实设备）"""

    @pytest.mark.skip(reason="需要真实 Android 设备")
    @pytest.mark.asyncio
    async def test_full_workflow(self):
        """测试完整工作流"""
        # 这个测试需要真实的 Android 设备
        # 在实际环境中取消注释并运行

        manager = ServerManager()
        device_id = "your_device_id"

        # 1. 检查 server 是否存在
        exists = await manager._server_exists(device_id)

        # 2. 如果不存在，推送 server
        if not exists:
            success = await manager.push_server(device_id)
            assert success is True

        # 3. 验证 server 已存在
        exists = await manager._server_exists(device_id)
        assert exists is True

        # 4. 获取版本
        version = await manager.get_server_version(device_id)
        assert version == SCRCPY_SERVER_VERSION

        # 5. 删除 server
        success = await manager.delete_server(device_id)
        assert success is True

        # 6. 验证已删除
        exists = await manager._server_exists(device_id)
        assert exists is False
