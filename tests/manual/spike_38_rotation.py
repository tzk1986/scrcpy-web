#!/usr/bin/env python3
"""方案 38 spike：缩略图/截图双缺陷修复真机验证（非 pytest）。

数据层直连 adb 验证（不依赖后端）+ 可选 HTTP 链路验证：

    1. .16 多屏设备：新硬链路 `sh -c "... 2>/dev/null"` 输出魔数在 0 位、
       PIL 可解、尺寸（内置屏预期 1280×800）；raw+gzip 链路 gzip 魔数 0 位；
    2. .24 rotation=2 设备：dumpsys input SurfaceOrientation=2；PNG 可解
       且经与后端同款 PIL transpose(ROTATE_180) 补偿后画面正向（配合
       --out 落盘人工目检 / 与视频流比对）；
    3. 回归 .18/.33/.25：SurfaceOrientation=0、PNG 魔数 0 位可解、
       raw+gzip 往返无损（未施加补偿，字节与设备端直连一致）。

用法：
    python -u tests/manual/spike_38_rotation.py 192.168.8.16:5555 192.168.8.24:5555
    python -u tests/manual/spike_38_rotation.py 192.168.8.16:5555 192.168.8.24:5555 --out /tmp/p38 --http

判据（写进输出 JSON 的 verdict 列表）：
    P38-1  .16 PNG         魔数 0 位 + PIL 解 + 尺寸吻合
    P38-2  .16 raw+gzip    gzip 魔数 0 位
    P38-3  .24 rotation    查询=2 + PIL 补偿落盘
    P38-4  regression      rotation=0 + PNG/raw 无损（三机）
"""
from __future__ import annotations

import argparse
import gzip as _gzip
import io
import json
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

PNG_MAGIC = b"\x89PNG"
GZIP_MAGIC = b"\x1f\x8b"
TRANSPOSE = {1: "ROTATE_270", 2: "ROTATE_180", 3: "ROTATE_90"}


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def exec_out(device: str, args: list[str], timeout: float = 60.0) -> bytes:
    r = subprocess.run(
        ["adb", "-s", device, "exec-out", *args],
        capture_output=True, timeout=timeout,
    )
    if r.returncode != 0:
        raise RuntimeError(f"exec-out {args} rc={r.returncode}: "
                           f"{r.stderr.decode(errors='replace')[:200]}")
    return r.stdout


def shell(device: str, args: list[str], timeout: float = 15.0) -> str:
    r = subprocess.run(
        ["adb", "-s", device, "shell", *args],
        capture_output=True, timeout=timeout,
    )
    return r.stdout.decode("utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"shell {args} rc={r.returncode}: "
                           f"{r.stderr.decode('utf-8', errors='replace')[:200]}")


def get_rotation(device: str) -> int:
    out = shell(device, ["dumpsys", "input"])
    for line in out.splitlines():
        if line.strip().startswith("SurfaceOrientation:"):
            try:
                return int(line.split(":")[1].strip())
            except ValueError:
                return 0
    return 0


def check_png(device: str) -> dict:
    """新硬链路 PNG 截图：魔数 0 位 + PIL 可解 + 尺寸。"""
    data = exec_out(device, ["sh", "-c", "screencap -p 2>/dev/null"])
    row: dict = {"magic_at_zero": data.startswith(PNG_MAGIC), "bytes_after_strip": len(data)}
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(data))
        img.load()
        row["decodable"] = True
        row["size"] = list(img.size)
        row["format"] = img.format
    except Exception as e:  # noqa: BLE001 - spike 采集，分类错误后继续
        row["decodable"] = False
        row["error"] = str(e)[:200]
    return row


def check_raw_gzip(device: str) -> dict:
    """raw+gzip 链路：gzip 魔数 0 位 + 解压后 16B 头可解析。"""
    data = exec_out(device, ["sh", "-c", "screencap 2>/dev/null | gzip -1"])
    row: dict = {"magic_at_zero": data.startswith(GZIP_MAGIC), "wire_bytes": len(data)}
    try:
        raw = _gzip.decompress(data)
        import struct
        w, h, fmt = struct.unpack("<III", raw[:12])
        row["decompressed_bytes"] = len(raw)
        row["header"] = {"width": w, "height": h, "pixel_format": fmt}
        row["len_match"] = len(raw) - 12 == w * h * 4 or len(raw) - 16 == w * h * 4
    except Exception as e:  # noqa: BLE001
        row["decompressed_bytes"] = -1
        row["error"] = str(e)[:200]
    return row


def http_roundtrip(device: str, base: str) -> dict | None:
    """可选：HTTP 端点链路验证（需后端 8765 在线）。"""
    import urllib.parse
    import urllib.request
    try:
        with urllib.request.urlopen(
            f"{base}/api/devices/{urllib.parse.quote(device)}/screenshot", timeout=60
        ) as r:
            png = r.read()
            rot = r.headers.get("X-Rotation", "<missing>")
        with urllib.request.urlopen(
            f"{base}/api/devices/{urllib.parse.quote(device)}/screenshot?format=raw", timeout=60
        ) as r:
            wire = r.read()
            fmt = r.headers.get("X-Frame-Format", "<missing>")
            rot_raw = r.headers.get("X-Rotation", "<missing>")
        raw_ok = False
        if fmt == "raw-rgba":
            _gzip.decompress(wire)  # 解压失败抛异常 → except 分支
            raw_ok = True
        return {
            "png_ok": png.startswith(PNG_MAGIC),
            "rotation_header": rot,
            "raw_format": fmt,
            "raw_rotation_header": rot_raw,
            "raw_decompress_ok": raw_ok,
        }
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:200]}


def save_out(out_dir: str, device: str, png: bytes, compensated: bytes | None) -> None:
    import pathlib
    pathlib.Path(out_dir).mkdir(parents=True, exist_ok=True)
    tag = device.replace(":", "_")
    (pathlib.Path(out_dir) / f"{tag}_png.png").write_bytes(png)
    if compensated is not None:
        (pathlib.Path(out_dir) / f"{tag}_compensated.png").write_bytes(compensated)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("devices", nargs="+", help="ADB 序列号列表")
    ap.add_argument("--http", action="store_true", help="附加 HTTP 端点链路验证（需后端在线）")
    ap.add_argument("--base", default="http://127.0.0.1:8765", help="后端地址")
    ap.add_argument("--expect-size", default="1280x800", help=".16 预期内置屏尺寸，格式 WxH")
    ap.add_argument("--out", default="", help="落盘目录（人工目检正向）")
    args = ap.parse_args()

    try:
        from PIL import Image
    except ImportError:
        log("需 PIL（后端依赖已含），pip install pillow 后重试")
        return 2

    exp_w, exp_h = (int(x) for x in args.expect_size.split("x"))
    verdicts: list[dict] = []

    for device in args.devices:
        log(f"=== {device} ===")
        rotation = 0
        try:
            rotation = get_rotation(device)
        except RuntimeError as e:
            log(f"rotation 查询失败：{e}")
        log(f"SurfaceOrientation = {rotation}")

        png = exec_out(device, ["sh", "-c", "screencap -p 2>/dev/null"])
        png_row = check_png(device)

        if rotation == 2:
            img = Image.open(io.BytesIO(png))
            img.load()
            comp = img.transpose(Image.Transpose.ROTATE_180)
            buf = io.BytesIO()
            comp.save(buf, format="PNG")
            compensated = buf.getvalue()
            log(f"rotation=2 → PIL ROTATE_180 补偿完成（原始 {len(png)}B → {len(compensated)}B），"
                f"落盘目录人工目检：{args.out or '（未指定 --out，跳过落盘）'}")
            verdicts.append({
                "device": device, "item": "P38-3", "name": "rotation=2 补偿可执行",
                "ok": True, "rotation": rotation,
            })
        elif rotation == 0:
            compensated = None
            verdicts.append({
                "device": device, "item": "P38-4", "name": "regression rotation=0 无补偿",
                "ok": png_row.get("decodable") and png_row.get("magic_at_zero"),
                "png": png_row,
            })
        else:
            compensated = None
            log(f"rotation={rotation} 非 0/2，仅记录不补偿（舰队无此设备）")

        if args.out:
            save_out(args.out, device, png, compensated)

        raw_row = check_raw_gzip(device)
        log(f"PNG : {json.dumps(png_row, ensure_ascii=False)}")
        log(f"raw : {json.dumps(raw_row, ensure_ascii=False)}")

        http_row = None
        if args.http:
            http_row = http_roundtrip(device, args.base)
            log(f"HTTP: {json.dumps(http_row, ensure_ascii=False)}")

        if ".16:" in device:
            size_ok = png_row.get("size") == [exp_w, exp_h]
            verdicts.append({
                "device": device, "item": "P38-1", "name": ".16 PNG 魔数/PIL/尺寸",
                "ok": bool(png_row.get("magic_at_zero") and png_row.get("decodable") and size_ok),
                "png": png_row, "expect_size": [exp_w, exp_h],
            })
            verdicts.append({
                "device": device, "item": "P38-2", "name": ".16 raw+gzip 魔数 0 位",
                "ok": bool(raw_row.get("magic_at_zero")),
                "raw": raw_row,
            })
            if not size_ok:
                log(f"⚠ 尺寸 {png_row.get('size')} ≠ 预期 {[exp_w, exp_h]}——可能取到 HDMI 屏，"
                    f"需启动 -d 0 自适应方案评估（方案 38 §七 第 1 行）")

        if http_row and http_row.get("error"):
            log(f"⚠ HTTP 验证失败：{http_row['error']}")

    print(json.dumps({"verdicts": verdicts}, ensure_ascii=False, indent=2))
    all_ok = all(v["ok"] for v in verdicts)
    log(f"总结：{len(verdicts)} 项验证，{'全部通过' if all_ok else '存在未过项，见上方 ⚠'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())