#!/usr/bin/env python3
"""
最小参数测试 scrcpy-server
============================

使用最小参数集测试 server 是否能正常工作。
"""

import asyncio
import secrets


async def test_minimal():
    """最小参数测试"""
    device_id = "192.168.8.34:5555"

    # 生成 scid（8 位十六进制）
    scid_num = secrets.randbelow(2**31)
    scid_hex = f"{scid_num:08x}"
    socket_name = f"scrcpy_{scid_hex}"
    local_port = 27195

    print(f"测试参数:")
    print(f"  scid_hex: {scid_hex}")
    print(f"  socket_name: {socket_name}")
    print(f"  local_port: {local_port}")

    # 1. 推送 JAR
    print("\n[1] 推送 scrcpy-server.jar...")
    push = await asyncio.create_subprocess_exec(
        "adb", "-s", device_id, "push",
        "backend/app/scrcpy/scrcpy-server.jar",
        "/data/local/tmp/scrcpy-server.jar",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await push.communicate()
    print(f"  完成")

    # 2. 清理旧的端口转发
    print("\n[2] 清理旧的端口转发...")
    cleanup = await asyncio.create_subprocess_exec(
        "adb", "-s", device_id, "forward", "--remove-all",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await cleanup.communicate()
    print(f"  完成")

    # 3. 设置端口转发
    print("\n[3] 设置端口转发...")
    forward = await asyncio.create_subprocess_exec(
        "adb", "-s", device_id, "forward",
        f"tcp:{local_port}",
        f"localabstract:{socket_name}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await forward.communicate()
    if forward.returncode != 0:
        print(f"  失败: {stderr.decode()}")
        return
    print(f"  成功: tcp:{local_port} -> localabstract:{socket_name}")

    # 4. 启动 scrcpy-server（最小参数）
    print("\n[4] 启动 scrcpy-server（最小参数）...")
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
        "log_level=debug",  # 使用 debug 级别查看详细日志
    ]
    print(f"  命令: {' '.join(cmd)}")

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    print(f"  PID: {process.pid}")

    # 5. 读取 server 输出（10 秒）
    print("\n[5] 读取 server 输出（10 秒）...")

    async def read_output():
        try:
            while True:
                line = await asyncio.wait_for(process.stderr.readline(), timeout=0.5)
                if not line:
                    break
                decoded = line.decode().strip()
                if decoded:
                    print(f"  [server] {decoded}")
        except asyncio.TimeoutError:
            pass
        except Exception as e:
            print(f"  [error] {e}")

    await read_output()

    # 6. 检查进程状态
    if process.returncode is not None:
        print(f"\n[6] Server 已退出，返回码: {process.returncode}")
        stdout = await process.stdout.read()
        if stdout:
            print(f"  stdout: {stdout.decode()}")
    else:
        print(f"\n[6] Server 仍在运行")

        # 7. 尝试连接并读取数据
        print("\n[7] 尝试连接到 socket...")
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", local_port),
                timeout=3.0,
            )
            print(f"  连接成功")

            # 读取设备信息
            print("\n[8] 读取设备信息...")
            device_name_data = await asyncio.wait_for(
                reader.readexactly(64),
                timeout=2.0,
            )
            device_name = device_name_data.decode("utf-8").rstrip("\x00")
            print(f"  设备名: {device_name}")

            device_id_data = await asyncio.wait_for(
                reader.readexactly(32),
                timeout=1.0,
            )
            print(f"  设备ID: {device_id_data.hex()[:32]}...")

            # 尝试读取视频数据
            print("\n[9] 等待视频数据（10 秒）...")
            data_count = 0
            total_bytes = 0
            try:
                while True:
                    chunk = await asyncio.wait_for(
                        reader.read(65536),
                        timeout=2.0,
                    )
                    if not chunk:
                        print(f"  连接关闭")
                        break
                    data_count += 1
                    total_bytes += len(chunk)
                    if data_count == 1:
                        print(f"  收到第一帧: {len(chunk)} 字节")
                        print(f"  前 16 字节: {chunk[:16].hex()}")
                    elif data_count <= 3:
                        print(f"  收到第 {data_count} 帧: {len(chunk)} 字节")
            except asyncio.TimeoutError:
                print(f"  超时，停止等待")

            print(f"\n  总共收到 {data_count} 帧，{total_bytes} 字节")

            writer.close()
            await writer.wait_closed()

        except Exception as e:
            print(f"  连接失败: {e}")

    # 10. 清理
    print("\n[10] 清理...")
    if process.returncode is None:
        process.terminate()
        await process.wait()

    cleanup = await asyncio.create_subprocess_exec(
        "adb", "-s", device_id, "forward", "--remove-all",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await cleanup.communicate()
    print(f"  清理完成")


if __name__ == "__main__":
    asyncio.run(test_minimal())
