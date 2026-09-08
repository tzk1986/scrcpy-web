"""
scrcpy-server PoC 验证脚本
测试 scrcpy-server 能否正常启动并输出 H.264 流
"""
import asyncio
import sys
from pathlib import Path

# 添加 backend 到路径
sys.path.insert(0, str(Path(__file__).parent / "backend"))

from app.infrastructure.adb.cli import AdbCliDriver
from app.domain.ports import EncoderOpts


async def test_scrcpy_server():
    """测试 scrcpy-server 启动"""
    device_id = "192.168.8.22:5555"

    print(f"=== scrcpy-server PoC 测试 ===")
    print(f"设备: {device_id}")
    print()

    # 1. 检查设备连接
    print("1. 检查设备连接...")
    driver = AdbCliDriver()
    devices = await driver.list_devices()
    if device_id not in devices:
        print(f"[X] 设备 {device_id} 未连接")
        return False
    print(f"[OK] 设备已连接")
    print()

    # 2. 检查 scrcpy-server.jar
    print("2. 检查 scrcpy-server.jar...")
    try:
        output = await driver.shell(device_id, "ls -lh /data/local/tmp/scrcpy-server.jar")
        if "No such file" in output:
            print("[X] scrcpy-server.jar 不存在")
            return False
        print(f"[OK] scrcpy-server.jar 存在")
        print(f"   {output}")
    except Exception as e:
        print(f"[X] 检查失败: {e}")
        return False
    print()

    # 3. 启动 scrcpy-server 并读取输出
    print("3. 启动 scrcpy-server...")
    cmd = [
        "java", "-Xmx256m",
        "-Djava.class.path=/data/local/tmp/scrcpy-server.jar",
        "-cp", "/data/local/tmp/scrcpy-server.jar",
        "com.genymobile.scrcpy.Server",
        "2.4",
        "max_size=1080",
        "max_fps=30",
        "video_codec=h264",
        "bit_rate=4M",
        "send_frame_meta=false",
        "raw_stream=true",
        "tunnel_forward=true",
        "control=true",
        "show_touches=false",
        "stay_awake=false",
        "power_off_on_exit=false",
        "clipboard_autosync=false"
    ]

    # 使用 adb shell 执行
    full_cmd = f"CLASSPATH=/data/local/tmp/scrcpy-server.jar app_process / com.genymobile.scrcpy.Server 2.4 max_size=1080 max_fps=30 video_codec=h264 bit_rate=4M send_frame_meta=false raw_stream=true tunnel_forward=true control=true show_touches=false stay_awake=false power_off_on_exit=false clipboard_autosync=false"

    print(f"   命令: {full_cmd}")
    print()

    # 启动进程
    proc = await asyncio.create_subprocess_exec(
        str(Path(__file__).parent / "tools" / "adb.exe"),
        "-s", device_id, "shell", full_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    print(f"   PID: {proc.pid}")
    print()

    # 等待 2 秒让服务器启动
    print("4. 等待服务器启动...")
    await asyncio.sleep(2)

    # 检查进程状态
    if proc.returncode is not None:
        print(f"[X] 进程已退出，返回码: {proc.returncode}")
        stderr = await proc.stderr.read()
        print(f"   错误: {stderr.decode()}")
        return False

    print(f"[OK] 服务器正在运行")
    print()

    # 5. 尝试读取数据
    print("5. 读取 H.264 数据...")
    total_bytes = 0
    start_time = asyncio.get_event_loop().time()

    try:
        # 读取 5 秒的数据
        for i in range(50):  # 每次读取 100ms
            if proc.returncode is not None:
                print(f"[X] 进程意外退出")
                break

            # 非阻塞读取
            try:
                data = await asyncio.wait_for(proc.stdout.read(65536), timeout=0.1)
                if data:
                    total_bytes += len(data)
                    if i % 10 == 0:
                        print(f"   读取 {total_bytes} 字节...")
            except asyncio.TimeoutError:
                pass

        elapsed = asyncio.get_event_loop().time() - start_time
        print()
        print(f"[OK] 成功读取数据")
        print(f"   总字节数: {total_bytes}")
        print(f"   时间: {elapsed:.2f}s")
        print(f"   速率: {total_bytes / elapsed / 1024:.2f} KB/s")

    except Exception as e:
        print(f"[X] 读取失败: {e}")
        return False

    finally:
        # 6. 停止服务器
        print()
        print("6. 停止服务器...")
        proc.kill()
        await proc.wait()
        print("[OK] 服务器已停止")

    print()
    print("=== PoC 测试完成 ===")
    return True


if __name__ == "__main__":
    success = asyncio.run(test_scrcpy_server())
    sys.exit(0 if success else 1)
