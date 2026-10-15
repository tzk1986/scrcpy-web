"""
打包产物半自动验证脚本（方案 15 任务 6，本地人工运行，不入 CI）
================================================================

用法（先启动打包产物 OpenScrcpy.exe，或先运行本脚本拉起）：
    PYTHONPATH=backend:. python tests/manual/verify_packaged_exe.py [--base http://127.0.0.1:8765]

探测项（自动）：/health、/api/devices、SPA 页面（含 /settings/help 手册回退，
方案 47 T6）、根路径、pair 端点参数校验错误路径（方案 47 T4，不触真机）；
报告项（打印复查指引，需人工确认）：data/port.txt/logs 落点、
adb server 随包路径（ANDROID_ADB_SERVER_PORT 隔离法，见方案 §9.5）。
"""

from __future__ import annotations

import argparse
import sys

import httpx

CHECKS: list[tuple[str, str]] = [
    ("GET /health", "/health"),
    ("GET /api/devices", "/api/devices"),
    ("SPA 首页", "/"),
    ("SPA 设置手册页（/settings/help history 回退）", "/settings/help"),
]

# pair 参数校验负例（方案 47 T4）：应在端点层 400 INVALID_PAIR_REQUEST 拦下，
# 不进入 adb 子进程/预检——真机无关，打包产物也可安全探测
PAIR_BADCASES: list[tuple[str, dict[str, str]]] = [
    ("配对码 5 位", {"ip": "192.168.1.5", "port": "37123", "code": "12345"}),
    ("非 IPv4", {"ip": "::1", "port": "37123", "code": "123456"}),
    ("全角数字码", {"ip": "192.168.1.5", "port": "37123", "code": "１２３４５６"}),
    ("端口越界", {"ip": "192.168.1.5", "port": "70000", "code": "123456"}),
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="OpenScrcpy 打包产物验证")
    parser.add_argument("--base", default="http://127.0.0.1:8765")
    args = parser.parse_args(argv)

    failed = 0
    with httpx.Client(base_url=args.base, timeout=5.0) as client:
        for label, path in CHECKS:
            try:
                r = client.get(path)
                ok = r.status_code == 200
            except httpx.HTTPError as e:
                ok = False
                r = e
            print(f"{'PASS' if ok else 'FAIL'}  {label}: {r}")
            failed += 0 if ok else 1

        for label, params in PAIR_BADCASES:
            r = client.post("/api/devices/pair", params=params)
            code = r.json().get("error", {}).get("code") if r.headers.get(
                "content-type", ""
            ).startswith("application/json") else None
            ok = r.status_code == 400 and code == "INVALID_PAIR_REQUEST"
            print(f"{'PASS' if ok else 'FAIL'}  POST /pair {label}: {r.status_code} {code}")
            failed += 0 if ok else 1

    print()
    print("人工复查项（自动探测无法覆盖）：")
    print("  1. 数据落点：exe 同级 data/debug.sqlite 存在（从其他目录启动亦同）")
    print("  2. 单实例：双击第二次 exe 应只开浏览器、不起新进程")
    print("  3. 端口回退：占用 8765 后再启动，port.txt 应为 8766 且浏览器指向新端口")
    print("  4. 日志文件：logs/openscrcpy.log 持续增长且无堆栈异常")
    print("  5. adb 随包路径：ANDROID_ADB_SERVER_PORT=5039 启动后用")
    print("     Get-CimInstance Win32_Process -Filter \"name='adb.exe'\" | Select ExecutablePath")
    print("     新 server 应为 <产物>\\_internal\\tools\\adb.exe")
    print("  6. 真机 .18/.33/.25：视频帧出流、鼠标控制、截图模式回退、debug 会话")
    print("  7. 配对页签：添加设备对话框「无线调试配对」两步渲染；错码回显中文分类文案；")
    print("     .18 真机成功配对为人工验收（需人眼读弹窗码，脚本不覆盖成功路径）")
    return failed


if __name__ == "__main__":
    sys.exit(main())