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
    async def test_push_server_verification_failed(self, mock_device_id, mock_adb_success):
        """测试推送成功但验证失败"""
        manager = ServerManager()

        # Mock _server_exists 返回 False（验证失败）
        with patch.object(manager, '_server_exists', return_value=False):
            with patch('asyncio.create_subprocess_exec', side_effect=mock_adb_success):
                success = await manager.push_server(mock_device_id)
                assert success is False

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
# 确保 server 就绪测试
# ---------------------------------------------------------------------------

class TestEnsureServer:
    """确保 server 就绪测试"""

    @pytest.mark.asyncio
    async def test_ensure_server_already_exists(self, mock_device_id):
        """测试 server 已存在时直接返回 True"""
        manager = ServerManager()

        # Mock _server_exists 返回 True
        with patch.object(manager, '_server_exists', return_value=True):
            success = await manager.ensure_server(mock_device_id)
            assert success is True

    @pytest.mark.asyncio
    async def test_ensure_server_push_needed(self, mock_device_id):
        """测试 server 不存在时推送"""
        manager = ServerManager()

        # 第一次调用 _server_exists 返回 False（不存在）
        # 第二次调用返回 True（推送后验证）
        with patch.object(manager, '_server_exists', side_effect=[False, True]):
            with patch.object(manager, 'push_server', return_value=True):
                success = await manager.ensure_server(mock_device_id)
                assert success is True

    @pytest.mark.asyncio
    async def test_ensure_server_push_failed(self, mock_device_id):
        """测试推送失败"""
        manager = ServerManager()

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
