import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.interfaces.http.origin_guard import OriginGuardMiddleware


def _make_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(OriginGuardMiddleware)

    @app.get("/probe")
    async def probe() -> dict[str, bool]:
        return {"ok": True}

    @app.websocket("/ws")
    async def ws(websocket: WebSocket) -> None:
        await websocket.accept()
        await websocket.send_text("hello")

    return app


def _client(base_url: str = "http://127.0.0.1:8765") -> TestClient:
    return TestClient(_make_app(), base_url=base_url)


# 注：starlette TestClient.websocket_connect 硬编码目标 ws://testserver（base_url 不生效，
# 已核 1.3.1/1.7.0 两版源码），WS 用例须用显式 host header 提供 Host=127.0.0.1:8765。

def test_rejects_foreign_origin():
    r = _client().get("/probe", headers={"Origin": "http://evil.example"})
    assert r.status_code == 403
    assert r.json() == {"detail": "origin not allowed"}


def test_allows_no_origin():
    assert _client().get("/probe").status_code == 200


def test_allows_same_origin_ip_literal():
    assert _client().get("/probe", headers={"Origin": "http://127.0.0.1:8765"}).status_code == 200


def test_allows_same_origin_localhost():
    client = _client("http://localhost:8765")
    assert client.get("/probe", headers={"Origin": "http://localhost:8765"}).status_code == 200


def test_allows_whitelisted_dev_server():
    assert _client().get("/probe", headers={"Origin": "http://localhost:8080"}).status_code == 200


def test_allows_origin_explicit_default_port():
    # 方案 51 D17：Origin 显式 :80 与 Host 省略形式语义同源，须放行（原误 403）
    r = _client("http://127.0.0.1").get("/probe", headers={"Origin": "http://127.0.0.1:80"})
    assert r.status_code == 200


def test_allows_host_explicit_default_port():
    r = _client().get("/probe", headers={"Origin": "http://127.0.0.1", "host": "127.0.0.1:80"})
    assert r.status_code == 200


def test_allows_same_origin_mixed_case():
    # D17：host 部分大小写不敏感
    client = _client("http://localhost:8765")
    assert client.get("/probe", headers={"Origin": "http://LocalHost:8765"}).status_code == 200


def test_rejects_non_default_port_mismatch():
    # 归一只剥默认端口：:80 与 :8765 仍是不同源，不得因小写化/剥端口放松
    r = _client().get("/probe", headers={"Origin": "http://127.0.0.1:80", "host": "127.0.0.1:8765"})
    assert r.status_code == 403


def test_rejects_dns_rebinding_domain():
    # rebind：Origin 与 Host 同为攻击域名，netloc 相等但 host 非 IP 字面量/localhost → 拒
    client = _client("http://rebind.example:8765")
    assert client.get("/probe", headers={"Origin": "http://rebind.example:8765"}).status_code == 403


_WS_HOST = {"host": "127.0.0.1:8765"}


def test_ws_rejects_foreign_origin():
    with pytest.raises(WebSocketDisconnect) as exc:
        with _client().websocket_connect("/ws", headers={**_WS_HOST, "Origin": "http://evil.example"}):
            pass
    assert exc.value.code == 1008


def test_ws_allows_no_origin():
    with _client().websocket_connect("/ws", headers=dict(_WS_HOST)) as ws:
        assert ws.receive_text() == "hello"


def test_ws_allows_same_origin():
    with _client().websocket_connect(
        "/ws", headers={**_WS_HOST, "Origin": "http://127.0.0.1:8765"}
    ) as ws:
        assert ws.receive_text() == "hello"