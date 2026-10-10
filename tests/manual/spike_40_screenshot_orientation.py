#!/usr/bin/env python3
"""方案 40 spike：截图方向根治真机验证（非 pytest）。

数据层直连 adb 验证（不依赖后端）+ 可选 HTTP 链路验证：

    1. .19/.13：dumpsys display 首段 mCurrentOrientation=0（与后端
       cli.py get_rotation 同款解析）→ 无补偿 → 落盘 PNG 原帧 +
       四边亮度条带辅助判向（.13 顶部状态栏极暗应为条带最小侧）；
    2. .24 回归：mCurrentOrientation=2 → ROTATE_180 补偿落盘；
    3. .18/.25/.16 回归：rotation=0、PNG 有效、无补偿；
    3b. 各机判据自洽复核：默认屏 DisplayDeviceInfo 段内
        mCurrentOrientation 与 mOverrideDisplayInfo 行 rotation 字段一致；
    4/5. 浏览器人工清单（无法脚本化）：观看中「截图」按钮方向、
        缩略图缓存链路 —— 输出 JSON 的 manual_steps 供人工执行；
    6. dumpsys display 耗时记录（直连 3 次 + HTTP TTL 命中观察）。

用法：
    python -u tests/manual/spike_40_screenshot_orientation.py \\
        192.168.8.19:5555 192.168.8.13:5555 192.168.8.24:5555 \\
        192.168.8.18:5555 192.168.8.25:5555 192.168.8.16:5555 --out /tmp/p40
    python -u tests/manual/spike_40_screenshot_orientation.py ... --http

判据（写进输出 JSON 的 verdict 列表）：
    P40-1  .19/.13   mCurrentOrientation=0 + 无补偿落盘 + 条带
    P40-2  .24       rotation=2 + ROTATE_180 补偿落盘
    P40-3  regression .18/.25/.16 rotation=0 + PNG 有效
    P40-3b 各机      mCurrentOrientation 与 mOverrideDisplayInfo rotation 一致
    P40-6  dumpsys display 耗时 + HTTP TTL 命中观察
"""
from __future__ import annotations

import argparse
import io
import json
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")

PNG_MAGIC = b"\x89PNG"
TRANSPOSE = {1: "ROTATE_270", 2: "ROTATE_180", 3: "ROTATE_90"}
EDGE_BAND = 8  # 条带宽度（px），四边亮度辅助判向


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def shell(device: str, args: list[str], timeout: float = 30.0) -> str:
    r = subprocess.run(
        ["adb", "-s", device, "shell", *args],
        capture_output=True, timeout=timeout,
    )
    if r.returncode != 0:
        raise RuntimeError(f"shell {args} rc={r.returncode}: "
                           f"{r.stderr.decode('utf-8', errors='replace')[:200]}")
    return r.stdout.decode("utf-8", errors="replace")


def exec_out(device: str, args: list[str], timeout: float = 60.0) -> bytes:
    r = subprocess.run(
        ["adb", "-s", device, "exec-out", *args],
        capture_output=True, timeout=timeout,
    )
    if r.returncode != 0:
        raise RuntimeError(f"exec-out {args} rc={r.returncode}: "
                           f"{r.stderr.decode(errors='replace')[:200]}")
    return r.stdout


def parse_mcurrent_orientation(output: str) -> int | None:
    """与 backend/app/infrastructure/adb/cli.py get_rotation 同款解析。"""
    for line in output.split("\n"):
        if line.strip().startswith("mCurrentOrientation="):
            try:
                return int(line.split("=")[1].strip())
            except ValueError:
                return None
    return None


def first_device_segment(output: str) -> str:
    """按 DisplayDeviceInfo{ 行切段，取首段（默认屏）。"""
    lines = output.split("\n")
    start = next(
        (i for i, line in enumerate(lines)
         if line.strip().startswith("DisplayDeviceInfo{")), None,
    )
    if start is None:
        return ""
    for j in range(start + 1, len(lines)):
        s = lines[j].strip()
        if s.startswith("DisplayDeviceInfo{") or s.startswith("DisplayAdapterInfo"):
            return "\n".join(lines[start:j])
    return "\n".join(lines[start:])


def override_rotation_of_default(output: str) -> int | None:
    """默认屏 mOverrideDisplayInfo 行内 rotation 字段（3b 判据）。

    mOverrideDisplayInfo 位于 logical display dump 区域（DisplayDeviceInfo
    段之外），优先取 displayId 0（新格式）或 uniqueId "local:0"（老格式）
    的行；两者俱缺时退化为首个 mOverrideDisplayInfo 行（logical display 0
    排最前）。
    """
    first: int | None = None
    for line in output.split("\n"):
        if "mOverrideDisplayInfo=DisplayInfo{" not in line:
            continue
        m = re.search(r"rotation\s+(\d+)", line)
        if not m:
            continue
        val = int(m.group(1))
        if first is None:
            first = val
        if "displayId 0" in line or 'uniqueId "local:0"' in line:
            return val
    return first


def seg_device_rotation(segment: str) -> int | None:
    """默认屏 DisplayDeviceInfo 行内 rotation 字段（硬件位姿，仅记录）。"""
    first = segment.split("\n")[0]
    m = re.search(r"rotation\s+(\d+)", first)
    return int(m.group(1)) if m else None


def check_png(data: bytes) -> dict:
    row: dict = {"magic_at_zero": data.startswith(PNG_MAGIC), "bytes": len(data)}
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(data))
        img.load()
        row["decodable"] = True
        row["size"] = list(img.size)
    except Exception as e:  # noqa: BLE001 - spike 采集，分类错误后继续
        row["decodable"] = False
        row["error"] = str(e)[:200]
    return row


def edge_brightness(data: bytes) -> dict[str, float]:
    """四边 8px 条带灰度均值（PIL histogram 实现，辅助判向）。"""
    from PIL import Image
    img = Image.open(io.BytesIO(data)).convert("L")
    w, h = img.size
    crops = {
        "top": img.crop((0, 0, w, EDGE_BAND)),
        "bottom": img.crop((0, h - EDGE_BAND, w, h)),
        "left": img.crop((0, 0, EDGE_BAND, h)),
        "right": img.crop((w - EDGE_BAND, 0, w, h)),
    }
    return {k: band_mean(c) for k, c in crops.items()}


def band_mean(band) -> float:  # noqa: ANN001 - PIL Image 无公开类型导入，spike 脚本
    hist = band.histogram()
    total = sum(hist)
    if not total:
        return 0.0
    return round(sum(i * c for i, c in enumerate(hist)) / total, 1)


def transpose_png(data: bytes, rotation: int) -> bytes:
    from PIL import Image
    img = Image.open(io.BytesIO(data))
    img.load()
    mode = {
        1: Image.Transpose.ROTATE_270,
        2: Image.Transpose.ROTATE_180,
        3: Image.Transpose.ROTATE_90,
    }.get(rotation)
    if mode is None:
        raise ValueError(f"rotation={rotation} 不在补偿映射内")
    comp = img.transpose(mode)
    buf = io.BytesIO()
    comp.save(buf, format="PNG")
    return buf.getvalue()


def save_png(out_dir: str, tag: str, png: bytes) -> None:
    import pathlib
    path = pathlib.Path(out_dir)
    path.mkdir(parents=True, exist_ok=True)
    (path / f"{tag}.png").write_bytes(png)


def time_dumpsys(device: str, runs: int = 3) -> dict:
    """dumpsys display 直连耗时（P40-6 未命中基准，3 次取中位）。"""
    samples: list[float] = []
    for _ in range(runs):
        t0 = time.perf_counter()
        try:
            shell(device, ["dumpsys", "display"], timeout=30.0)
            samples.append((time.perf_counter() - t0) * 1000)
        except RuntimeError:
            break
    if not samples:
        return {"runs": 0, "median_ms": None}
    samples.sort()
    return {
        "runs": len(samples),
        "median_ms": round(samples[len(samples) // 2], 1),
        "all_ms": [round(s, 1) for s in samples],
    }


def http_twice(device: str, base: str) -> list[dict]:
    """HTTP 截图两次调用：X-Rotation 一致性 + 端到端耗时（P40-6 TTL 观察）。"""
    url = f"{base}/api/devices/{urllib.parse.quote(device)}/screenshot"
    rows: list[dict] = []
    try:
        for k in range(2):
            t0 = time.perf_counter()
            with urllib.request.urlopen(url, timeout=90) as r:
                png = r.read()
                rot = r.headers.get("X-Rotation", "<missing>")
            rows.append({
                "call": k + 1,
                "ms": round((time.perf_counter() - t0) * 1000, 1),
                "rotation_header": rot,
                "png_magic_ok": png.startswith(PNG_MAGIC),
            })
            time.sleep(1.0)
        rows.append({
            "call": "consistency",
            "ok": rows[0]["rotation_header"] == rows[1]["rotation_header"],
        })
    except Exception as e:  # noqa: BLE001
        rows.append({"error": str(e)[:200]})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("devices", nargs="+", help="ADB 序列号列表（.33 按用户指示排除）")
    ap.add_argument("--http", action="store_true", help="附加 HTTP 端点验证（需后端在线）")
    ap.add_argument("--base", default="http://127.0.0.1:8765", help="后端地址")
    ap.add_argument("--out", default="", help="落盘目录（人工目检正向）")
    args = ap.parse_args()

    try:
        from PIL import Image  # noqa: F401 - 预检依赖，函数内实际使用处自行导入
    except ImportError:
        log("需 PIL（后端依赖已含），pip install pillow 后重试")
        return 2

    verdicts: list[dict] = []
    for device in args.devices:
        tag = device.replace(":", "_")
        log(f"=== {device} ===")
        dump = shell(device, ["dumpsys", "display"])
        rot = parse_mcurrent_orientation(dump)
        if rot is None:
            log("⚠ mCurrentOrientation 行缺失，解析回退 0（后端同款语义）")
            rot = 0
        log(f"mCurrentOrientation = {rot} → 补偿 {TRANSPOSE.get(rot, '无')}")

        seg = first_device_segment(dump)
        dev_rot = seg_device_rotation(seg)
        override_rot = override_rotation_of_default(dump)
        consistency = override_rot is not None and rot == override_rot
        state3b = ("一致 ✓" if consistency
                   else ("行缺失/未采集" if override_rot is None else "不一致 ✗"))
        log(f"3b 默认屏：DeviceInfo rotation={dev_rot}，"
            f"mOverrideDisplayInfo rotation={override_rot}，"
            f"mCurrentOrientation={rot} → {state3b}")
        verdicts.append({
            "device": device, "item": "P40-3b",
            "name": "mCurrentOrientation 与 mOverrideDisplayInfo rotation 一致",
            "ok": consistency, "rotation": rot,
            "override_rotation": override_rot, "device_rotation": dev_rot,
        })

        png = exec_out(device, ["sh", "-c", "screencap -p 2>/dev/null"])
        png_row = check_png(png)
        edges = edge_brightness(png) if png_row.get("decodable") else {}
        log(f"PNG : {json.dumps({**png_row, 'edges': edges}, ensure_ascii=False)}")

        if rot == 0:
            if args.out:
                save_png(args.out, tag, png)
            verdicts.append({
                "device": device, "item": "P40-3",
                "name": "regression rotation=0 无补偿 + PNG 有效",
                "ok": bool(png_row.get("magic_at_zero") and png_row.get("decodable")),
                "png": png_row, "edges": edges,
            })
        elif rot == 2:
            compensated = transpose_png(png, rot)
            if args.out:
                save_png(args.out, tag, png)
                save_png(args.out, f"{tag}_compensated", compensated)
            log(f"rotation=2 → ROTATE_180 补偿落盘（{len(png)}B → "
                f"{len(compensated)}B），目录：{args.out or '（未指定 --out）'}")
            verdicts.append({
                "device": device, "item": "P40-2",
                "name": "rotation=2 ROTATE_180 补偿可执行",
                "ok": True, "rotation": rot,
            })
        else:
            log(f"rotation={rot} 非 0/2，仅记录不补偿（舰队无此设备）")

        t_row = time_dumpsys(device)
        log(f"dumpsys display 耗时(ms)：{json.dumps(t_row, ensure_ascii=False)}")
        verdicts.append({
            "device": device, "item": "P40-6",
            "name": "dumpsys display 耗时记录",
            "ok": t_row["median_ms"] is not None, **t_row,
        })

        if args.http:
            h_row = http_twice(device, args.base)
            log(f"HTTP: {json.dumps(h_row, ensure_ascii=False)}")

    print(json.dumps({
        "verdicts": verdicts,
        "manual_steps": [
            "P40-4 详情页观看中「截图」按钮 → 下载 PNG 方向与视频流一致（人工比照）",
            "P40-5 缩略图缓存链路：观看 → 回列表 → 缩略图来自流帧"
            "（快照时间 title）；刷新缩略图按钮对无缓存设备走 screencap 兜底",
            "P40-1 目检：{--out}/192.168.8.19_5555.png 与 .13 原帧方向正"
            "（条带数据顶/底对照 .13 顶部状态栏极暗）",
            "P40-2 目检：{--out}/192.168.8.24_5555_compensated.png 正向",
        ],
    }, ensure_ascii=False, indent=2))
    all_ok = all(v["ok"] for v in verdicts)
    log(f"总结：{len(verdicts)} 项验证，{'全部通过' if all_ok else '存在未过项，见上方 ⚠'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())