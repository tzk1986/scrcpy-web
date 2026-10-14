"""GET /api/system/scrcpy-update 测试（方案 45 T7；网络调用以 monkeypatch 隔离）。"""
import urllib.error

import pytest

from app.scrcpy.constants import SCRCPY_SERVER_VERSION

from .http_testkit import make_client


@pytest.mark.parametrize(
    "tag,expected",
    [
        # upToDate 用例从 SCRCPY_SERVER_VERSION 常量派生（当前为 "5.0.1"，加 v 前缀，
        # _parse_version 容忍可选 v），常量升级后此用例自动跟随
        (f"v{SCRCPY_SERVER_VERSION}", "upToDate"),
        ("v5.1.0", "updateAvailable"),
        ("v4.9.0", "installedNewer"),
        ("somerelease", "unknown"),  # 无法解析上游版本号
    ],
)
def test_update_status_variants(monkeypatch, tag, expected):
    monkeypatch.setattr(
        "app.interfaces.http.system._fetch_latest_release",
        lambda: {"tag_name": tag, "html_url": "https://github.com/Genymobile/scrcpy/releases"},
    )
    res = make_client().get("/api/system/scrcpy-update")

    assert res.status_code == 200
    assert res.json()["status"] == expected


def test_update_network_failure_reports_unknown(monkeypatch):
    def boom():
        raise urllib.error.URLError("offline")

    monkeypatch.setattr("app.interfaces.http.system._fetch_latest_release", boom)
    res = make_client().get("/api/system/scrcpy-update")

    body = res.json()
    assert body["status"] == "unknown"
    assert "GitHub" in body["error"]


def test_parse_version():
    from app.interfaces.http.system import _parse_version

    assert _parse_version("v5.0.1") == (5, 0, 1)
    assert _parse_version("3.2") == (3, 2, 0)
    assert _parse_version("junk") is None