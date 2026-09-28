"""
系统 HTTP 端点测试（方案 23 T2）
================================

覆盖 POST /api/system/shutdown（优雅停机）：
    - 已注册回调：accepted=true 且回调被触发（should_exit 标志语义，
      响应先于停机送达）
    - 无注册回调（docker 直跑 uvicorn 场景）：accepted=false 不假成功
    - 重复请求幂等：第二次仍 accepted=true 且回调再次触发、无状态残留

回调注册经 launcher 公开 API 往返（不用 monkeypatch 私有变量），
注册表是进程级全局，每用例 try/finally 反注册复位。
"""

from app.core import launcher

from .http_testkit import make_client


def test_shutdown_accepted_when_handler_registered():
    calls: list[bool] = []
    launcher.register_shutdown_handler(lambda: calls.append(True))
    try:
        resp = make_client().post("/api/system/shutdown")
        assert resp.status_code == 200
        assert resp.json() == {"accepted": True, "message": "服务正在退出"}
        assert calls == [True]
    finally:
        launcher.register_shutdown_handler(None)


def test_shutdown_refused_without_handler():
    launcher.register_shutdown_handler(None)
    resp = make_client().post("/api/system/shutdown")
    assert resp.status_code == 200
    assert resp.json() == {
        "accepted": False,
        "message": "当前运行方式未接入在线退出，请手动停止服务",
    }


def test_shutdown_idempotent_double_request():
    calls: list[bool] = []
    launcher.register_shutdown_handler(lambda: calls.append(True))
    try:
        client = make_client()
        for _ in range(2):
            resp = client.post("/api/system/shutdown")
            assert resp.status_code == 200
            assert resp.json()["accepted"] is True
        assert calls == [True, True]
    finally:
        launcher.register_shutdown_handler(None)