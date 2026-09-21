#!/usr/bin/env python3
"""
直接测试 scrcpy-server socket 数据流
======================================

绕过 WebSocket，直接连接 server socket 查看是否有数据。
"""

import asyncio
import secrets


async def test_raw_socket():
    """直接测试 socket 数据"""
    device_id = "192.168.8.34:5555"

    # 生成较小的 scid（确保不溢出）
    scid_num = secrets.randbelow(0x10000000)  # 限制在 0-268435455
    scid_hex = f"{scid_num:08x}"
    socket_name = f"scrcpy_{scid_hex}"
    local_port = 27196

    print(f"scid: {scid_hex}")
    print(f"socket: {socket_name}")
    print(f"port: {local_port}")

    # 清理
    await asyncio.create_subprocess_exec(
        "adb", "-s", device_id, "forward", "--remove-all",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    # 端口转发
    forward = await asyncio.create_subprocess_exec(
        "adb", "-s", device_id, "forward",
        f"tcp:{local_port}",
        f"localabstract:{socket_name}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await forward.communicate()

    # 启动 server
    print("\n启动 server...")
    cmd = [
        "adb", "-s", device_id, "shell",
        "CLASSPATH=/data/local/tmp/scrcpy-server.jar",
        "app_process", "/",
        "com.genymobile.scrcpy.Server",
        "4.1",
        f"scid={scid_hex}",
        "tunnel_forward=true",
        "video=true",
        "audio=false",
        "control=false",
        "log_level=debug",
    ]

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    # 等待 server 启动
    await asyncio.sleep(2)

    # 读取 server 日志
    async def read_logs():
        try:
            while True:
                line = await asyncio.wait_for(process.stderr.readline(), timeout=0.5)
                if not line:
                    break
                decoded = line.decode().strip()
                if decoded:
                    print(f"[server] {decoded}")
        except:
            pass

    log_task = asyncio.create_task(read_logs())

    # 连接 socket
    print("\n连接 socket...")
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", local_port),
            timeout=3.0,
        )
        print("连接成功")

        # 读取设备信息
        print("\n读取设备信息...")
        device_name = await asyncio.wait_for(reader.readexactly(64), timeout=2.0)
        print(f"设备名: {device_name.decode('utf-8').rstrip(chr(0))}")

        device_id_data = await asyncio.wait_for(reader.readexactly(32), timeout=1.0)
        print(f"设备ID: {device_id_data.hex()[:32]}...")

        # 持续读取数据
        print("\n读取数据流（15 秒）...")
        chunk_count = 0
        total_bytes = 0
        start_time = asyncio.get_event_loop().time()

        while asyncio.get_event_loop().time() - start_time < 15:
            try:
                chunk = await asyncio.wait_for(reader.read(65536), timeout=2.0)
                if not chunk:
                    print("连接关闭")
                    break
                chunk_count += 1
                total_bytes += len(chunk)

                if chunk_count == 1:
                    print(f"第一帧: {len(chunk)} 字节")
                    print(f"前 32 字节: {chunk[:32].hex()}")

                    # 检查是否是 H.264 NALU
                    if chunk[:4] == b'\x00\x00\x00\x01':
                        print("✓ 检测到 NALU 起始码 (00 00 00 01)")
                    elif chunk[:3] == b'\x00\x00\x01':
                        print("✓ 检测到 NALU 起始码 (00 00 01)")
                    else:
                        print(f"✗ 未检测到 NALU 起始码")

                elif chunk_count <= 5:
                    print(f"第 {chunk_count} 帧: {len(chunk)} 字节")

            except asyncio.TimeoutError:
                print("超时，继续等待...")
                continue

        print(f"\n总计: {chunk_count} 帧, {total_bytes} 字节")

        writer.close()
        await writer.wait_closed()

    except Exception as e:
        print(f"错误: {e}")
        import traceback
        traceback.print_exc()

    # 清理
    log_task.cancel()
    process.terminate()
    await process.wait()

    await asyncio.create_subprocess_exec(
        "adb", "-s", device_id, "forward", "--remove-all",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )


if __name__ == "__main__":
    asyncio.run(test_raw_socket())
