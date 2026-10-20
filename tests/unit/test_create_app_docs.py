"""
A1 L5：frozen（打包 exe）运行关闭 docs/OpenAPI 暴露面
========================================================

exe 分发场景局域网可达且无鉴权，Swagger UI 与 /openapi.json
成为免费的功能面侦察入口。frozen 时关闭三件套；源码 dev 保持
默认开启（开发依赖文档）。create_app 工厂可重入，逐次验证。
"""

import sys

from fastapi.testclient import TestClient

from app.main import create_app


def test_frozen_app_disables_docs_surface(monkeypatch) -> None:
    """frozen：docs 路由不注册。GET /docs 命中的是 SPA 前端回退页而非
    Swagger（StaticFiles catch-all 预期行为），故断言响应不含 Swagger 面。"""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    app = create_app()
    assert app.docs_url is None
    assert app.redoc_url is None
    assert app.openapi_url is None

    client = TestClient(app)
    assert b"Swagger UI" not in client.get("/docs").content
    assert b"swagger" not in client.get("/openapi.json").content


def test_dev_app_keeps_docs_surface(monkeypatch) -> None:
    monkeypatch.delattr(sys, "frozen", raising=False)
    app = create_app()
    assert app.docs_url == "/docs"
    assert app.openapi_url == "/openapi.json"

    client = TestClient(app)
    assert b"Swagger UI" in client.get("/docs").content
    assert client.get("/openapi.json").json()["openapi"]
