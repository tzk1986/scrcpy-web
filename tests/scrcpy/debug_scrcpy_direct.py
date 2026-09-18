#!/usr/bin/env python3
"""
手动测试 scrcpy-server 协议
==============================

直接使用 adb shell 启动服务器，并通过 socket 读取数据。
"""

import asyncio
import struct


async def test_scrcpy_protocol():
    """测试 scrcpy 协议"""
    device_id = "192.168.8.34:5555"
    local_port = 27199

    print(f"测试设备: {device_id}")

    # 1. 清理旧的端口转发
    print("\n[1] 清理旧的端口转发...")
    cleanup = await asyncio.create_subprocess_exec(
        "adb", "-s", device_id, "forward", "--remove-all",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await cleanup.communicate()

    # 2. 设置端口转发
    print("[2] 设置端口转发...")
    forward = await asyncio.create_subprocess_exec(
        "adb", "-s", device_id, "forward",
        f"tcp:{local_port}",
        "localabstract:scrcpy",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await forward.communicate()
    if forward.returncode != 0:
        print(f"  失败: {stderr.decode()}")
        return
    print(f"  成功: tcp:{local_port} -> localabstract:scrcpy")

    # 3. 启动 scrcpy-server
    print("[3] 启动 scrcpy-server...")
    cmd = [
        "adb", "-s", device_id, "shell",
        "CLASSPATH=/data/local/tmp/scrcpy-server.jar",
        "app_process", "/",
        "com.genymobile.scrcpy.Server",
        "2.4",
        "log_level=debug",
        "max_size=1080",
        "max_fps=30",
        "video_bit_rate=4000000",
        "video_codec=h264",
        "tunnel_forward=true",
        "send_frame_meta=false",
        "control=true",
        "audio=false",
        "show_touches=false",
        "stay_awake=false",
        "power_off_on_close=false",
        "clipboard_autosync=false",
    ]

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    print(f"  PID: {process.pid}")

    # 4. 读取服务器日志（等待启动）
    print("\n[4] 等待服务器启动...")

    # 读取所有可用的日志
    logs = []
    start_time = asyncio.get_event_loop().time()
    server_ready = False

    while asyncio.get_event_loop().time() - start_time < 5.0:
        if process.returncode is not None:
            # 进程已退出，读取剩余输出
            remaining = await process.stderr.read()
            if remaining:
                for line in remaining.decode().strip().split('\n'):
                    if line.strip():
                        logs.append(line.strip())
                        print(f"  [server] {line.strip()}")
            print(f"\n[错误] 服务器已退出，返回码: {process.returncode}")
            return

        try:
            line = await asyncio.wait_for(process.stderr.readline(), timeout=0.5)
            if line:
                decoded = line.decode().strip()
                if decoded:
                    logs.append(decoded)
                    print(f"  [server] {decoded}")
                    if "Device:" in decoded or "scrcpy" in decoded.lower():
                        server_ready = True
            else:
                # EOF
                break
        except asyncio.TimeoutError:
            continue
        except Exception as e:
            print(f"  读取日志错误: {e}")
            break

    if not server_ready:
        print("  [警告] 未检测到服务器就绪标志")

    # 检查进程状态
    if process.returncode is not None:
        print(f"\n[错误] 服务器已退出，返回码: {process.returncode}")
        return

    print(f"  服务器运行中，PID: {process.pid}")

    # 5. 连接视频 socket
    print("\n[5] 连接视频 socket...")
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", local_port),
            timeout=3.0,
        )
        print("  连接成功")
    except Exception as e:
        print(f"  连接失败: {e}")
        return

    # 6. 连接控制 socket
    print("[6] 连接控制 socket...")
    try:
        _, control_writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", local_port),
            timeout=3.0,
        )
        print("  连接成功")
    except Exception as e:
        print(f"  连接失败: {e}")
        print("  继续尝试...")

    # 7. 检查服务器进程状态
    print("\n[7] 检查服务器状态...")
    if process.returncode is not None:
        print(f"  [错误] 服务器已退出，返回码: {process.returncode}")
        stderr_data = await process.stderr.read()
        if stderr_data:
            print(f"  stderr: {stderr_data.decode()}")
        return
    print(f"  服务器运行中 (PID: {process.pid})")

    # 8. 读取协议数据
    print("\n[8] 读取协议数据...")

    try:
        print("  等待 dummy byte...")
        # 读取 dummy byte
        dummy = await asyncio.wait_for(reader.readexactly(1), timeout=3.0)
        print(f"  Dummy byte: {dummy.hex()}")

        # 读取设备名
        print("  等待设备名 (64 字节)...")
        device_name_data = await asyncio.wait_for(reader.readexactly(64), timeout=3.0)
        device_name = device_name_data.decode("utf-8").rstrip("\x00")
        print(f"  设备名: {device_name}")

        # 读取 codec 名称（4 字节，ASCII 字符串）
        print("  等待 codec 名称 (4 字节)...")
        codec_data = await asyncio.wait_for(reader.readexactly(4), timeout=2.0)
        codec_name = codec_data.decode("ascii")
        print(f"  Codec: {codec_name}")

        # 读取宽度（4 字节，uint32 大端序）
        print("  等待宽度 (4 字节)...")
        width_data = await asyncio.wait_for(reader.readexactly(4), timeout=2.0)
        print(f"  宽度原始数据: {width_data.hex()}")
        width = struct.unpack(">I", width_data)[0]
        print(f"  宽度: {width}")

        # 读取高度（4 字节，uint32 大端序）
        print("  等待高度 (4 字节)...")
        height_data = await asyncio.wait_for(reader.readexactly(4), timeout=2.0)
        print(f"  高度原始数据: {height_data.hex()}")
        height = struct.unpack(">I", height_data)[0]
        print(f"  高度: {height}")

        print(f"\n  ✓ 分辨率: {width}x{height}")

    except asyncio.TimeoutError:
        print("  读取超时")
        # 检查进程状态
        if process.returncode is not None:
            print(f"  服务器已退出，返回码: {process.returncode}")
        return
    except Exception as e:
        print(f"  读取错误: {e}")
        # 检查进程状态
        if process.returncode is not None:
            print(f"  服务器已退出，返回码: {process.returncode}")
        return

    # 9. 读取视频数据
    print("\n[9] 读取视频数据（10秒）...")
    chunk_count = 0
    total_bytes = 0

    try:
        for i in range(100):  # 最多读取 100 个 chunk
            chunk = await asyncio.wait_for(reader.read(65536), timeout=2.0)
            if not chunk:
                print("  连接关闭")
                break

            chunk_count += 1
            total_bytes += len(chunk)

            if chunk_count == 1:
                print(f"  第 1 帧: {len(chunk)} 字节")
                print(f"  前 16 字节: {chunk[:16].hex()}")

                # 检查是否是 H.264 NALU
                if chunk[:4] == b'\x00\x00\x00\x01':
                    print("  ✓ 检测到 NALU 起始码 (00 00 00 01)")
                elif chunk[:3] == b'\x00\x00\x01':
                    print("  ✓ 检测到 NALU 起始码 (00 00 01)")
                else:
                    print(f"  ✗ 未检测到 NALU 起始码")

            elif chunk_count <= 5:
                print(f"  第 {chunk_count} 帧: {len(chunk)} 字节")

    except asyncio.TimeoutError:
        print("  读取超时")
    except Exception as e:
        print(f"  读取错误: {e}")

    print(f"\n  总计: {chunk_count} 帧, {total_bytes} 字节")

    # 10. 清理
    print("\n[10] 清理...")
    try:
        writer.close()
        await writer.wait_closed()
    except:
        pass

    try:
        control_writer.close()
        await control_writer.wait_closed()
    except:
        pass

    process.terminate()
    await process.wait()

    cleanup = await asyncio.create_subprocess_exec(
        "adb", "-s", device_id, "forward", "--remove-all",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await cleanup.communicate()

    print("  完成")


if __name__ == "__main__":
    asyncio.run(test_scrcpy_protocol())
