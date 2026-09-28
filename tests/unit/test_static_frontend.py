"""
前端静态托管与 SPA fallback 测试（方案 15 任务 2）
==================================================

覆盖 §8.5 任务 2 / §9.3-10：dist 存在时挂载静态资源，
history 深链 fallback 到 index.html，API 路由优先命中，
路径不得逃逸 dist 目录（../ 防护）。
"""

import sys
from pathlib import Path

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
    # 不用 with：不触发 lifespan 副作用（与项目其他 TestClient 用例一致）
    monkeypatch.setattr(main_module, "FRONTEND_DIST", _make_fake_dist(tmp_path))
    return TestClient(create_app(), raise_server_exceptions=False)


def test_frontend_dist_source_points_repo():
    assert main_module.FRONTEND_DIST == (
        Path(main_module.__file__).resolve().parent.parent.parent / "frontend" / "dist"
    )


def test_frontend_dist_frozen_uses_meipass(monkeypatch, tmp_path):
    # frozen 下 __file__ = _MEIPASS/app/main.py，dist 在 _MEIPASS/frontend/dist
    # （上溯级数与源码不同，审查 Critical 回归锁定）
    fake_meipass = tmp_path / "_internal"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(fake_meipass), raising=False)
    assert main_module._compute_frontend_dist() == fake_meipass / "frontend" / "dist"


def test_index_served_at_root(tmp_path, monkeypatch):
    client = _client_with_dist(tmp_path, monkeypatch)
    r = client.get("/")
    assert r.status_code == 200
    assert "SPA" in r.text


def test_deep_link_falls_back_to_index(tmp_path, monkeypatch):
    # history 路由（createWebHistory 已实测）：/devices/abc 无对应文件 → index.html
    client = _client_with_dist(tmp_path, monkeypatch)
    r = client.get("/devices/abc")
    assert r.status_code == 200
    assert "SPA" in r.text


def test_assets_directory_served(tmp_path, monkeypatch):
    client = _client_with_dist(tmp_path, monkeypatch)
    r = client.get("/assets/app.js")
    assert r.status_code == 200
    assert "assets" in r.text


def test_real_file_in_dist_served(tmp_path, monkeypatch):
    client = _client_with_dist(tmp_path, monkeypatch)
    r = client.get("/about.html")
    assert r.status_code == 200
    assert r.text == "about-page"


def test_api_routes_take_precedence(tmp_path, monkeypatch):
    client = _client_with_dist(tmp_path, monkeypatch)
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_path_traversal_blocked(tmp_path, monkeypatch):
    secret = tmp_path / "secret.txt"
    secret.write_text("top-secret", encoding="utf-8")
    client = _client_with_dist(tmp_path, monkeypatch)
    # httpx 会把 /../secret.txt 归一化为 /secret.txt（触达不到服务端防护），
    # 用百分号编码形式让服务端真实收到逃逸路径，验证 containment 防护生效
    r = client.get("/%2e%2e%2fsecret.txt")
    assert "top-secret" not in r.text
    assert "SPA" in r.text


def test_unknown_api_path_stays_404(tmp_path, monkeypatch):
    # API 命名空间未注册路径不得被 SPA fallback 吞成 200 HTML
    client = _client_with_dist(tmp_path, monkeypatch)
    assert client.get("/api/nonexistent").status_code == 404
    assert client.get("/api").status_code == 404
    # httpx 的 get() 不接受 method= 参数（plan 片段笔误，语义等价改写）
    assert client.head("/api/nonexistent").status_code == 404
    assert client.post("/api/nonexistent").status_code != 200


def test_no_dist_no_mount(tmp_path, monkeypatch):
    monkeypatch.setattr(main_module, "FRONTEND_DIST", tmp_path / "nonexistent")
    client = TestClient(create_app(), raise_server_exceptions=False)
    r = client.get("/")
    assert r.status_code == 404
    assert client.get("/health").status_code == 200
