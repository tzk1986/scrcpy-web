"""
方案 51 D01 回归：env > YAML > 默认 的兑现

走查坐实旧缺陷：yaml 节作为 init kwargs 传入子模型（BaseSettings）时压过其
env 候选，同名键一旦在 yaml 给出，LOG_LEVEL/ADB_PATH 等环境变量静默失效
（仅 BACKEND_*/DB_PATH 手工补丁幸免）。get_settings 现以 _ENV_OVERRIDES
统一 overlay 表在构造前压过全部 YAML 层；本文件钉全部登记键的优先级与
「无 env 时 yaml/默认照常」的行为兼容。
"""

import copy

import pytest

import config.settings as cs

_ALL_ENV_VARS = ("BACKEND_HOST", "BACKEND_PORT", "DB_PATH", "LOG_LEVEL", "ADB_PATH", "APP_ENV")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in _ALL_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def _fake_yaml(base: dict):
    def loader(env: str = "base") -> dict:
        return copy.deepcopy(base) if env == "base" else {}

    return loader


def test_log_level_env_beats_yaml(monkeypatch):
    monkeypatch.setattr(cs, "load_yaml_config", _fake_yaml({"app": {"log_level": "INFO"}}))
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    assert cs.get_settings().app.log_level == "DEBUG"


def test_log_level_yaml_kept_without_env(monkeypatch):
    monkeypatch.setattr(cs, "load_yaml_config", _fake_yaml({"app": {"log_level": "INFO"}}))
    assert cs.get_settings().app.log_level == "INFO"


def test_adb_path_env_beats_yaml(monkeypatch):
    monkeypatch.setattr(
        cs, "load_yaml_config", _fake_yaml({"adb": {"adb_path": "yaml-adb", "timeout": 7}})
    )
    monkeypatch.setenv("ADB_PATH", "env-adb")
    s = cs.get_settings()
    assert s.adb.adb_path == "env-adb"
    assert s.adb.path == "env-adb"
    assert s.adb.timeout == 7  # 覆盖仅触及登记键，节内其余键保留


def test_backend_host_port_env_beats_yaml(monkeypatch):
    monkeypatch.setattr(
        cs, "load_yaml_config", _fake_yaml({"server": {"host": "10.0.0.1", "port": 1234}})
    )
    monkeypatch.setenv("BACKEND_HOST", "127.0.0.1")
    monkeypatch.setenv("BACKEND_PORT", "9999")
    s = cs.get_settings()
    assert (s.server.host, s.server.port) == ("127.0.0.1", 9999)


def test_db_path_env_beats_yaml(monkeypatch):
    monkeypatch.setattr(
        cs, "load_yaml_config", _fake_yaml({"database": {"path": "./from_yaml.sqlite"}})
    )
    monkeypatch.setenv("DB_PATH", "./from_env.sqlite")
    path = cs.get_settings().database.path
    assert path.endswith("from_env.sqlite")  # 相对路径已锚定 app_base_dir 归一


def test_defaults_when_yaml_and_env_absent(monkeypatch):
    monkeypatch.setattr(cs, "load_yaml_config", _fake_yaml({}))
    s = cs.get_settings()
    assert s.server.port == 8765
    assert s.app.log_level == "INFO"
