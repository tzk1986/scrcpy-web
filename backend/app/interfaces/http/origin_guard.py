"""Origin/Host 守卫中间件（方案 37 A2）——CSRF 面收敛。

任意网页可向 127.0.0.1:8765 发简单请求（无需预检）与 WS 直连；本中间件在
入口按 Origin 裁决：无 Origin（非浏览器）放行；同源限 IP 字面量/localhost
（DNS rebinding 防护）；cors_origins 白名单放行；其余 403 / WS 1008。

ASGI 原生实现（非 BaseHTTPMiddleware）：后者只处理 http scope，websocket
握手直接透传不设防（方案 37 A1 报告 H3，starlette middleware/base.py）。
"""
import ipaddress
from urllib.parse import urlparse

from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import settings

_JSON_403_BODY = b'{"detail": "origin not allowed"}'


class OriginGuardMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        raw_headers = scope.get("headers", [])
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in raw_headers}
        origin = headers.get("origin")
        if origin is None or self._allowed(origin, headers.get("host")):
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            # 未 accept 直接关：握手上即失败（uvicorn 侧呈现为 HTTP 403 拒绝）
            await send({"type": "websocket.close", "code": 1008})
            return
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": _JSON_403_BODY})

    @staticmethod
    def _allowed(origin: str, host: str | None) -> bool:
        if origin in settings().security.cors_origins:
            return True
        if not host:
            return False
        parsed = urlparse(origin)
        # 方案 51 D17：比较前归一——scheme/host 大小写不敏感；显式默认端口
        # （http:80 / https:443）与省略形式语义同源，不归一会误 403 合法请求。
        scheme = parsed.scheme.lower()
        if OriginGuardMiddleware._normalize_authority(parsed.netloc, scheme) != (
            OriginGuardMiddleware._normalize_authority(host, scheme)
        ):
            return False
        # rebind 场景 Origin/Host 同为攻击域名（netloc 相等），此时 host 部分是域名
        # 而非 IP 字面量/localhost → 不采信；局域网 IP 直连访问不受影响。
        return OriginGuardMiddleware._is_ip_or_localhost(parsed.hostname or "")

    _DEFAULT_PORTS = {"http": "80", "https": "443"}

    @classmethod
    def _normalize_authority(cls, authority: str, scheme: str) -> str:
        """小写化并剥离对应 scheme 的显式默认端口。"""
        lowered = authority.lower()
        _, _, port = lowered.rpartition(":")
        if port == cls._DEFAULT_PORTS.get(scheme):
            return lowered[: len(lowered) - len(port) - 1]
        return lowered

    @staticmethod
    def _is_ip_or_localhost(host: str) -> bool:
        if host == "localhost":
            return True
        try:
            ipaddress.ip_address(host)
        except ValueError:
            return False
        return True