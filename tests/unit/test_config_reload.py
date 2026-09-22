"""
配置热重载测试
================

覆盖方案 01「配置热重载」的实施：

    - reload_settings()：重新加载并替换全局缓存单例
    - reload_config()：成功路径 / 失败保留旧配置 / 端口变更告警标记
    - ConfigWatcher：mtime 快照检测与监听循环触发
    - POST /api/system/config/reload 手动触发端点
    - ADB 路径动态读取（AdbCliDriver / InteractiveShell 热重载即时生效）
"""

import asyncio
import copy
import os
import time

import pytest

import config.settings as config_settings
from app.core.config_watch import ConfigWatcher, reload_config, watched_paths
from config.settings import reload_settings, settings

from .http_testkit import make_client


@pytest.fixture(autouse=True)
def _isolate_settings(monkeypatch):
    """隔离全局配置缓存与环境变量，用例结束后还原，避免污染其他测试。"""
    for var in ("BACKEND_PORT", "BACKEND_HOST", "APP_ENV", "ADB_PATH"):
        monkeypatch.delenv(var, raising=False)
    saved = config_settings._settings
    try:
        yield
    finally:
        config_settings._settings = saved


def _fake_loader(config: dict):
    """替换 load_yaml_config：base 返回给定配置，环境文件返回空。"""

    def loader(env: str = "base"):
        return copy.deepcopy(config) if env == "base" else {}

    return loader


def _base_config(**app_overrides) -> dict:
    app = {"name": "OpenScrcpy", "version": "0.1.0"}
    app.update(app_overrides)
    return {"app": app, "server": {"host": "0.0.0.0", "port": 8765}}


# ---------------------------------------------------------------------------
# reload_settings / reload_config
# ---------------------------------------------------------------------------

def test_reload_settings_replaces_cached_singleton(monkeypatch):
    before = settings()
    assert before.app.name == "OpenScrcpy"

    monkeypatch.setattr(
        config_settings, "load_yaml_config",
        _fake_loader(_base_config(name="Reloaded")),
    )
    reloaded = reload_settings()

    assert reloaded.app.name == "Reloaded"
    assert settings() is reloaded  # 全局缓存已替换


def test_reload_failure_keeps_old_config(monkeypatch):
    old = settings()

    def boom(env: str = "base"):
        raise ValueError("bad yaml")

    monkeypatch.setattr(config_settings, "load_yaml_config", boom)
    result = reload_config()

    assert result["reloaded"] is False
    assert "bad yaml" in result["error"]
    assert settings() is old  # 旧配置保持生效


def test_reload_rejects_missing_or_empty_base_yaml(monkeypatch):
    """base.yaml 缺失/为空视为失败路径，保留旧配置（不静默回落内置默认值）。"""
    old = settings()

    monkeypatch.setattr(config_settings, "load_yaml_config", lambda env="base": {})
    result = reload_config()

    assert result["reloaded"] is False
    assert "base.yaml" in result["error"]
    assert settings() is old


def test_reload_endpoint_rejects_empty_base_yaml(monkeypatch):
    monkeypatch.setattr(config_settings, "load_yaml_config", lambda env="base": {})
    res = make_client().post("/api/system/config/reload")

    assert res.status_code == 400
    assert res.json()["error"]["code"] == "CONFIG_RELOAD_FAILED"


def test_reload_config_reports_restart_when_bind_changed(monkeypatch):
    monkeypatch.setattr(
        config_settings, "load_yaml_config",
        _fake_loader({"app": {"name": "OpenScrcpy"},
                      "server": {"host": "127.0.0.1", "port": 9999}}),
    )
    result = reload_config()

    assert result == {"reloaded": True, "restart_required": True}


def test_reload_config_success_same_bind(monkeypatch):
    monkeypatch.setattr(
        config_settings, "load_yaml_config",
        _fake_loader(_base_config(name="SameBind")),
    )
    result = reload_config()

    assert result == {"reloaded": True, "restart_required": False}


# ---------------------------------------------------------------------------
# ConfigWatcher
# ---------------------------------------------------------------------------

def test_watched_paths_includes_base_yaml():
    assert "base.yaml" in [p.name for p in watched_paths()]


def test_config_watcher_detects_mtime_change(tmp_path, monkeypatch):
    cfg = tmp_path / "base.yaml"
    cfg.write_text("app: {}\n", encoding="utf-8")
    monkeypatch.setattr("app.core.config_watch.watched_paths", lambda: [cfg])

    watcher = ConfigWatcher(interval=0.01)
    watcher._mtimes = watcher.scan()
    assert watcher.poll_changed() is False

    future = time.time() + 10
    os.utime(cfg, (future, future))
    assert watcher.poll_changed() is True
    assert watcher.poll_changed() is False  # 快照已更新


async def test_config_watcher_triggers_reload_on_change(tmp_path, monkeypatch):
    cfg = tmp_path / "base.yaml"
    cfg.write_text("app: {}\n", encoding="utf-8")
    monkeypatch.setattr("app.core.config_watch.watched_paths", lambda: [cfg])
    calls: list[int] = []
    monkeypatch.setattr("app.core.config_watch.reload_config", lambda: calls.append(1))

    watcher = ConfigWatcher(interval=0.01)
    watcher.start()
    await asyncio.sleep(0.05)  # 让监听循环建立初始快照
    future = time.time() + 10
    os.utime(cfg, (future, future))
    for _ in range(100):
        if calls:
            break
        await asyncio.sleep(0.01)
    await watcher.stop()

    assert calls  # 文件变化已触发重载


async def test_config_watcher_start_stop_idempotent(monkeypatch):
    monkeypatch.setattr("app.core.config_watch.watched_paths", lambda: [])
    watcher = ConfigWatcher(interval=0.01)

    watcher.start()
    first = watcher._task
    assert first is not None and not first.done()
    watcher.start()  # 已运行 → 不重建
    assert watcher._task is first

    await watcher.stop()
    assert watcher._task is None
    await watcher.stop()  # 幂等


# ---------------------------------------------------------------------------
# HTTP 端点
# ---------------------------------------------------------------------------

def test_reload_endpoint_success(monkeypatch):
    monkeypatch.setattr(
        config_settings, "load_yaml_config",
        _fake_loader(_base_config(name="HttpReload")),
    )
    res = make_client().post("/api/system/config/reload")

    assert res.status_code == 200
    assert res.json() == {"reloaded": True, "restart_required": False}


def test_reload_endpoint_failure_maps_to_400(monkeypatch):
    def boom(env: str = "base"):
        raise ValueError("bad yaml")

    monkeypatch.setattr(config_settings, "load_yaml_config", boom)
    res = make_client().post("/api/system/config/reload")

    assert res.status_code == 400
    assert res.json()["error"]["code"] == "CONFIG_RELOAD_FAILED"


# ---------------------------------------------------------------------------
# ADB 路径动态读取
# ---------------------------------------------------------------------------

def test_adb_driver_reads_path_dynamically(monkeypatch):
    from app.infrastructure.adb.cli import AdbCliDriver

    monkeypatch.setenv("ADB_PATH", "/fake/first")
    reload_settings()  # 让 env 变更进入缓存单例
    driver = AdbCliDriver()
    assert driver.adb_path == "/fake/first"

    monkeypatch.setenv("ADB_PATH", "/fake/second")
    reload_settings()
    assert driver.adb_path == "/fake/second"  # 同一实例，热重载即时生效


def test_interactive_shell_reads_path_dynamically(monkeypatch):
    from app.infrastructure.adb.shell import InteractiveShell

    monkeypatch.setenv("ADB_PATH", "/fake/shell")
    reload_settings()
    shell = InteractiveShell()
    assert shell._adb_path == "/fake/shell"

    monkeypatch.setenv("ADB_PATH", "/fake/shell2")
    reload_settings()
    assert shell._adb_path == "/fake/shell2"