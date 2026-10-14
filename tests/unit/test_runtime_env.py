"""GET /api/system/runtime-env 测试（方案 45 T6 运行环境信息透明化）。

Step 0 grep 实证（brief 授权的执行层核对）：
    - SCRCPY_SERVER_VERSION 唯一分发点 scrcpy_version.py 值为 "5.0.1"（无 v 前缀），
      断言与常量同源 import，不做字符串字面量假设；
    - ServerManager 零参可构造，get_local_jar_path() 返回 Path（指向
      backend/app/scrcpy/scrcpy-server.jar），endswith(".jar") 成立；
    - adb 路径用 settings().adb.path（自动检测 tools/adb.exe → "adb" 回退），
      与全仓一致（server_manager.py 同款读取），比「adb.adb_path or "adb"」更真实。
"""

from app.scrcpy.constants import SCRCPY_SERVER_VERSION

from .http_testkit import make_client


def test_runtime_env_returns_basic_fields(monkeypatch):
    monkeypatch.setattr(
        "app.interfaces.http.system._probe_adb_version",
        lambda path: "Android Debug Bridge version 1.0.41",
    )
    res = make_client().get("/api/system/runtime-env")

    assert res.status_code == 200
    body = res.json()
    assert body["scrcpy_server_version"] == SCRCPY_SERVER_VERSION
    assert body["jar_path"].endswith(".jar")
    assert body["adb_path"]  # 非空：tools/adb.exe 自动检测或 "adb" 回退
    assert body["adb_version"] == "Android Debug Bridge version 1.0.41"
    assert isinstance(body["frozen"], bool)


def test_runtime_env_adb_probe_failure_returns_unknown(monkeypatch):
    monkeypatch.setattr("app.interfaces.http.system._probe_adb_version", lambda path: "未知")
    res = make_client().get("/api/system/runtime-env")

    assert res.json()["adb_version"] == "未知"