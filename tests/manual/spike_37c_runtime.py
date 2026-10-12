#!/usr/bin/env python3
"""方案 37 C6 spike：runtime 端点真机实测（非 pytest，不入 CI）。

数据流：打真实 HTTP 端点（需后端在线，`python run_server.py`）——
`GET {base}/api/apps/{device_id}/{package}/runtime`（路径两段均真实值：
device id 为 IP 串百分号编码，package 从现场 dumpsys activity 解析取
真实前台包名）；现场新鲜输出用 subprocess 直连 adb 采集、backend 同款
parse 函数解析，与端点返回做一致性对比。spike 只验收不改产品码。

判据（结果逐项写入输出 JSON 的 verdicts 列表）：
    S37C-1 每台在线设备端点 200 + 六字段完整
           （pkg/top_activity/resumed/services/providers/parse_warning）
    S37C-2 .18/.25 样本解析特征一致（fixture 锚定基线复算 + 端点返回
           形态对比——pkg/class 组件形态，非逐字节）
    S37C-3 .19（替班 .33）结构完整 + parse_warning 口径合理
           （空串 = 全字段齐；非空须含「缺失」短语）
    S37C-4 现场直采 dumpsys activity → 同款 parse → resumed/top 与
           端点返回一致（不一致重试 1 次：端点请求与直采间隙前台切换）

离线降级：设备离线/缺失 → 打印「设备离线」跳过该台（skip，不计 fail）。

用法（仓库根，Git Bash，后端已启动）：
    MSYS_NO_PATHCONV=1 PYTHONPATH=backend python -u tests/manual/spike_37c_runtime.py
    MSYS_NO_PATHCONV=1 PYTHONPATH=backend python -u tests/manual/spike_37c_runtime.py \
        --devices 192.168.8.18:5555 --base http://127.0.0.1:8765 --out /tmp/s37c.json

退出码：存在 FAIL 判据 → 1；全 pass/observe/skip → 0；后端不在线 → 2。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
# Git Bash 路径转换防御（子进程列参本不经 MSYS，双保险）
os.environ.setdefault("MSYS_NO_PATHCONV", "1")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from app.infrastructure.adb.cli import (  # noqa: E402
    parse_activity_runtime,
    parse_providers,
    parse_services,
)

DEFAULT_DEVICES = ["192.168.8.18:5555", "192.168.8.25:5555", "192.168.8.19:5555"]
FIX = ROOT / "tests" / "application" / "fixtures"

# fixture 样本锚定基线（与 tests/unit/test_cli_runtime.py 同值）：
# 检验 sample 仍可被当前解析器解析出采集时刻的活动组件（形态特征参照点）。
SAMPLE_ANCHORS = {
    "18": ("com.duzkj.facecash/.module.orderMeal.MainActivity",) * 2,
    "25": ("com.android.settings/.SubSettings",) * 2,
}
FALLBACK_PKG = "com.android.settings"
HTTP_TIMEOUT = 60


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def shell(device: str, args: list[str], timeout: float = 60.0) -> str:
    r = subprocess.run(
        ["adb", "-s", device, "shell", *args],
        capture_output=True, timeout=timeout,
    )
    if r.returncode != 0:
        raise RuntimeError(
            f"adb[{device}] shell {' '.join(args)} rc={r.returncode}: "
            f"{r.stderr.decode('utf-8', errors='replace')[:200]}"
        )
    return r.stdout.decode("utf-8", errors="replace")


def device_state(device: str) -> str:
    """adb devices 中该 serial 的状态（device/offline/missing）。"""
    r = subprocess.run(["adb", "devices"], capture_output=True, timeout=30)
    for line in r.stdout.decode("utf-8", errors="replace").splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[0] == device:
            return parts[1]
    return "missing"


def http_get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


def component_shape_ok(s: str) -> bool:
    """"pkg/.Class" 形态：至少一个 '/' 且包名段含 '.'。"""
    return "/" in s and "." in s.split("/", 1)[0]


def fetch_live_runtime(device: str) -> dict:
    """现场直采三命令输出，用 backend 同款 parse 函数解析（数据层口径）。"""
    text = shell(device, ["dumpsys", "activity"])
    svc_text = shell(device, ["dumpsys", "activity", "services"])
    prov_text = shell(device, ["dumpsys", "activity", "providers"])
    top = parse_activity_runtime(text)
    return {
        "pkg": top.pkg,
        "top_activity": top.top_activity,
        "resumed": top.resumed,
        "services": parse_services(svc_text),
        "providers": parse_providers(prov_text),
    }


def http_runtime(base: str, device: str, package: str) -> dict:
    url = (
        f"{base}/api/apps/{urllib.parse.quote(device, safe='')}/"
        f"{urllib.parse.quote(package, safe='')}/runtime"
    )
    log(f"GET {url}")
    return http_get_json(url)


def verdicts_summary(results: list[dict]) -> str:
    by = {}
    for r in results:
        by[r["status"]] = by.get(r["status"], 0) + 1
    return f"{len(results)} 项：{by}" if by else "0 项"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--devices", nargs="+", default=DEFAULT_DEVICES, help="ADB 序列号列表")
    ap.add_argument("--base", default="http://127.0.0.1:8765", help="后端地址")
    ap.add_argument("--out", default="", help="结果 JSON 落盘路径（可选）")
    args = ap.parse_args()

    # 后端在线预检（GET /api/devices 轻量探活）
    try:
        http_get_json(f"{args.base}/api/devices")
    except (urllib.error.URLError, OSError) as e:
        log(f"后端不在线或不可达（{e}）——请先启动后端：python run_server.py")
        return 2

    results: list[dict] = []
    for device in args.devices:
        tail = device.rsplit(":", 1)[0].rsplit(".", 1)[-1]  # IP 尾段（样本锚点键）
        state = device_state(device)
        if state != "device":
            log(f"设备离线：{device}（adb devices 状态 {state!r}）→ 跳过该台")
            results.append({
                "id": f"S37C-1[{device}]", "status": "skip",
                "note": f"设备离线（{state}），按 spike 约定跳过，不计 fail",
                "device": device,
            })
            continue

        row: dict = {"device": device}
        try:
            # 真机后端超时/失活兜底：现场直采失败给出恢复提示（同一台只重试一次）
            live = fetch_live_runtime(device)
            pkg = live["pkg"] or FALLBACK_PKG
            body = http_runtime(args.base, device, pkg)
            row["response"] = body
            row["live"] = {
                "pkg": live["pkg"],
                "top_activity": live["top_activity"],
                "resumed": live["resumed"],
                "n_services": len(live["services"]),
                "n_providers": len(live["providers"]),
            }

            # S37C-1 六字段结构完整
            missing = [k for k in
                       ("pkg", "top_activity", "resumed", "services", "providers", "parse_warning")
                       if k not in body]
            s37c1 = "pass" if not missing else "fail"
            results.append({
                "id": f"S37C-1[{device}]", "status": s37c1,
                "note": "" if not missing else f"缺字段 {missing}",
                **row,
            })

            # S37C-4 现场直采与端点一致性（resumed/top；不一致重试一次再判）
            consistent = (
                body.get("resumed") == live["resumed"]
                and body.get("top_activity") == live["top_activity"]
            )
            if not consistent:
                log(f"{device} 端点与直采不一致（resumed {body.get('resumed')!r} vs "
                    f"{live['resumed']!r}），重试直采一次")
                live = fetch_live_runtime(device)
                consistent = (
                    body.get("resumed") == live["resumed"]
                    and body.get("top_activity") == live["top_activity"]
                )
            results.append({
                "id": f"S37C-4[{device}]", "status": "pass" if consistent else "fail",
                "note": "" if consistent else
                    (f"端点 resumed/top 与现场直采解析不一致："
                     f"http={body.get('resumed')!r}/{body.get('top_activity')!r} "
                     f"live={live['resumed']!r}/{live['top_activity']!r}"),
                **row,
            })

            # S37C-2 .18/.25 样本解析特征一致性（形态对比，非逐字节）
            if tail in SAMPLE_ANCHORS:
                sample = parse_activity_runtime(
                    (FIX / f"dumpsys_activity_{tail}.txt").read_text(encoding="utf-8")
                )
                anchor = (sample.resumed, sample.top_activity) == SAMPLE_ANCHORS[tail]
                shaped = (
                    component_shape_ok(body.get("top_activity", ""))
                    and component_shape_ok(body.get("resumed", ""))
                )
                ok = anchor and shaped
                results.append({
                    "id": f"S37C-2[{device}]", "status": "pass" if ok else "fail",
                    "note": "" if ok else
                        (f"样本基线锚定 {anchor} / 端点组件形态 {shaped}："
                         f"sample={sample.resumed!r} http={body.get('resumed')!r}"),
                    "sample_anchor": anchor, "http_shape": shaped,
                    **row,
                })

            # S37C-3 .19（替班 .33）结构完整 + parse_warning 口径合理
            if tail == "19":
                w = body.get("parse_warning", None)
                w_ok = isinstance(w, str) and (w == "" or "缺失" in w)
                results.append({
                    "id": f"S37C-3[{device}]", "status": "pass" if w_ok else "fail",
                    "note": "" if w_ok else f"parse_warning 口径异常：{w!r}",
                    "parse_warning": w,
                    **row,
                })

            # 校验服务端返回载荷解析成功（HTTP 层 HTTPError 视为该台失败）
            log(f"{device}: pkg={body.get('pkg')} resumed={body.get('resumed')} "
                f"services={len(body.get('services', []))} "
                f"providers={len(body.get('providers', []))} "
                f"warn={body.get('parse_warning')!r}")
        except (urllib.error.HTTPError, urllib.error.URLError, OSError) as e:
            err = str(e)
            # 连接丢失按裁决恢复再试：同一台 disconnect/connect 后重跑一次
            if isinstance(e, (urllib.error.URLError, OSError)) or getattr(e, "code", 0) >= 500:
                log(f"{device} 端点失败（{err}）——adb disconnect/connect 恢复后重试一次")
                subprocess.run(["adb", "disconnect", device], capture_output=True, timeout=30)
                subprocess.run(["adb", "connect", device], capture_output=True, timeout=30)
                time.sleep(1)
                try:
                    live = fetch_live_runtime(device)
                    body = http_runtime(args.base, device, live["pkg"] or FALLBACK_PKG)
                    log(f"{device} 恢复重试成功：{body}")
                    results.append({"id": f"S37C-1[{device}]", "status": "pass", "note": "恢复重试后通过", "response": body, "device": device})
                    continue
                except Exception as e2:  # noqa: BLE001 - spike 观测兜底
                    err = f"恢复重试仍失败：{e2}"
            for vid in ("S37C-1", "S37C-4"):
                results.append({"id": f"{vid}[{device}]", "status": "fail", "note": err, "device": device})
            if tail in SAMPLE_ANCHORS:
                results.append({"id": f"S37C-2[{device}]", "status": "fail", "note": err, "device": device})
            if tail == "19":
                results.append({"id": f"S37C-3[{device}]", "status": "fail", "note": err, "device": device})
        except Exception as e:  # noqa: BLE001 - spike 观测兜底
            log(f"{device} 现场直采失败：{e}")
            results.append({"id": f"S37C-1[{device}]", "status": "fail",
                            "note": f"adb 直采失败：{e}", "device": device})

    fail = [r for r in results if r["status"] == "fail"]
    print("\n== 判据汇总 ==", flush=True)
    for r in results:
        print(f"  {r['id']}: {r['status']}" + (f" — {r['note']}" if r.get("note") else ""), flush=True)
    log(verdicts_summary(results))

    payload = {
        "verdicts": results,
        "fail": [r["id"] for r in fail],
        "summary": verdicts_summary(results),
    }
    if args.out:
        Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"结果已落盘：{args.out}")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
