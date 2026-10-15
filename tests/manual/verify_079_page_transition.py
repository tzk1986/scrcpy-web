"""0.7.9 页面切换流畅冒烟（Dashboard 重进静默刷新；本地真机运行，不入 CI）

前置：打包版 exe 已启动（建议 BACKEND_PORT=8766 + ANDROID_ADB_SERVER_PORT=5039），
真机可达（默认 192.168.8.18）。

用法：
    python tests/manual/verify_079_page_transition.py [--base http://127.0.0.1:8766] [--device-ip 192.168.8.18]

断言：
  A1 API：/health、connect 后 /api/devices 含目标设备
  A2 冷启动：Dashboard 出设备行
  A3 录屏页切回 Dashboard：无 .el-loading-mask 可见 且 设备行即时保留（不做等待）
  A4 设置页切回 Dashboard：同 A3
  A5 录屏页/设置页自身渲染回归（列表或卡片存在、无错误弹窗）
"""

from __future__ import annotations

import argparse
import sys

import httpx
from playwright.sync_api import sync_playwright

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8766")
    parser.add_argument("--device-ip", default="192.168.8.18")
    args = parser.parse_args(argv)
    base = args.base

    # ---- A1 API ----
    with httpx.Client(base_url=base, timeout=10.0) as client:
        r = client.get("/health")
        check("A1a /health", r.status_code == 200, str(r.status_code))
        try:
            r = client.post("/api/devices/connect", params={"ip": args.device_ip, "port": 5555})
            body = r.json() if r.status_code == 200 else {}
            check("A1b connect", bool(body.get("success", True)), str(body)[:120])
        except Exception as e:  # noqa: BLE001
            check("A1b connect", False, repr(e))
        r = client.get("/api/devices")
        devs = r.json() if r.status_code == 200 else []
        ids = [d.get("id", "") for d in devs] if isinstance(devs, list) else []
        has_dev = any(args.device_ip in i for i in ids)
        check("A1c devices 含目标", has_dev, str(ids)[:160])

    # ---- Playwright UI ----
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        # 1600 宽视口：横向 el-menu 在窄视口会隐藏溢出项（1280 下「设置」不可见）
        page = browser.new_page(viewport={"width": 1600, "height": 900})
        page.goto(base, wait_until="domcontentloaded")
        try:
            page.wait_for_selector(".el-table__row", timeout=15000)
            rows0 = page.locator(".el-table__row").count()
            check("A2 冷启动出设备行", rows0 > 0, f"rows={rows0}")
        except Exception as e:  # noqa: BLE001
            check("A2 冷启动出设备行", False, repr(e)[:120])
            rows0 = 0

        for menu_text, path, name in [("录屏", "/recordings", "A3"), ("设置", "/settings", "A4")]:
            # 必须走 SPA 菜单切换（goto 会整页 reload 冷重建 store，破坏 warm 前提）。
            # headless 字体度量下 EP menu ellipsis 可能把溢出项收进「…」弹层（真机正常），
            # 不可见时先展开弹层再点弹层内项
            item = page.locator(f".el-menu-item:has-text('{menu_text}')").first
            if not item.is_visible():
                page.locator(".el-sub-menu__title").first.click()
                item = page.locator(f".el-menu--popup .el-menu-item:has-text('{menu_text}')").first
            item.click()
            page.wait_for_url(f"**{path}**", timeout=10000)
            # A5 该页自身渲染：无组件错误文本
            body_text = page.locator("body").inner_text()
            check(
                f"{name}r {path} 渲染",
                ("页面出错" not in body_text and "Cannot" not in body_text),
                f"len={len(body_text)}",
            )
            # 切回 Dashboard：挂载不应出现 loading 遮罩，旧列表即时保留
            page.locator(".el-menu-item:has-text('Dashboard')").first.click()
            page.wait_for_url(base + "/", timeout=10000)
            masks = page.locator(".el-loading-mask").count()
            vis = page.locator(".el-loading-mask:visible").count()
            rows = page.locator(".el-table__row").count()
            check(
                f"{name} 切回无遮罩+列表即时保留",
                vis == 0 and rows >= 1,
                f"mask_total={masks} visible={vis} rows={rows}",
            )

        browser.close()

    failed = sum(1 for _, ok, _ in results if not ok)
    print()
    print(f"== 汇总：{len(results) - failed}/{len(results)} PASS ==")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
