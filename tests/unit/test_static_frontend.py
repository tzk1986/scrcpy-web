"""
前端静态托管与 SPA fallback 测试（方案 15 任务 2）
==================================================

覆盖 §8.5 任务 2 / §9.3-10：dist 存在时挂载静态资源，
history 深链 fallback 到 index.html，API 路由优先命中，
路径不得逃逸 dist 目录（../ 防护）。
"""

import app.main as main_module
from app.main import create_app
from fastapi.testclient import TestClient


def _make_fake_dist(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>SPA</title>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text('console.log("assets")', encoding="utf-8")
    (dist / "about.html").write_text("about-page", encoding="utf-8")
    return dist


def _client_with_dist(tmp_path, monkeypatch):
    monkeypatch.setattr(main_module, "FRONTEND_DIST", _make_fake_dist(tmp_path))
    return TestClient(create_app(), raise_server_exceptions=False)


def test_index_served_at_root(tmp_path, monkeypatch):
    with _client_with_dist(tmp_path, monkeypatch) as client:
        r = client.get("/")
        assert r.status_code == 200
        assert "SPA" in r.text


def test_deep_link_falls_back_to_index(tmp_path, monkeypatch):
    # history 路由（createWebHistory 已实测）：/devices/abc 无对应文件 → index.html
    with _client_with_dist(tmp_path, monkeypatch) as client:
        r = client.get("/devices/abc")
        assert r.status_code == 200
        assert "SPA" in r.text


def test_assets_directory_served(tmp_path, monkeypatch):
    with _client_with_dist(tmp_path, monkeypatch) as client:
        r = client.get("/assets/app.js")
        assert r.status_code == 200
        assert "assets" in r.text


def test_real_file_in_dist_served(tmp_path, monkeypatch):
    with _client_with_dist(tmp_path, monkeypatch) as client:
        r = client.get("/about.html")
        assert r.status_code == 200
        assert r.text == "about-page"


def test_api_routes_take_precedence(tmp_path, monkeypatch):
    with _client_with_dist(tmp_path, monkeypatch) as client:
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}


def test_path_traversal_blocked(tmp_path, monkeypatch):
    secret = tmp_path / "secret.txt"
    secret.write_text("top-secret", encoding="utf-8")
    with _client_with_dist(tmp_path, monkeypatch) as client:
        r = client.get("/../secret.txt")
        assert "top-secret" not in r.text


def test_unknown_api_path_stays_404(tmp_path, monkeypatch):
    # API 命名空间未注册路径不得被 SPA fallback 吞成 200 HTML
    with _client_with_dist(tmp_path, monkeypatch) as client:
        assert client.get("/api/nonexistent").status_code == 404


def test_no_dist_no_mount(tmp_path, monkeypatch):
    monkeypatch.setattr(main_module, "FRONTEND_DIST", tmp_path / "nonexistent")
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        r = client.get("/")
        assert r.status_code == 404
        assert client.get("/health").status_code == 200