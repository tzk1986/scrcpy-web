"""
后端端口可配置化测试
=====================

验证 get_settings 的端口解析优先级：BACKEND_PORT 环境变量 > base.yaml > 默认(8765)。
直接调用 get_settings()（非缓存的 settings()），每个用例独立读环境变量。
"""

import pytest

from config.settings import get_settings


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    # 清除可能干扰的外部变量，回到"无 env、base"的干净基线
    monkeypatch.delenv("BACKEND_PORT", raising=False)
    monkeypatch.delenv("BACKEND_HOST", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)


def test_default_port_is_8765():
    assert get_settings().server.port == 8765


def test_base_yaml_port_used(monkeypatch):
    # base.yaml 现设 port=8765；确认它作为默认生效（与类默认一致）
    assert get_settings().server.host == "0.0.0.0"


def test_env_overrides_port(monkeypatch):
    monkeypatch.setenv("BACKEND_PORT", "9100")
    assert get_settings().server.port == 9100


def test_env_overrides_host(monkeypatch):
    monkeypatch.setenv("BACKEND_HOST", "127.0.0.1")
    assert get_settings().server.host == "127.0.0.1"


def test_env_port_wins_over_yaml(monkeypatch):
    # env 覆盖在 Settings 构造前显式写入 config，优先级高于 base.yaml
    monkeypatch.setenv("BACKEND_PORT", "12345")
    assert get_settings().server.port == 12345


def test_invalid_port_raises(monkeypatch):
    monkeypatch.setenv("BACKEND_PORT", "not-a-number")
    with pytest.raises(ValueError):
        get_settings()
