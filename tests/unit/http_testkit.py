"""
interfaces/http 路由测试公共工具
=================================

层：测试工具（非测试用例，pytest 不会收集本文件）。

提供：
    - make_client(): 构造不抛服务端异常的 TestClient（500 由统一异常处理器
      输出 JSON 响应体，否则 TestClient 会直接重抛异常导致无法断言）；
    - override(): 上下文管理器，用 Fake 服务临时覆盖 FastAPI 依赖。
"""

from contextlib import contextmanager
from typing import Any, Iterator

from fastapi.testclient import TestClient

from app.main import app


def make_client() -> TestClient:
    """构造隔离 TestClient；服务端未捕获异常转为 500 响应而非重抛。"""
    return TestClient(app, raise_server_exceptions=False)


@contextmanager
def override(dep: Any, fake: Any) -> Iterator[None]:
    """临时将依赖 dep 覆盖为 fake，退出时清空所有依赖覆盖。"""
    app.dependency_overrides[dep] = lambda: fake
    try:
        yield
    finally:
        app.dependency_overrides.clear()