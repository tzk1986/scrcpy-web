"""
冻结环境 config 路径解析测试（方案 15 任务 1）
==============================================

覆盖 §8.4-7 / §9.3-4：
1. load_yaml_config：frozen 时优先 exe 同级 config/（存在 base.yaml 才生效），
   否则回落 bundle 内 basconfig
2. config_watch.watched_paths 与加载路径同步（§9.3-4：CONFIG_DIR 独立常量
   必须同改，否则热重载仍监听 bundle 内文件）
"""

import sys
from pathlib import Path

import pytest

import config.settings as settings_module
from config.settings import load_yaml_config

REPO_CONFIG = Path(settings_module.__file__).resolve().parent


@pytest.fixture
def _fake_frozen(monkeypatch, tmp_path):
    """假装冻结运行于 tmp_path/OpenScrcpy.exe（可写）。"""
    exe = tmp_path / "OpenScrcpy.exe"
    exe.write_bytes(b"")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    return tmp_path


def test_source_run_uses_repo_config():
    assert load_yaml_config("base")["app"]["name"] == "OpenScrcpy"


def test_frozen_prefers_exe_side_config(_fake_frozen):
    exe_config = _fake_frozen / "config"
    exe_config.mkdir()
    (exe_config / "base.yaml").write_text("app:\n  name: ExeSide\n", encoding="utf-8")
    assert load_yaml_config("base")["app"]["name"] == "ExeSide"


def test_frozen_falls_back_to_bundle_config(_fake_frozen):
    # exe 同级无 config/base.yaml → 读回 bundle 内（仓库 config/）
    assert load_yaml_config("base")["app"]["name"] == "OpenScrcpy"


def test_frozen_exe_side_env_file_preferred(_fake_frozen, monkeypatch):
    monkeypatch.setenv("APP_ENV", "dev")
    exe_config = _fake_frozen / "config"
    exe_config.mkdir()
    (exe_config / "base.yaml").write_text("app:\n  name: ExeSide\n", encoding="utf-8")
    (exe_config / "dev.yaml").write_text("app:\n  name: ExeSideDev\n", encoding="utf-8")
    assert load_yaml_config("dev")["app"]["name"] == "ExeSideDev"


def test_watched_paths_tracks_exe_side_config(_fake_frozen):
    from app.core.config_watch import watched_paths

    exe_config = _fake_frozen / "config"
    exe_config.mkdir()
    (exe_config / "base.yaml").write_text("app:\n  name: ExeSide\n", encoding="utf-8")
    assert watched_paths()[0] == exe_config / "base.yaml"


def test_watched_paths_defaults_to_repo_config():
    from app.core.config_watch import watched_paths

    assert watched_paths()[0] == REPO_CONFIG / "base.yaml"