"""
打包产物半自动验证脚本（方案 15 任务 6，本地人工运行，不入 CI）
================================================================

用法（先启动打包产物 OpenScrcpy.exe，或先运行本脚本拉起）：
    PYTHONPATH=backend:. python tests/manual/verify_packaged_exe.py [--base http://127.0.0.1:8765]

探测项（自动）：/health、/api/devices、SPA 页面、根路径；
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
    return failed


if __name__ == "__main__":
    sys.exit(main())