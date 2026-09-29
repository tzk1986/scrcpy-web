"""
数据库路径解析测试
==================

覆盖方案 15 §9.3-14 的修复：
1. 相对路径以稳定基目录锚定（源码运行=仓库根；frozen=exe 同级，不可写时回落
   %LOCALAPPDATA%/OpenScrcpy），不随启动 cwd 漂移
2. 绝对路径原样保留
3. path 字段不被系统 PATH 环境变量污染（pydantic-settings 按字段名读 env 的冲突）
"""

import sys
from pathlib import Path

import pytest

import config.settings as settings_module
from config.settings import DatabaseConfig, get_settings

REPO_ROOT = Path(settings_module.__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("DB_PATH", raising=False)


def test_default_relative_path_anchored_to_repo_root():
    cfg = DatabaseConfig()
    assert Path(cfg.path).is_absolute()
    assert Path(cfg.path) == REPO_ROOT / "data" / "debug.sqlite"


def test_absolute_path_unchanged(tmp_path):
    abs_path = tmp_path / "custom.sqlite"
    cfg = DatabaseConfig(path=str(abs_path))
    assert cfg.path == str(abs_path)


def test_path_field_not_polluted_by_PATH_env(monkeypatch):
    monkeypatch.setenv("PATH", r"C:\fake\bin;D:\fake\bin")
    cfg = DatabaseConfig()
    assert "fake" not in cfg.path


def test_frozen_anchors_to_exe_dir(monkeypatch, tmp_path):
    fake_dir = tmp_path / "dist"
    fake_dir.mkdir()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(fake_dir / "OpenScrcpy.exe"))
    assert Path(DatabaseConfig().path) == fake_dir / "data" / "debug.sqlite"


def test_frozen_unwritable_exe_dir_falls_back_to_localappdata(monkeypatch, tmp_path):
    fake_dir = tmp_path / "program_files"
    fake_dir.mkdir()
    appdata = tmp_path / "appdata"
    appdata.mkdir()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(fake_dir / "OpenScrcpy.exe"))
    monkeypatch.setattr(settings_module, "_is_writable", lambda directory: False)
    monkeypatch.setenv("LOCALAPPDATA", str(appdata))
    assert Path(DatabaseConfig().path) == appdata / "OpenScrcpy" / "data" / "debug.sqlite"


def test_get_settings_applies_normalization():
    assert Path(get_settings().database.path).is_absolute()
    assert Path(get_settings().database.path) == REPO_ROOT / "data" / "debug.sqlite"


def test_db_path_env_overrides_yaml(monkeypatch, tmp_path):
    # docker-compose 等容器部署依赖 DB_PATH 覆盖 yaml 的 database.path。
    # 夹具须用平台自适应绝对路径：Windows 盘符字面量（C:\...）在 POSIX 上
    # 不以 / 开头，会被判为相对路径锚定到基目录（CI ubuntu runner 实证）
    env_path = tmp_path / "fromEnv" / "prod.sqlite"
    monkeypatch.setenv("DB_PATH", str(env_path))
    assert get_settings().database.path == str(env_path)