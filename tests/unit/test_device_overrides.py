"""设备级参数覆盖 helpers 测试（方案 45 T8）。"""
import copy

import yaml

import config.settings as config_settings
from config.settings import (
    device_alias,
    device_recording_settings,
    device_stream_settings,
    reload_settings,
)


def _layered_loader(layers):
    def loader(env: str = "base"):
        value = layers.get(env, {})
        return copy.deepcopy(value) if isinstance(value, dict) else value

    return loader


def test_device_stream_override_and_fallback(monkeypatch):
    monkeypatch.setattr(
        config_settings, "load_yaml_config",
        _layered_loader({
            "base": {"app": {"name": "OpenScrcpy"},
                     "server": {"host": "0.0.0.0", "port": 8765}},
            "user": {"devices": {"dev-a": {"stream": {"max_size": 720}}}},
        }),
    )
    saved = config_settings._settings
    config_settings._settings = None
    try:
        reload_settings()

        ds = device_stream_settings("dev-a")
        assert ds.max_size == 720
        assert ds.fps == 30  # 未覆盖键回落全局默认
        assert device_stream_settings("other").max_size == 1080
    finally:
        config_settings._settings = saved


def test_device_recording_override(monkeypatch):
    monkeypatch.setattr(
        config_settings, "load_yaml_config",
        _layered_loader({
            "base": {"app": {"name": "OpenScrcpy"},
                     "server": {"host": "0.0.0.0", "port": 8765}},
            "user": {"devices": {"dev-a": {"recording": {"time_limit": 120}}}},
        }),
    )
    saved = config_settings._settings
    config_settings._settings = None
    try:
        reload_settings()

        dr = device_recording_settings("dev-a")
        assert dr.time_limit == 120
        assert device_recording_settings("other").time_limit == 60
    finally:
        config_settings._settings = saved


def test_device_key_with_special_chars_roundtrip():
    dev_id = "192.168.8.18:5555"  # ':' '.' 在 yaml 键内往返一致（Review Focus #5）
    cfg = {"devices": {dev_id: {"stream": {"fps": 24}}}}
    dumped = yaml.safe_dump(cfg, allow_unicode=True, sort_keys=True)
    reloaded = yaml.safe_load(dumped)

    assert reloaded["devices"][dev_id] == {"stream": {"fps": 24}}


def test_device_alias_helper_three_states(monkeypatch):
    """方案 50 T1：device_alias 单源读取——有 alias / 设备节无 alias 键 / 设备不在表，均为既定回落。"""
    monkeypatch.setattr(
        config_settings, "load_yaml_config",
        _layered_loader({
            "base": {"app": {"name": "OpenScrcpy"},
                     "server": {"host": "0.0.0.0", "port": 8765}},
            "user": {"devices": {
                "dev-a": {"alias": "主力机", "stream": {"fps": 24}},
                "dev-b": {"stream": {"fps": 60}},
            }},
        }),
    )
    saved = config_settings._settings
    config_settings._settings = None
    try:
        reload_settings()

        assert device_alias("dev-a") == "主力机"
        assert device_alias("dev-b") == ""  # 有设备节未设备注 → 回落空串
        assert device_alias("dev-c") == ""  # 设备不在表 → 空串
    finally:
        config_settings._settings = saved