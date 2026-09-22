#!/usr/bin/env python3
"""真机编码器稳定性探测（Rockchip 卡死复现 / 参数对照，非 pytest）。

直接对设备启动 scrcpy-server（独立 scid，可与既有会话并存；固定 "scrcpy"
socket 名的现有会话不受影响），按 v4.1 协议读流 N 秒，输出逐秒帧数/字节/
关键帧统计与卡死窗口，用于：

  1. 复现 Rockchip OMX 编码器「进程活着但不产帧」故障（/proc/io 冻结）
  2. 对照实验：不同 max_size / fps / bit_rate 下的稳定性
  3. 验证 RESET_VIDEO 控制消息（type=17）能否在故障/正常态催出关键帧

用法：
    python tests/manual/probe_encoder_stability.py \
        192.168.8.25:5555 --seconds 120 [--max-size 0] [--fps 30] \
        [--bit-rate 4000000] [--reset-at 30] [--tag baseline]

输出：逐秒一行 `t= 12s frames= 29 bytes= 145632 key=1`，
末尾汇总（总帧/关键帧数/最长无帧窗口/关键事件时刻）。
"""

import argparse
import asyncio
import random
import subprocess
import sys
import time
from pathlib import Path

# 脚本独立运行（非 pytest）时补齐 backend/ 与项目根（config 包）导入路径
_ROOT = Path(__file__).resolve().parents[2]
for _p in (_ROOT / "backend", _ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from app.scrcpy.control_sender import ACTION_DOWN, ACTION_MOVE, ControlSender
from app.scrcpy.stream_protocol import MediaEvent, SessionEvent, read_packets

REMOTE_JAR = "/data/local/tmp/scrcpy-server.jar"
LOCAL_JAR = "backend/app/scrcpy/scrcpy-server.jar"
TYPE_RESET_VIDEO = 17


def adb(device: str, *args: str, timeout: float = 15.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["adb", "-s", device, *args],
        capture_output=True, text=True, timeout=timeout,
    )


async def connect_retry(port: int, deadline: float) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """server 监听就绪前重试连接（启动约需 1-3s）。"""
    while True:
        try:
            return await asyncio.open_connection("127.0.0.1", port)
        except OSError:
            if time.monotonic() > deadline:
                raise TimeoutError(f"connect 127.0.0.1:{port} timeout")
            await asyncio.sleep(0.3)


async def wait_device_socket(device: str, sock_name: str, deadline: float) -> float:
    """轮询设备端 /proc/net/unix 等 server 创建 abstract socket 后再连接。

    TCP 连 127.0.0.1:port 只与 adb 本地监听完成握手，不代表设备端 socket
    已存在：server 未就绪时 adb 会立即重置该连接（表现为连接成功却秒 EOF），
    因此必须先等 socket 出现（JVM 启动 1-3s）。
    """
    t_start = time.monotonic()
    while True:
        r = await asyncio.to_thread(
            adb, device, "shell", f"grep {sock_name} /proc/net/unix")
        if r.returncode == 0 and sock_name in r.stdout:
            return time.monotonic() - t_start
        if time.monotonic() > deadline:
            r2 = await asyncio.to_thread(
                adb, device, "shell", "grep -i scrcpy /proc/net/unix")
            raise TimeoutError(
                f"device socket {sock_name} not present on {device}; "
                f"existing: {r2.stdout.strip()!r}")
        await asyncio.sleep(0.3)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("device", help="设备序列号，如 192.168.8.25:5555")
    ap.add_argument("--seconds", type=float, default=120)
    ap.add_argument("--max-size", type=int, default=0)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--bit-rate", type=int, default=4_000_000)
    ap.add_argument("--reset-at", type=float, default=-1,
                    help="在第 N 秒经控制 socket 发送 RESET_VIDEO(type=17)")
    ap.add_argument("--reset-on-stall", type=float, default=-1,
                    help="无帧持续 N 秒后发 RESET_VIDEO（验证卡死态催 IDR），可重复触发")
    ap.add_argument("--wiggle", action="store_true",
                    help="每秒经控制 socket 注入小幅触摸抖动，制造持续画面变化"
                         "（静止屏 scrcpy 不出帧，无法观测稳态 IDR/卡死）")
    ap.add_argument("--tag", default="probe")
    args = ap.parse_args()
    device = args.device

    # scid：server 端 Integer.parseInt(value, 16) 按 hex 解析（31 位非负，
    # scrcpy Options.java:346），socket 名 = scrcpy_%08x。传十进制大数
    # （9-10 位字符的 hex 值溢出 int32）会让 server NumberFormatException
    # 启动即退——表现为连接「秒被重置」而日志为空，极难排查
    scid = random.randint(1, 0x7FFFFFFF)
    scid_arg = f"{scid:x}"
    sock_name = f"scrcpy_{scid:08x}"
    port = random.randint(28000, 28999)

    # 确保 jar 在设备上（server 退出时会自删）
    r = adb(device, "shell", f"ls {REMOTE_JAR}")
    if r.returncode != 0:
        print(f"[{args.tag}] pushing jar...")
        adb(device, "push", LOCAL_JAR, REMOTE_JAR, timeout=60)

    r = adb(device, "forward", f"tcp:{port}", f"localabstract:{sock_name}")
    if r.returncode != 0:
        print(f"[{args.tag}] forward failed: {r.stderr.strip()}")
        return 1

    inner = (
        f"CLASSPATH={REMOTE_JAR} app_process / com.genymobile.scrcpy.Server 4.1 "
        f"log_level=info max_size={args.max_size} max_fps={args.fps} "
        f"video_bit_rate={args.bit_rate} video_codec=h264 tunnel_forward=true "
        f"control=true audio=false scid={scid_arg}"
    )
    print(f"[{args.tag}] device={device} scid={scid_arg} socket={sock_name} "
          f"max_size={args.max_size} fps={args.fps} bit_rate={args.bit_rate} "
          f"seconds={args.seconds}")
    proc = await asyncio.create_subprocess_exec(
        "adb", "-s", device, "shell", inner,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )

    video_writer = control_writer = None
    server_log: list[str] = []

    async def drain_server_log() -> None:
        assert proc.stdout is not None
        while True:
            line = await proc.stdout.readline()
            if not line:
                return
            server_log.append(line.decode(errors="replace").rstrip())

    log_task = asyncio.create_task(drain_server_log())

    try:
        deadline = time.monotonic() + 20
        try:
            wait_s = await wait_device_socket(device, sock_name, deadline)
        except TimeoutError as e:
            print(f"[{args.tag}] {e}")
            await asyncio.sleep(0.5)
            print(f"[{args.tag}] server proc exit_code={proc.returncode}")
            for line in server_log[-20:]:
                print(f"[{args.tag}]   {line}")
            return 1
        print(f"[{args.tag}] device socket ready after {wait_s:.1f}s")
        reader, video_writer = await connect_retry(port, deadline)
        _, control_writer = await connect_retry(port, deadline)
        print(f"[{args.tag}] connected (video + control)")

        # ---- 统计状态 ----
        t0 = time.monotonic()
        cur_frames = cur_bytes = cur_keys = 0
        total_frames = total_keys = 0
        last_frame_at = t0
        max_gap = 0.0
        reset_sent_at = -1.0
        reset_count = 0
        key_times: list[float] = []
        session_seen: list[float] = []
        config_seen: list[float] = []
        screen_size: tuple[int, int] = (0, 0)

        async def consume() -> None:
            nonlocal cur_frames, cur_bytes, cur_keys
            nonlocal total_frames, total_keys
            nonlocal last_frame_at, max_gap, screen_size
            async for ev in read_packets(reader):
                now = time.monotonic()
                if isinstance(ev, SessionEvent):
                    session_seen.append(now - t0)
                    screen_size = (ev.width, ev.height)
                    print(f"[{args.tag}] t={now - t0:6.1f}s SESSION "
                          f"{ev.width}x{ev.height}")
                    continue
                assert isinstance(ev, MediaEvent)
                if ev.is_config:
                    config_seen.append(now - t0)
                    print(f"[{args.tag}] t={now - t0:6.1f}s CONFIG "
                          f"{len(ev.payload)}B")
                    continue
                gap = now - last_frame_at
                if gap > max_gap:
                    max_gap = gap
                last_frame_at = now
                cur_frames += 1
                cur_bytes += len(ev.payload)
                total_frames += 1
                if ev.is_key:
                    cur_keys += 1
                    total_keys += 1
                    key_times.append(now - t0)

        consume_task = asyncio.create_task(consume())

        wiggle_sender: ControlSender | None = None
        wiggle_flip = False
        reset_sent_for_stall = False
        next_tick = t0 + 1.0
        while True:
            now = time.monotonic()
            elapsed = now - t0
            if elapsed >= args.seconds:
                break
            if consume_task.done():
                exc = consume_task.exception()
                if exc is None:
                    print(f"[{args.tag}] consume ended early: stream closed by server")
                else:
                    print(f"[{args.tag}] consume failed: {exc!r}")
                break
            # RESET_VIDEO 定时发送
            if 0 <= args.reset_at <= elapsed and reset_count == 0:
                reset_sent_at = elapsed
                reset_count += 1
                assert control_writer is not None
                control_writer.write(bytes([TYPE_RESET_VIDEO]))
                await control_writer.drain()
                print(f"[{args.tag}] t={elapsed:6.1f}s >>> RESET_VIDEO sent")
            # 卡死/静止探针：无帧 N 秒发一次 RESET_VIDEO，每段 stall 只发一次
            # （帧恢复后 flag 复位，下一段 stall 再发）
            if args.reset_on_stall > 0:
                if (now - last_frame_at) >= args.reset_on_stall:
                    if not reset_sent_for_stall:
                        reset_sent_for_stall = True
                        reset_sent_at = elapsed
                        reset_count += 1
                        assert control_writer is not None
                        control_writer.write(bytes([TYPE_RESET_VIDEO]))
                        await control_writer.drain()
                        print(f"[{args.tag}] t={elapsed:6.1f}s stall="
                              f"{now - last_frame_at:.1f}s >>> RESET_VIDEO #{reset_count}")
                else:
                    reset_sent_for_stall = False
            if now >= next_tick:
                print(f"[{args.tag}] t={elapsed:6.1f}s frames={cur_frames:4d} "
                      f"bytes={cur_bytes:8d} key={cur_keys}", flush=True)
                cur_frames = cur_bytes = cur_keys = 0
                next_tick += 1.0
                # 触摸抖动：制造持续画面变化，触发编码器持续出帧
                if args.wiggle and control_writer is not None and screen_size[0] > 0:
                    cx, cy = screen_size[0] // 2, screen_size[1] // 2
                    if wiggle_sender is None:
                        wiggle_sender = ControlSender(control_writer, screen_size)
                        await wiggle_sender.touch(cx - 40, cy, ACTION_DOWN)
                    wiggle_flip = not wiggle_flip
                    await wiggle_sender.touch(cx + (40 if wiggle_flip else -40),
                                              cy, ACTION_MOVE)
            await asyncio.sleep(0.05)

        elapsed = time.monotonic() - t0
        consume_task.cancel()

        # ---- 汇总 ----
        print(f"\n[{args.tag}] === summary ===")
        print(f"[{args.tag}] duration={elapsed:.1f}s total_frames={total_frames} "
              f"avg_fps={total_frames / elapsed:.2f} total_keys={total_keys} "
              f"max_gap={max_gap:.2f}s")
        if key_times:
            idr = [f"{key_times[0]:.1f}"] + [
                f"{key_times[i] - key_times[i-1]:.1f}"
                for i in range(1, len(key_times))
            ]
            print(f"[{args.tag}] key@ {[f'{t:.1f}' for t in key_times[:20]]}")
            print(f"[{args.tag}] key_gaps {idr[:20]}")
        if reset_sent_at >= 0:
            after = [t for t in key_times if t >= reset_sent_at]
            if after:
                print(f"[{args.tag}] RESET_VIDEO→key latency "
                      f"{after[0] - reset_sent_at:.2f}s (reset at {reset_sent_at:.1f}s)")
            else:
                print(f"[{args.tag}] RESET_VIDEO sent but NO key frame after "
                      f"({elapsed - reset_sent_at:.1f}s elapsed)")
        if server_log:
            print(f"[{args.tag}] --- server log (tail) ---")
            for line in server_log[-15:]:
                print(f"[{args.tag}]   {line}")
        return 0

    finally:
        for w in (video_writer, control_writer):
            if w is not None:
                w.close()
        # 断开后 server 应自行退出；兜底按 scid 精确清理
        # （ps 输出不含参数，须读 /proc/PID/cmdline 才能匹配 scid）
        await asyncio.sleep(1.5)
        r = adb(device, "shell",
                "ps | grep app_process | grep -v grep | "
                "while read u p pp rest; do "
                f"if cat /proc/$p/cmdline 2>/dev/null | grep -q 'scid={scid_arg}'; "
                "then echo $p; kill $p; fi; done")
        for pid in r.stdout.split():
            print(f"[{args.tag}] killed leftover server pid={pid}")
        adb(device, "forward", "--remove", f"tcp:{port}")
        if proc.returncode is None:
            proc.terminate()
        log_task.cancel()
        try:
            await proc.wait()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))