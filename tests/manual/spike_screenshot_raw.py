#!/usr/bin/env python3
"""方案 29 spike：截图模式设备端编码链路候选——raw 直读 + 设备端 gzip

对当前设备画面并行测四条链路（同一静态画面，N 次采样）：
    A. screencap -p            （现状：设备端 PNG 编码）
    B. screencap               （raw 裸传：16 字节头 + RGBA_8888）
    C. screencap | gzip -1     （raw + 设备端 gzip 快速档，候选）
    D. screencap | gzip -6     （raw + gzip 高压缩档，参照）

并验证 gzip 往返正确性：解压后 = 4196368 B、16 字节头 w/h/fmt/colorspace
可解析、像素长度 = w*h*4；可选 --verify-channels 用 PIL 解码同帧 PNG 与 raw
逐像素对比（钉死通道顺序，rgba vs bgra、行序）。

用法：
    python -u tests/manual/spike_screenshot_raw.py 192.168.8.18:5555
    python -u tests/manual/spike_screenshot_raw.py 192.168.8.18:5555 -n 5 --verify-channels

判据（写进输出 JSON 的 verdict）：
    GZIP_WORTHY  —— gzip -1 最坏采样 < 800ms 且 < 同帧 PNG 最坏采样
    GZIP_MARGINAL—— gzip -1 < 同帧 PNG 但 ≥ 800ms
    GZIP_UNWORTHY—— gzip -1 ≥ 同帧 PNG
"""
from __future__ import annotations

import argparse
import gzip as _gzip
import json
import statistics
import struct
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

CHAIN_A = ["screencap", "-p"]
CHAIN_B = ["screencap"]
CHAIN_C = ["sh", "-c", "screencap | gzip -1"]
CHAIN_D = ["sh", "-c", "screencap | gzip -6"]


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def exec_out(device: str, args: list[str], timeout: float = 60.0) -> tuple[bytes, float]:
    t0 = time.monotonic()
    r = subprocess.run(
        ["adb", "-s", device, "exec-out", *args],
        capture_output=True, timeout=timeout,
    )
    ms = (time.monotonic() - t0) * 1000
    if r.returncode != 0:
        raise RuntimeError(f"exec-out {args} rc={r.returncode}: {r.stderr.decode(errors='replace')[:200]}")
    return r.stdout, ms


def measure(device: str, name: str, args: list[str], n: int) -> dict:
    times: list[float] = []
    sizes: list[int] = []
    for _ in range(n):
        data, ms = exec_out(device, args)
        times.append(ms)
        sizes.append(len(data))
        time.sleep(0.4)
    row = {
        "chain": name,
        "min_ms": round(min(times)),
        "median_ms": round(statistics.median(times)),
        "max_ms": round(max(times)),
        "bytes": sizes[0],
        "stable_bytes": len(set(sizes)) == 1,
    }
    log(f"{name:16s} min/med/max = {row['min_ms']}/{row['median_ms']}/{row['max_ms']} ms, "
        f"bytes={row['bytes']}{'' if row['stable_bytes'] else ' (画面变动)'}")
    return row


def verify_roundtrip(device: str) -> dict:
    """gzip 链往返：解压 → 头解析 → 长度吻合。"""
    wire, ms = exec_out(device, CHAIN_C)
    t0 = time.monotonic()
    raw = _gzip.decompress(wire)
    dec_ms = (time.monotonic() - t0) * 1000
    w, h, fmt, cs = struct.unpack("<IIII", raw[:16])
    ok = len(raw) - 16 == w * h * 4
    log(f"gzip 往返：wire={len(wire)}B exec={ms:.0f}ms → raw={len(raw)}B decompress={dec_ms:.1f}ms "
        f"header w={w} h={h} fmt={fmt} cs={cs} len_match={ok}")
    return {
        "wire_bytes": len(wire),
        "exec_ms": round(ms),
        "raw_bytes": len(raw),
        "decompress_ms": round(dec_ms, 1),
        "width": w, "height": h, "pixel_format": fmt, "colorspace": cs,
        "len_match": ok,
    }


def verify_channels(device: str) -> dict | None:
    """同帧 PNG 解码 vs raw 逐像素对比（需 PIL；画面须静态）。"""
    try:
        from PIL import Image
    except ImportError:
        log("--verify-channels 需 PIL，跳过")
        return None
    import io

    png, _ = exec_out(device, CHAIN_A)
    wire, _ = exec_out(device, CHAIN_C)
    raw = _gzip.decompress(wire)
    img = Image.open(io.BytesIO(png)).convert("RGBA")
    w, h = img.size
    pix = img.load()

    def raw_px(x: int, y: int) -> tuple[int, int, int, int]:
        i = 16 + (y * w + x) * 4
        return tuple(raw[i:i + 4])  # type: ignore[return-value]

    pts = [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1), (w // 2, h // 2), (w // 3, h // 4), (w // 2, h // 8)]
    results = []
    for x, y in pts:
        p, r_ = pix[x, y], raw_px(x, y)
        verdict = "SAME" if p == r_ else ("SWAP_BR" if (p[0], p[1], p[2]) == (r_[2], r_[1], r_[0]) else "DIFF")
        results.append({"pt": [x, y], "png": list(p), "raw": list(r_), "verdict": verdict})
    all_same = all(r["verdict"] == "SAME" for r in results)
    log(f"通道对照：{len(pts)} 采样点全部 SAME={all_same}")
    return {"all_same": all_same, "samples": results}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("device", help="ADB 序列号，如 192.168.8.18:5555")
    ap.add_argument("-n", type=int, default=3, help="每条链路采样次数")
    ap.add_argument("--verify-channels", action="store_true", help="PNG/raw 逐像素通道对照（需 PIL+静态画面）")
    ap.add_argument("--skip-raw", action="store_true", help="跳过 raw 裸传（慢，4.2MB/次）")
    ap.add_argument("--only", default="", help="只测指定链路，逗号分隔：png,raw,gz1,gz6")
    args = ap.parse_args()

    wanted = {s.strip() for s in args.only.split(",") if s.strip()} or {"png", "raw", "gz1", "gz6"}
    log(f"设备 {args.device}，每条链路 N={args.n}，链路={sorted(wanted)}；请先停在被测画面上")

    rows: list[dict] = []
    if "png" in wanted:
        rows.append(measure(args.device, "png (-p)", CHAIN_A, args.n))
    if "raw" in wanted and not args.skip_raw:
        rows.append(measure(args.device, "raw", CHAIN_B, args.n))
    if "gz1" in wanted:
        rows.append(measure(args.device, "raw|gzip -1", CHAIN_C, args.n))
    if "gz6" in wanted:
        rows.append(measure(args.device, "raw|gzip -6", CHAIN_D, args.n))

    roundtrip = verify_roundtrip(args.device)
    channels = verify_channels(args.device) if args.verify_channels else None

    by_chain = {r["chain"]: r for r in rows}
    gz1, png = by_chain.get("raw|gzip -1"), by_chain.get("png (-p)")
    if gz1 is None or png is None:
        verdict = "PARTIAL"  # 缺 png 或 gz1，无法对照
    elif gz1["median_ms"] < 800 and gz1["median_ms"] < png["median_ms"] and gz1["max_ms"] < png["max_ms"]:
        verdict = "GZIP_WORTHY"
    elif gz1["median_ms"] < png["median_ms"]:
        verdict = "GZIP_MARGINAL"
    else:
        verdict = "GZIP_UNWORTHY"

    print(json.dumps({
        "verdict": verdict,
        "device": args.device,
        "rows": rows,
        "roundtrip": roundtrip,
        "channels": channels,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())