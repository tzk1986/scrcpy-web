"""
run_server 停机回调契约测试（方案 23 T4 缺陷回归）
=================================================

`_serve` 的停机来源仅依赖 uvicorn Server 实例的 `should_exit` bool 属性
（f7f6a34 曾误用不存在的 request_shutdown，打包复测才暴露——uvicorn 0.52.1
类上实证无 request_* 方法）。本测试 pin 该属性契约：uvicorn 升级若改名
先红于此。
"""

import uvicorn


def test_uvicorn_server_exposes_should_exit():
    config = uvicorn.Config(None, host="127.0.0.1", port=0)
    server = uvicorn.Server(config)
    assert isinstance(server.should_exit, bool)
