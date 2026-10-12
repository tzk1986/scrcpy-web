#!/usr/bin/env python3
"""方案 37 D6 spike：perf 端点真机实测（非 pytest，不入 CI）。

数据流：打真实 HTTP/WS 端点（需后端在线，`python run_server.py`）——
- POST {base}/api/perf/{device}/start 启采样攒 buffer；
- GET  {base}/api/perf/{device}/export?format=csv|json&source=buffer 导出；
- WS   ws://{base}/ws/perf/{device} 实时帧；
- 现场新鲜输出用 subprocess 直连 adb 采集（dumpsys battery / cpuinfo），
  backend 同款 parse 函数解析，与 WS 帧做真值对照；
- --record-seconds N>0 时走录制落盘（cache 源导出正验；需 metrics.recording
  已临时开启并热重载生效，脚本内轮询记录状态等待，不直接改任何配置文件）。

spike 只验收不改产品码。

判据（结果逐项写入输出 JSON 的 verdicts 列表）：
    S37D-1 buffer CSV：旧 9 列前缀逐项不变 + 新 4 列置尾 + 数据行 13 列非空
    S37D-2 buffer JSON：行恰含 12 键（旧 8 + 新 4），cpu_processes 元素
           proc(str)/user(float)/kernel(float) 形态完整
    S37D-3 WS 帧：cpu_processes 非空 list 且元素三键形态、battery_temp
           float、battery_voltage 数值、battery_health str，旧 8 键俱在
    S37D-4 battery 直出对照：dumpsys battery → parse_battery 温度（摄氏，
           ±1.0℃）/电压（±50mV）/health 串与 WS 末帧一致（不一致重试一次）
    S37D-5 cpuinfo 直出对照：dumpsys cpuinfo → parse_cpuinfo 进程名集合与
           WS 帧 cpu_processes 名集合交集 ≥1（不一致重试一次）
    S37D-6 cache 源导出：--record-seconds N>0 → record/start 轮询等待
           （热重载宽限）→ 采 N 秒 → record/stop → cache csv/json 13 列/
           12 键、行数>0、X-Export-Count 对账；未开该参数 → 负向 404
           NO_DATA 观测（observe，录制开关默认关闭时诚实空库）
    S37D-7 battery health 枚举覆盖率观测（observe）：每台直出 health 码→
           枚举串 + 帧内出现过的枚举串，仅记录不下判（D5 评审「UNKNOWN
           孤立电池条」产品决策证据）

离线降级：设备离线/缺失 → 打印「设备离线」跳过该台（skip，不计 fail）。

用法（仓库根，Git Bash，后端已启动）：
    MSYS_NO_PATHCONV=1 PYTHONPATH=backend python -u tests/manual/spike_37d_perf.py
    MSYS_NO_PATHCONV=1 PYTHONPATH=backend python -u tests/manual/spike_37d_perf.py \
        --devices 192.168.8.18:5555 --cache-only --record-seconds 12

退出码：存在 FAIL 判据 → 1；全 pass/observe/skip → 0；后端不在线 → 2。
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import httpx
from websockets.sync.client import connect

sys.stdout.reconfigure(encoding="utf-8")
# Git Bash 路径转换防御（子进程列参本不经 MSYS，双保险）
os.environ.setdefault("MSYS_NO_PATHCONV", "1")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from app.infrastructure.adb.cli import parse_battery, parse_cpuinfo  # noqa: E402

DEFAULT_DEVICES = ["192.168.8.18:5555", "192.168.8.25:5555"]
# 方案 37 D4 契约：旧 9 列前缀序不变，新 4 列置尾（interfaces/http/performance.py）
OLD_CSV_PREFIX = [
    "ts", "ts_iso", "cpu_percent", "total_memory_mb", "used_memory_mb",
    "fps", "jank_count", "current_activity", "top_package",
]
NEW_CSV_TAIL = ["cpu_processes", "battery_temp", "battery_voltage", "battery_health"]
OLD_JSON_KEYS = [
    "ts", "cpu_percent", "total_memory_mb", "used_memory_mb",
    "fps", "jank_count", "current_activity", "top_package",
]
NEW_JSON_KEYS = ["cpu_processes", "battery_temp", "battery_voltage", "battery_health"]
ALL_JSON_KEYS = OLD_JSON_KEYS + NEW_JSON_KEYS
HTTP_TIMEOUT = 60.0
BATTERY_HEALTH_CODE_RE = re.compile(r"^ *health:\s*(\d+)", re.M)


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


def csv_rows(text: str) -> list[list[str]]:
    """导出响应体 → 行清单（含表头行；空表 → 空清单）。"""
    return list(csv.reader(io.StringIO(text)))


def proc_shape_ok(p: object) -> bool:
    """cpu_processes 元素三键形态：proc str、user/kernel float ≥ 0。"""
    if not isinstance(p, dict):
        return False
    if not isinstance(p.get("proc"), str):
        return False
    try:
        user = float(p["user"])
        kernel = float(p["kernel"])
    except (KeyError, TypeError, ValueError):
        return False
    return user >= 0.0 and kernel >= 0.0


def frame_shape_ok(frame: dict) -> tuple[list[str], str]:
    """WS 帧 12 键形态检查。返回（问题清单, 备注串）。"""
    problems = []
    if not isinstance(frame.get("cpu_processes"), list) or not frame["cpu_processes"]:
        problems.append("cpu_processes 非空 list")
    elif not all(proc_shape_ok(p) for p in frame["cpu_processes"]):
        problems.append("cpu_processes 元素三键形态坏")
    if not isinstance(frame.get("battery_temp"), float):
        problems.append("battery_temp 非 float")
    if not isinstance(frame.get("battery_voltage"), int) and not isinstance(
        frame.get("battery_voltage"), float
    ):
        problems.append("battery_voltage 非数值")
    if not isinstance(frame.get("battery_health"), str):
        problems.append("battery_health 非 str")
    missing = [k for k in OLD_JSON_KEYS if k not in frame]
    if missing:
        problems.append(f"旧 8 键缺失 {missing}")
    note = "" if not problems else "; ".join(problems)
    return problems, note


def battery_direct(device: str) -> tuple[dict, int | None]:
    """现场直采 dumpsys battery（backend 同款 parse 口径）。

    返回 (BatteryInfo 字典化, health 原始数字码或 None)。
    """
    raw = shell(device, ["dumpsys", "battery"])
    info = parse_battery(raw)
    code = None
    m = BATTERY_HEALTH_CODE_RE.search(raw)
    if m:
        code = int(m.group(1))
    return {
        "level": info.level,
        "temperature": info.temperature,
        "voltage": info.voltage,
        "health": info.health,
    }, code


def cpuinfo_direct(device: str) -> list[dict]:
    """现场直采 dumpsys cpuinfo（backend 同款 parse 口径）→ 字典化清单。"""
    raw = shell(device, ["dumpsys", "cpuinfo"])
    return [
        {"proc": p.proc, "user": p.user, "kernel": p.kernel}
        for p in parse_cpuinfo(raw)
    ]


def ws_frames(base: str, device: str, seconds: float) -> list[dict]:
    """连 /ws/perf/{device} 收帧，最长 seconds 秒、最多 12 帧。"""
    url = base.replace("http://", "ws://") + f"/ws/perf/{device}"
    log(f"WS {url}（最多 {seconds:.0f}s / 12 帧）")
    frames: list[dict] = []
    deadline = time.time() + seconds
    with connect(url, open_timeout=15) as ws:
        while time.time() < deadline and len(frames) < 12:
            remain = deadline - time.time()
            if remain <= 0:
                break
            try:
                msg = ws.recv(timeout=min(5.0, remain))
            except TimeoutError:
                break
            frames.append(json.loads(msg))
    return frames


def verdicts_summary(results: list[dict]) -> str:
    by: dict[str, int] = {}
    for r in results:
        by[r["status"]] = by.get(r["status"], 0) + 1
    return f"{len(results)} 项：{by}" if by else "0 项"


def run_runtime(client: httpx.Client, base: str, device: str) -> list[dict]:
    """buffer 导出 + WS 帧 + 直出对照（S37D-1..5、7）。"""
    results: list[dict] = []
    row = {"device": device}

    def fail_all(vid_suffix: str, note: str) -> None:
        for i in range(1, 6):
            results.append({"id": f"S37D-{i}[{device}]", "status": "fail",
                            "note": note, **row})
        results.append({"id": f"S37D-7[{device}]", "status": "observe",
                        "note": f"未观测：{note}", **row})

    try:
        r = client.post(f"{base}/api/perf/{device}/start", params={"interval": 1})
        r.raise_for_status()
        if not r.json().get("success"):
            fail_all("", f"start 响应无 success：{r.json()}")
            return results
        log(f"{device}: /api/perf/start 200，攒 buffer 12s…")
        time.sleep(12)

        # ---- 现场直采（对照基准，取在导出/WS 之前） ----
        live_batt, health_code = battery_direct(device)
        live_procs = cpuinfo_direct(device)

        # ---- S37D-1 / S37D-2：buffer 源导出 ----
        r = client.get(f"{base}/api/perf/{device}/export",
                       params={"format": "csv", "source": "buffer"})
        r.raise_for_status()
        rows = csv_rows(r.text)
        header_ok = rows and rows[0] == OLD_CSV_PREFIX + NEW_CSV_TAIL
        data_ok = len(rows) >= 2 and all(len(c) == 13 for c in rows[1:])
        ok = header_ok and data_ok
        results.append({
            "id": f"S37D-1[{device}]", "status": "pass" if ok else "fail",
            "note": "" if ok else
                f"表头 {rows[0] if rows else '空'} / 数据行数 {len(rows) - 1 if rows else 0}",
            "n_rows": len(rows) - 1 if rows else 0,
            **row,
        })

        r = client.get(f"{base}/api/perf/{device}/export",
                       params={"format": "json", "source": "buffer"})
        r.raise_for_status()
        jrows = r.json()
        last = jrows[-1] if jrows else {}
        keys_ok = isinstance(last, dict) and set(last.keys()) == set(ALL_JSON_KEYS)
        procs = last.get("cpu_processes")
        shape_ok = isinstance(procs, list) and bool(procs) and all(
            proc_shape_ok(p) for p in procs
        )
        ok = bool(jrows) and keys_ok and shape_ok
        results.append({
            "id": f"S37D-2[{device}]", "status": "pass" if ok else "fail",
            "note": "" if ok else
                f"12 键 {keys_ok} / cpu_processes 形态 {shape_ok} / 行数 {len(jrows)}",
            "n_rows": len(jrows),
            **row,
        })

        # ---- S37D-3：WS 帧形态 ----
        frames = ws_frames(base, device, seconds=12.0)
        if not frames:
            results.append({"id": f"S37D-3[{device}]", "status": "fail",
                            "note": "12s 内未收到任何 WS 帧", **row})
            return results
        last_frame = frames[-1]
        problems, note = frame_shape_ok(last_frame)
        results.append({
            "id": f"S37D-3[{device}]", "status": "pass" if not problems else "fail",
            "note": note, "n_frames": len(frames), **row,
        })

        # ---- S37D-4：battery 直出对照（不一致重试一次） ----
        match = (
            isinstance(last_frame.get("battery_temp"), float)
            and isinstance(live_batt["temperature"], float)
            and abs(live_batt["temperature"] - last_frame["battery_temp"]) <= 1.0
            and last_frame.get("battery_health") == live_batt["health"]
            and isinstance(live_batt["voltage"], int)
            and isinstance(last_frame.get("battery_voltage"), int)
            and abs(live_batt["voltage"] - last_frame["battery_voltage"]) <= 50
        )
        if not match:
            log(f"{device} battery 直出与 WS 帧不一致，重试直采一次")
            live_batt, health_code = battery_direct(device)
            match = (
                isinstance(last_frame.get("battery_temp"), float)
                and isinstance(live_batt["temperature"], float)
                and abs(live_batt["temperature"] - last_frame["battery_temp"]) <= 1.0
                and last_frame.get("battery_health") == live_batt["health"]
                and isinstance(live_batt["voltage"], int)
                and isinstance(last_frame.get("battery_voltage"), int)
                and abs(live_batt["voltage"] - last_frame["battery_voltage"]) <= 50
            )
        results.append({
            "id": f"S37D-4[{device}]", "status": "pass" if match else "fail",
            "note": "" if match else
                f"直出 temp={live_batt['temperature']!r}/volt={live_batt['voltage']!r}"
                f"/health={live_batt['health']!r} vs 帧 "
                f"temp={last_frame.get('battery_temp')!r}/"
                f"volt={last_frame.get('battery_voltage')!r}/"
                f"health={last_frame.get('battery_health')!r}",
            "direct": live_batt, "frame": {
                k: last_frame.get(k) for k in
                ("battery_temp", "battery_voltage", "battery_health")
            },
            **row,
        })

        # ---- S37D-5：cpuinfo 直出对照（交集 ≥1；不一致重试一次） ----
        frame_procs = {p.get("proc") for p in last_frame.get("cpu_processes", [])}
        direct_procs = {p["proc"] for p in live_procs}
        common = frame_procs & direct_procs
        if not common:
            log(f"{device} cpuinfo 直出与 WS 帧无交集，重试直采一次")
            live_procs = cpuinfo_direct(device)
            direct_procs = {p["proc"] for p in live_procs}
            common = frame_procs & direct_procs
        results.append({
            "id": f"S37D-5[{device}]", "status": "pass" if common else "fail",
            "note": "" if common else
                f"直出 {len(direct_procs)} 进程与帧 {len(frame_procs)} 进程无交集",
            "n_direct_procs": len(direct_procs),
            "n_frame_procs": len(frame_procs),
            "common_sample": sorted(common)[:5],
            **row,
        })

        # ---- S37D-7：health 枚举覆盖率观测（只记录不下判） ----
        frame_healths = sorted({f.get("battery_health") for f in frames})
        results.append({
            "id": f"S37D-7[{device}]", "status": "observe",
            "note": f"直出 health 码 {health_code} → {live_batt['health']!r}；"
                    f"帧内枚举 {frame_healths}（{len(frames)} 帧）",
            "health_code": health_code,
            "direct_health": live_batt["health"],
            "frame_healths": frame_healths,
            **row,
        })

        # ---- 收尾：停采还原（幂等，失败不追责） ----
        try:
            client.post(f"{base}/api/perf/{device}/stop", timeout=HTTP_TIMEOUT)
        except httpx.HTTPError:
            pass
    except httpx.HTTPError as e:
        err = f"HTTP {getattr(e, 'response', None)}：{e}"
        log(f"{device} 端点失败：{err}")
        fail_all("", err)
    except Exception as e:  # noqa: BLE001 - spike 观测兜底
        log(f"{device} 现场直采/解析失败：{e}")
        fail_all("", f"adb 直采失败：{e}")
    return results


def run_cache(client: httpx.Client, base: str, device: str,
              record_seconds: float) -> list[dict]:
    """cache 源导出（S37D-6）。record_seconds≤0 → 负向 404 观测；>0 → 录制正验。"""
    results: list[dict] = []
    row = {"device": device}
    if record_seconds <= 0:
        r = client.get(f"{base}/api/perf/{device}/export",
                       params={"format": "csv", "source": "cache"})
        if r.status_code == 404:
            detail = ""
            try:
                body = r.json()
                err = body.get("error", {}) if isinstance(body, dict) else {}
                detail = str(err.get("code", "")) or str(err.get("message", ""))
            except ValueError:
                detail = "(非 JSON)"
            status = "observe"
            note = (f"cache 源 404 code={detail!r}"
                    f"（录制未落库时的预期负向）")
        elif r.status_code == 200:
            status, note = "observe", "cache 源 200（库中已有历史行）"
        else:
            status, note = "fail", f"cache 源意外状态码 {r.status_code}"
        results.append({"id": f"S37D-6[{device}]", "status": status, "note": note, **row})
        return results

    # ---- 正验：录制开关已临时开启（热重载轮询等待） ----
    try:
        recording = False
        for attempt in range(15):
            r = client.post(f"{base}/api/perf/{device}/record/start",
                            timeout=HTTP_TIMEOUT)
            body = r.json()
            if r.status_code == 200 and body.get("recording"):
                recording = True
                break
            if r.status_code == 400:
                log(f"{device}: record/start 400（热重载宽限第 {attempt + 1} 次）…")
                time.sleep(2)
                continue
            raise RuntimeError(f"record/start 意外响应 {r.status_code}：{body}")
        if not recording:
            results.append({"id": f"S37D-6[{device}]", "status": "fail",
                            "note": "录制开关 30s 内未生效（metrics.recording 未开或热重载失败）",
                            **row})
            return results
        log(f"{device}: 录制中，采 {record_seconds:.0f}s…")
        time.sleep(record_seconds)

        r = client.post(f"{base}/api/perf/{device}/record/stop", timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        stop_rows = r.json().get("rows", 0)

        r = client.get(f"{base}/api/perf/{device}/export",
                       params={"format": "csv", "source": "cache"})
        r.raise_for_status()
        rows = csv_rows(r.text)
        header_ok = rows and rows[0] == OLD_CSV_PREFIX + NEW_CSV_TAIL
        data = rows[1:] if rows else []
        cols_ok = all(len(c) == 13 for c in data)
        last_procs_ok = False
        if data:
            cell = data[-1][9] if len(data[-1]) >= 10 else ""
            try:
                parsed = json.loads(cell) if cell else None
            except ValueError:
                parsed = None
            last_procs_ok = isinstance(parsed, list) and bool(parsed) and all(
                proc_shape_ok(p) for p in parsed
            )
        count_hdr = r.headers.get("X-Export-Count")
        count_ok = count_hdr is not None and int(count_hdr) == len(data)

        r = client.get(f"{base}/api/perf/{device}/export",
                       params={"format": "json", "source": "cache"})
        r.raise_for_status()
        jrows = r.json()
        jlast = jrows[-1] if jrows else {}
        # 方案 18 旧契约：cache 行含 device_id 键（非 D4 引入），12 指标键为子集断言
        jkeys_ok = isinstance(jlast, dict) and set(jlast.keys()) >= set(ALL_JSON_KEYS)
        extra_keys = (
            sorted(set(jlast.keys()) - set(ALL_JSON_KEYS)) if isinstance(jlast, dict) else []
        )
        jtemp_ok = isinstance(jlast.get("battery_temp"), float)
        jhealth_ok = isinstance(jlast.get("battery_health"), str)

        ok = (
            header_ok and cols_ok and len(data) > 0 and last_procs_ok
            and count_ok and bool(jrows) and jkeys_ok and jtemp_ok and jhealth_ok
        )
        results.append({
            "id": f"S37D-6[{device}]", "status": "pass" if ok else "fail",
            "note": "" if ok else
                f"表头 {header_ok}/13 列 {cols_ok}/数据行 {len(data)}/"
                f"末行 cpu_processes {last_procs_ok}/X-Export-Count {count_ok!s}="
                f"{count_hdr!r}:{len(data)}/json {len(jrows)} 行 12 键 {jkeys_ok}/"
                f"temp {jtemp_ok}/health {jhealth_ok}",
            "extra_json_keys": extra_keys,
            "rows_finalized": stop_rows, "n_rows": len(data),
            "oldest_ts": r.headers.get("X-Export-Oldest-Ts"),
            "newest_ts": r.headers.get("X-Export-Newest-Ts"),
            **row,
        })
    except Exception as e:  # noqa: BLE001 - spike 观测兜底
        log(f"{device} cache 正验失败：{e}")
        results.append({"id": f"S37D-6[{device}]", "status": "fail",
                        "note": str(e), **row})
    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--devices", nargs="+", default=DEFAULT_DEVICES, help="ADB 序列号列表")
    ap.add_argument("--base", default="http://127.0.0.1:8765", help="后端地址")
    ap.add_argument("--record-seconds", type=float, default=0,
                    help="cache 源正验录制秒数（>0 需 metrics.recording 已临时开启）")
    ap.add_argument("--cache-only", action="store_true",
                    help="只跑 cache 源环节（跳过 buffer/WS 段）")
    ap.add_argument("--out", default="", help="结果 JSON 落盘路径（可选）")
    args = ap.parse_args()

    # 后端在线预检（GET /api/devices 轻量探活）
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT) as probe:
            probe.get(f"{args.base}/api/devices")
    except httpx.HTTPError as e:
        log(f"后端不在线或不可达（{e}）——请先启动后端：python run_server.py")
        return 2

    results: list[dict] = []
    with httpx.Client(timeout=HTTP_TIMEOUT) as client:
        for device in args.devices:
            state = device_state(device)
            if state != "device":
                log(f"设备离线：{device}（adb devices 状态 {state!r}）→ 跳过该台")
                for i in range(1, 8):
                    results.append({
                        "id": f"S37D-{i}[{device}]", "status": "skip",
                        "note": f"设备离线（{state}），按 spike 约定跳过，不计 fail",
                        "device": device,
                    })
                continue
            log(f"== {device}（online）开始 ==")
            if not args.cache_only:
                results.extend(run_runtime(client, args.base, device))
            results.extend(run_cache(client, args.base, device, args.record_seconds))

    fail = [r for r in results if r["status"] == "fail"]
    print("\n== 判据汇总 ==", flush=True)
    for r in results:
        print(f"  {r['id']}: {r['status']}" + (f" — {r['note']}" if r.get("note") else ""),
              flush=True)
    log(verdicts_summary(results))

    payload = {
        "verdicts": results,
        "fail": [r["id"] for r in fail],
        "summary": verdicts_summary(results),
    }
    if args.out:
        Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
        log(f"结果已落盘：{args.out}")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())