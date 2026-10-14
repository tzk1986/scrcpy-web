"""GET/PUT /api/settings 测试（方案 45 T2；devices 节用例在 T8 追加）。"""
import pytest
import yaml

import config.settings as config_settings
from config.settings import reload_settings, settings

from .http_testkit import make_client


@pytest.fixture
def settings_env(tmp_path, monkeypatch):
    """user.yaml 落点重定向到 tmp_path；配置加载器换成分层 fake，user 层直读该文件。"""
    user_path = tmp_path / "user.yaml"
    monkeypatch.setattr("app.core.settings_store.user_yaml_path", lambda: user_path)
    for var in ("BACKEND_PORT", "BACKEND_HOST", "APP_ENV", "DB_PATH"):
        monkeypatch.delenv(var, raising=False)

    def layered(env: str = "base"):
        if env == "base":
            return {"app": {"name": "OpenScrcpy"},
                    "server": {"host": "0.0.0.0", "port": 8765}}
        if env == "user":
            if not user_path.exists():
                return {}
            loaded = yaml.safe_load(user_path.read_text(encoding="utf-8"))
            return loaded if isinstance(loaded, dict) else {}
        return {}

    monkeypatch.setattr(config_settings, "load_yaml_config", layered)
    saved = config_settings._settings
    config_settings._settings = None
    reload_settings()
    try:
        yield user_path
    finally:
        config_settings._settings = saved


def test_get_returns_default_values_with_default_source(settings_env):
    res = make_client().get("/api/settings")
    assert res.status_code == 200
    body = res.json()
    assert body["stream"]["max_size"] == {"value": 1080, "source": "default"}
    assert body["stream"]["codec"] == {"value": "h264", "source": "default"}
    assert body["recording"]["time_limit"] == {"value": 60, "source": "default"}


def test_put_writes_nested_section_and_creates_file(settings_env):
    res = make_client().put("/api/settings",
                            json={"stream": {"bit_rate": "6M", "fps": 24}})
    assert res.status_code == 200
    assert res.json() == {"saved": True}
    assert "6M" in settings_env.read_text(encoding="utf-8")

    reload_settings()  # 生产路径 watcher 会在轮询间隔内完成同一动作
    assert settings().stream.bit_rate == "6M"
    assert settings().stream.fps == 24


def test_put_merges_with_existing_keys(settings_env):
    make_client().put("/api/settings", json={"stream": {"fps": 24}})
    res = make_client().put("/api/settings", json={"stream": {"bit_rate": "6M"}})
    assert res.status_code == 200

    reload_settings()
    assert settings().stream.fps == 24
    assert settings().stream.bit_rate == "6M"


def test_put_rejects_unknown_key(settings_env):
    res = make_client().put("/api/settings", json={"stream": {"bogus": 1}})
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"


def test_put_rejects_out_of_range_value(settings_env):
    res = make_client().put("/api/settings", json={"stream": {"fps": 0}})
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"


def test_put_rejects_empty_body(settings_env):
    res = make_client().put("/api/settings", json={})
    assert res.status_code == 422


def test_put_null_section_resets_to_defaults(settings_env):
    make_client().put("/api/settings", json={"stream": {"fps": 24}})
    res = make_client().put("/api/settings", json={"stream": None})
    assert res.status_code == 200

    reload_settings()
    assert settings().stream.fps == 30


def test_get_marks_user_source_after_put(settings_env):
    make_client().put("/api/settings", json={"stream": {"bit_rate": "6M"}})
    reload_settings()
    body = make_client().get("/api/settings").json()
    assert body["stream"]["bit_rate"] == {"value": "6M", "source": "user"}


def test_put_fails_loudly_on_corrupt_user_yaml(settings_env):
    settings_env.write_text("stream: [bad\n", encoding="utf-8")
    res = make_client().put("/api/settings", json={"stream": {"fps": 24}})
    assert res.status_code == 500
    assert res.json()["error"]["code"] == "SETTINGS_STORE_ERROR"


def test_get_fails_loudly_on_non_mapping_user_yaml(settings_env):
    settings_env.write_text("- just\n- a\n- list\n", encoding="utf-8")
    res = make_client().get("/api/settings")
    assert res.status_code == 500
    assert res.json()["error"]["code"] == "SETTINGS_STORE_ERROR"


def test_put_device_override_and_get_lists_it(settings_env):
    res = make_client().put(
        "/api/settings",
        json={"devices": {"192.168.8.18:5555": {"stream": {"max_size": 720}}}},
    )
    assert res.status_code == 200

    reload_settings()
    body = make_client().get("/api/settings").json()
    assert body["devices"]["192.168.8.18:5555"]["stream"] == {"max_size": 720}


def test_put_device_override_null_removes_device(settings_env):
    make_client().put(
        "/api/settings",
        json={"devices": {"d1": {"stream": {"fps": 24}}, "d2": {"stream": {"fps": 10}}}},
    )
    res = make_client().put("/api/settings", json={"devices": {"d1": None}})
    assert res.status_code == 200

    reload_settings()
    body = make_client().get("/api/settings").json()
    assert "d1" not in body["devices"]
    assert "d2" in body["devices"]


def test_put_device_override_rejects_bad_device_key(settings_env):
    res = make_client().put(
        "/api/settings", json={"devices": {"bad key!": {"stream": {"fps": 24}}}}
    )
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"


def test_put_fails_loudly_when_config_dir_not_writable(settings_env, monkeypatch):
    def boom(config):
        raise OSError("read-only fs")

    monkeypatch.setattr("app.core.settings_store.write_user_config", boom)
    res = make_client().put("/api/settings", json={"stream": {"fps": 24}})
    assert res.status_code == 500
    assert res.json()["error"]["code"] == "SETTINGS_STORE_ERROR"
