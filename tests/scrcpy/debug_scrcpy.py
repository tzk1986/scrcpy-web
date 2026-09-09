import asyncio
import struct
import sys
from pathlib import Path

# 使用绝对路径
ADB_PATH = str(Path.cwd() / "tools" / "adb.exe")

async def debug_scrcpy():
    device_id = "192.168.8.22:5555"
    
    print("=== 调试 scrcpy-server 连接 ===")
    
    # 1. 启动 scrcpy-server
    print("\n1. 启动 scrcpy-server...")
    proc = await asyncio.create_subprocess_exec(
        ADB_PATH, "-s", device_id, "shell",
        "CLASSPATH=/data/local/tmp/scrcpy-server.jar",
        "app_process", "/", "com.genymobile.scrcpy.Server", "2.4",
        "max_size=1080", "max_fps=30", "video_codec=h264",
        "video_bit_rate=4000000", "tunnel_forward=true",
        "send_frame_meta=false",
        "raw_stream=true",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    
    await asyncio.sleep(1)
    
    # 检查进程状态
    if proc.returncode is not None:
        stderr = await proc.stderr.read()
        print(f"[X] 启动失败: {stderr.decode()}")
        return
    
    print(f"[OK] PID: {proc.pid}")
    
    # 读取 stderr 输出
    print("\n2. 读取服务器日志...")
    try:
        for _ in range(10):
            line = await asyncio.wait_for(proc.stderr.readline(), timeout=0.5)
            if line:
                print(f"   {line.decode().strip()}")
    except asyncio.TimeoutError:
        pass
    
    # 2. 设置端口转发
    print("\n3. 设置端口转发...")
    fwd = await asyncio.create_subprocess_exec(
        ADB_PATH, "-s", device_id, "forward",
        "tcp:27183", "localabstract:scrcpy_video",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await fwd.wait()
    print(f"[OK] 视频端口转发完成")
    
    fwd2 = await asyncio.create_subprocess_exec(
        ADB_PATH, "-s", device_id, "forward",
        "tcp:27182", "localabstract:scrcpy_control",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await fwd2.wait()
    print(f"[OK] 控制端口转发完成")
    
    # 3. 连接视频 socket
    print("\n4. 连接视频 socket...")
    try:
        video_reader, video_writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", 27183),
            timeout=5.0
        )
        print("[OK] 视频 socket 已连接")
    except Exception as e:
        print(f"[X] 视频 socket 连接失败: {e}")
        return
    
    # 4. 连接控制 socket
    print("\n5. 连接控制 socket...")
    try:
        ctrl_reader, ctrl_writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", 27182),
            timeout=5.0
        )
        print("[OK] 控制 socket 已连接")
    except Exception as e:
        print(f"[X] 控制 socket 连接失败: {e}")
        return
    
    # 5. 读取设备信息
    print("\n6. 读取设备信息...")
    try:
        device_name_data = await asyncio.wait_for(video_reader.readexactly(64), timeout=5.0)
        device_name = device_name_data.decode("utf-8").rstrip("\x00")
        print(f"   设备名称: {device_name}")
        
        dummy_bytes = await asyncio.wait_for(video_reader.readexactly(12), timeout=5.0)
        print(f"   Dummy bytes: {dummy_bytes.hex()}")
    except Exception as e:
        print(f"[X] 读取设备信息失败: {e}")
        return
    
    # 6. 读取 H.264 数据
    print("\n7. 开始读取 H.264 数据...")
    start_time = asyncio.get_event_loop().time()
    total_bytes = 0
    nalu_count = 0
    
    try:
        for i in range(100):
            chunk = await asyncio.wait_for(video_reader.read(65536), timeout=1.0)
            if not chunk:
                print("   连接关闭")
                break
            
            total_bytes += len(chunk)
            nalu_count += 1
            
            if i == 0:
                print(f"   第一块数据: {len(chunk)} 字节")
                print(f"   前 20 字节: {chunk[:20].hex()}")
                
                # 查找 NALU 起始码
                start_code_pos = chunk.find(b'\x00\x00\x00\x01')
                if start_code_pos != -1:
                    print(f"   找到 NALU 起始码位置: {start_code_pos}")
                else:
                    print("   未找到 4 字节 NALU 起始码")
            
            if i % 10 == 0:
                elapsed = asyncio.get_event_loop().time() - start_time
                print(f"   已接收 {nalu_count} 块, {total_bytes} 字节, {elapsed:.1f}s")
                
    except asyncio.TimeoutError:
        print(f"   1 秒内未收到数据")
    except Exception as e:
        print(f"   读取错误: {e}")
    
    elapsed = asyncio.get_event_loop().time() - start_time
    print(f"\n8. 统计信息:")
    print(f"   总字节: {total_bytes}")
    print(f"   读取次数: {nalu_count}")
    print(f"   时间: {elapsed:.1f}s")
    if elapsed > 0:
        print(f"   速率: {total_bytes / elapsed / 1024:.1f} KB/s")
    
    # 清理
    print("\n9. 清理...")
    video_writer.close()
    ctrl_writer.close()
    proc.terminate()
    await proc.wait()
    
    await asyncio.create_subprocess_exec(
        ADB_PATH, "-s", device_id, "forward", "--remove", "tcp:27183"
    )
    await asyncio.create_subprocess_exec(
        ADB_PATH, "-s", device_id, "forward", "--remove", "tcp:27182"
    )
    
    print("\n=== 调试完成 ===")

asyncio.run(debug_scrcpy())
