"""
简单的 scrcpy-server 连接测试
"""
import subprocess
import socket
import time
import sys

def run_adb(cmd):
    """运行 ADB 命令"""
    result = subprocess.run(
        f"tools\\adb.exe -s 192.168.8.22:5555 {cmd}",
        shell=True, capture_output=True, text=True
    )
    return result.returncode, result.stdout, result.stderr

def main():
    print("=== 简单 scrcpy-server 测试 ===\n")

    # 1. 清理旧的端口转发
    print("1. 清理端口转发...")
    run_adb("forward --remove-all")
    time.sleep(0.5)

    # 2. 设置端口转发
    print("2. 设置端口转发...")
    video_port = 27183
    control_port = 27184
    run_adb(f"forward tcp:{video_port} localabstract:scrcpy")
    print(f"   视频端口: {video_port}")
    print(f"   控制端口: {control_port}")

    # 3. 启动 scrcpy-server（使用 raw_stream=true）
    print("\n3. 启动 scrcpy-server...")
    server_cmd = (
        f'tools\\adb.exe -s 192.168.8.22:5555 shell '
        f'CLASSPATH=/data/local/tmp/scrcpy-server.jar '
        f'app_process / com.genymobile.scrcpy.Server 2.4 '
        f'max_size=1080 max_fps=30 video_codec=h264 video_bit_rate=4000000 '
        f'send_frame_meta=false tunnel_forward=true raw_stream=true'
    )

    server_proc = subprocess.Popen(
        server_cmd,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )

    print("   等待 2 秒...")
    time.sleep(2)

    # 检查进程是否还在运行
    if server_proc.poll() is not None:
        print(f"   [ERROR] 服务器已退出，代码: {server_proc.returncode}")
        stdout, stderr = server_proc.communicate()
        print(f"   stdout: {stdout.decode('utf-8', errors='ignore')}")
        print(f"   stderr: {stderr.decode('utf-8', errors='ignore')}")
        return False

    print("   [OK] 服务器正在运行")

    # 4. 连接视频 socket
    print("\n4. 连接视频 socket...")
    try:
        video_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        video_sock.settimeout(5.0)
        video_sock.connect(('127.0.0.1', video_port))
        print(f"   [OK] 连接到端口 {video_port}")
    except Exception as e:
        print(f"   [ERROR] 连接失败: {e}")
        server_proc.terminate()
        return False

    # 5. 读取设备信息
    print("\n5. 读取设备信息...")
    try:
        # 读取设备名称（64 字节）
        print("   读取设备名称...")
        device_name = video_sock.recv(64)
        print(f"   设备名称: {device_name.decode('utf-8', errors='ignore').strip()}")

        # 读取设备 ID（32 字节）
        print("   读取设备 ID...")
        device_id = video_sock.recv(32)
        print(f"   设备 ID: {device_id.decode('utf-8', errors='ignore').strip()}")

    except Exception as e:
        print(f"   [ERROR] 读取失败: {e}")
        video_sock.close()
        server_proc.terminate()
        return False

    # 6. 读取 H.264 数据
    print("\n6. 接收 H.264 数据...")
    video_sock.settimeout(10.0)

    total_bytes = 0
    frame_count = 0
    start_time = time.time()

    try:
        for i in range(30):
            data = video_sock.recv(65536)
            if not data:
                print("   连接关闭")
                break

            total_bytes += len(data)
            frame_count += 1

            if frame_count <= 5 or frame_count % 10 == 0:
                print(f"   帧 {frame_count}: {len(data)} 字节 (累计: {total_bytes} 字节)")

            # 显示前几帧的十六进制数据
            if frame_count <= 3:
                print(f"     前 20 字节: {data[:20].hex()}")

    except socket.timeout:
        print(f"   超时（已接收 {total_bytes} 字节）")
    except Exception as e:
        print(f"   [ERROR] {e}")

    elapsed = time.time() - start_time
    print(f"\n=== 统计 ===")
    print(f"总帧数: {frame_count}")
    print(f"总字节: {total_bytes}")
    print(f"耗时: {elapsed:.2f} 秒")
    if elapsed > 0:
        print(f"平均速率: {total_bytes / elapsed:.2f} 字节/秒")

    if frame_count > 0:
        print(f"\n[SUCCESS] 视频流正常！")
        result = True
    else:
        print(f"\n[FAIL] 未收到视频数据")
        result = False

    # 清理
    video_sock.close()
    server_proc.terminate()
    run_adb("forward --remove-all")

    return result

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
