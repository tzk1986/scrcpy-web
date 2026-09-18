#!/usr/bin/env python3
"""真机 WS 视频流抓包验证脚本（方案 17 实施项 1b/1a 真机验证用，非 pytest）。

连接后端 /ws/video/{device_id}，抓取 N 秒视频流并输出可判读的统计：
    - 帧数/实fps（总均值与逐秒最小值/最大值，识别 0.4fps 类故障）
    - 包边界合法性（每包以起始码开头；1b 协议模式下每包 = 一个 AU）
    - 每帧 NALU 数直方图（检测多 slice 帧）
    - 关键帧数量与间隔
    - 重复载荷次数（检测 repeat-previous-frame 静止重复出帧）
    - config/restarting/error 消息计数（1b 冒烟：config 重发是否正常）
    - 可选：把二进制载荷落盘为 .h264，供 ffmpeg 解码校验

用法：
    python tests/manual/capture_ws_frames.py 192.168.8.18:5555 30
    python tests/manual/capture_ws_frames.py 192.168.8.33:5555 600 --save out.h264
"""

import argparse
import asyncio
import hashlib
import json
import statistics
import sys
import time

from websockets.asyncio.client import connect

START_CODE_4 = b"\x00\x00\x00\x01"
START_CODE_3 = b"\x00\x00\x01"


def count_nalus(data: bytes) -> int:
    """按起始码统计 NALU 数（字节扫描，与前端 extractNalus 语义一致）。"""
    count = 0
    i = 0
    n = len(data)
    while i < n - 3:
        if data[i : i + 4] == START_CODE_4:
            count += 1
            i += 4
        elif data[i : i + 3] == START_CODE_3:
            count += 1
            i += 3
        else:
            i += 1
    return count


def first_nalu_type(data: bytes) -> int | None:
    """取首个 NALU 类型（跳过起始码后的第一字节 & 0x1F）。"""
    if data[:4] == START_CODE_4:
        return data[4] & 0x1F if len(data) > 4 else None
    if data[:3] == START_CODE_3:
        return data[3] & 0x1F if len(data) > 3 else None
    return None


async def capture(device_id: str, duration: float, save_path: str | None) -> int:
    url = f"ws://127.0.0.1:8765/ws/video/{device_id}"
    print(f"[capture] connecting {url} for {duration:.0f}s")

    frame_count = 0
    frame_bytes = 0
    key_frames = 0
    bad_boundary = 0
    nalu_histogram: dict[int, int] = {}
    payload_hashes: list[str] = []
    duplicate_frames = 0
    per_second: dict[int, int] = {}
    config_count = 0
    restarting_count = 0
    error_msgs: list[str] = []
    keyframe_seconds: list[float] = []
    started = time.monotonic()
    config_info: dict | None = None
    out = open(save_path, "wb") if save_path else None

    try:
        async with connect(url, max_size=None) as ws:
            while True:
                remaining = duration - (time.monotonic() - started)
                if remaining <= 0:
                    break
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=remaining)
                except asyncio.TimeoutError:
                    break

                now = time.monotonic() - started
                if isinstance(msg, str):
                    try:
                        j = json.loads(msg)
                    except json.JSONDecodeError:
                        continue
                    mtype = j.get("type")
                    if mtype == "config":
                        config_count += 1
                        config_info = {
                            "codec": j.get("codec"),
                            "width": j.get("width"),
                            "height": j.get("height"),
                            "desc_len": len(j.get("description") or ""),
                        }
                        print(f"[capture] config#{config_count}: {config_info}")
                    elif mtype == "restarting":
                        restarting_count += 1
                        print(f"[capture] restarting: bit_rate={j.get('bit_rate')}")
                    elif mtype == "error":
                        error_msgs.append(str(j.get("message")))
                        print(f"[capture] error: {j.get('message')}")
                else:
                    frame_count += 1
                    frame_bytes += len(msg)
                    if out:
                        out.write(msg)
                    sec = int(now)
                    per_second[sec] = per_second.get(sec, 0) + 1

                    if not (msg[:4] == START_CODE_4 or msg[:3] == START_CODE_3):
                        bad_boundary += 1
                    n = count_nalus(msg)
                    nalu_histogram[n] = nalu_histogram.get(n, 0) + 1
                    if first_nalu_type(msg) == 5:
                        key_frames += 1
                        keyframe_seconds.append(round(now, 2))

                    h = hashlib.md5(msg).hexdigest()
                    if payload_hashes and payload_hashes[-1] == h:
                        duplicate_frames += 1
                    payload_hashes.append(h)

                    if frame_count <= 3:
                        print(
                            f"[capture] frame#{frame_count} t={now:.2f}s "
                            f"size={len(msg)} nalus={n} first_type={first_nalu_type(msg)}"
                        )
    finally:
        if out:
            out.close()

    elapsed = time.monotonic() - started
    counts = sorted(per_second.values())
    print("\n===== 结果 =====")
    print(f"device={device_id}")
    print(f"时长={elapsed:.1f}s 帧数={frame_count} 关键帧={key_frames} 总字节={frame_bytes}")
    print(f"平均fps={frame_count / elapsed:.2f}")
    if counts:
        print(
            f"逐秒帧数: min={counts[0]} p50={statistics.median(counts):.0f} "
            f"max={counts[-1]} 秒数={len(counts)} 零帧秒数={sum(1 for c in counts if c == 0)}"
        )
    print(f"起始码非法包={bad_boundary}")
    print(f"每帧NALU数直方图={dict(sorted(nalu_histogram.items()))}")
    print(f"相邻重复载荷次数={duplicate_frames}")
    print(f"config次数={config_count} restarting次数={restarting_count} error次数={len(error_msgs)}")
    if keyframe_seconds:
        gaps = [
            round(keyframe_seconds[i + 1] - keyframe_seconds[i], 2)
            for i in range(len(keyframe_seconds) - 1)
        ]
        print(f"关键帧间隔(秒)={gaps[:20]}{'...' if len(gaps) > 20 else ''}")
    if error_msgs:
        print(f"errors={error_msgs[:5]}")
    if save_path:
        print(f"落盘: {save_path}")

    # 判定（1b 转正条件的关键项）
    ok = True
    if frame_count == 0:
        ok = False
        print("[fail] 未收到任何帧")
    if bad_boundary:
        ok = False
        print(f"[fail] {bad_boundary} 个包不以起始码开头（帧序/边界异常）")
    if config_count == 0:
        ok = False
        print("[fail] 未收到 config")
    if error_msgs:
        print("[warn] 存在服务端 error 消息")
    print("[pass] 基础判读通过" if ok else "[fail] 存在基础判读失败项")
    return 0 if ok else 1


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("device_id", help="ADB serial，如 192.168.8.18:5555")
    p.add_argument("duration", type=float, help="抓取秒数")
    p.add_argument("--save", default=None, help="可选：二进制载荷落盘路径（.h264）")
    args = p.parse_args()
    return asyncio.run(capture(args.device_id, args.duration, args.save))


if __name__ == "__main__":
    sys.exit(main())