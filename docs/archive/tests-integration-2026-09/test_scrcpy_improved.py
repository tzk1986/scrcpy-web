"""
改进的 scrcpy-server 测试 - 处理时序问题
"""
import subprocess
import socket
import time

def run_adb(cmd):
    """运行 ADB 命令"""
    result = subprocess.run(
        f"tools\\adb.exe -s 192.168.8.22:5555 {cmd}",
        shell=True, capture_output=True, text=True
    )
    return result.returncode, result.stdout, result.stderr

def main():
    print("=== scrcpy-server 改进测试 ===\n")

    # 清理之前的端口转发
    print("0. 清理旧的端口转发...")
    run_adb("forward --remove-all")
    time.sleep(0.5)

    # 1. 推送 JAR
    print("1. 推送 scrcpy-server.jar...")
    code, out, err = run_adb("push backend\\app\\scrcpy\\scrcpy-server.jar /data/local/tmp/scrcpy-server.jar")
    if code != 0 and "cannot stat" not in err:
        print(f"   警告: {err}")
    print("   完成\n")

    # 2. 设置权限
    print("2. 设置权限...")
    run_adb("shell chmod 755 /data/local/tmp/scrcpy-server.jar")
    print("   完成\n")

    # 3. 设置端口转发（在启动服务器之前）
    print("3. 设置端口转发...")
    run_adb("forward tcp:27183 localabstract:scrcpy")
    print("   完成\n")

    # 4. 启动服务器
    print("4. 启动 scrcpy-server...")
    server_cmd = "tools\\adb.exe -s 192.168.8.22:5555 shell CLASSPATH=/data/local/tmp/scrcpy-server.jar app_process / com.genymobile.scrcpy.Server 2.4 max_size=1080 max_fps=30 video_codec=h264 video_bit_rate=4000000 send_frame_meta=false tunnel_forward=true"
    server_proc = subprocess.Popen(server_cmd, shell=True)
    print("   服务器进程已启动")

    # 5. 等待服务器完全启动
    print("\n5. 等待服务器初始化...")
    print("   等待 3 秒...")
    time.sleep(3)

    # 检查服务器是否还在运行
    if server_proc.poll() is None:
        print("   [OK] 服务器仍在运行")
    else:
        print(f"   [WARN] 服务器已退出，代码: {server_proc.returncode}")
        code, out, err = run_adb("logcat -d -t 30 | grep -i scrcpy")
        print(f"   日志:\n{out}")
        return

    # 6. 检查 socket 是否在监听
    print("\n6. 检查端口转发状态...")
    code, out, err = run_adb("forward --list")
    print(f"   {out.strip()}")

    # 7. 连接 socket
    print("\n7. 连接到 scrcpy socket...")
    max_retries = 3
    for attempt in range(max_retries):
        try:
            print(f"   尝试 {attempt + 1}/{max_retries}...")
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(10.0)
            sock.connect(('127.0.0.1', 27183))
            print("   [OK] 连接成功")
            break
        except Exception as e:
            print(f"   连接失败: {e}")
            if attempt < max_retries - 1:
                print("   等待 1 秒后重试...")
                time.sleep(1)
            else:
                print("   [FAIL] 无法连接到 socket")
                return

    # 8. 读取设备信息
    print("\n8. 读取设备信息...")
    try:
        # 读取设备名称（64 字节）
        print("   读取设备名称（64 字节）...")
        device_name = b""
        while len(device_name) < 64:
            chunk = sock.recv(64 - len(device_name))
            if not chunk:
                print("   [ERROR] 连接关闭")
                return
            device_name += chunk
        print(f"   设备名称: {device_name.decode('utf-8', errors='ignore')}")

        # 读取设备 ID（32 字节）
        print("   读取设备 ID（32 字节）...")
        device_id = b""
        while len(device_id) < 32:
            chunk = sock.recv(32 - len(device_id))
            if not chunk:
                print("   [ERROR] 连接关闭")
                return
            device_id += chunk
        print(f"   设备 ID: {device_id.decode('utf-8', errors='ignore')}")

    except Exception as e:
        print(f"   [ERROR] 读取设备信息失败: {e}")
        import traceback
        traceback.print_exc()
        return

    # 9. 读取 H.264 数据
    print("\n9. 接收 H.264 数据...")
    sock.settimeout(15.0)

    total_bytes = 0
    frame_count = 0
    start_time = time.time()

    try:
        for i in range(30):
            data = sock.recv(65536)
            if not data:
                print("   连接关闭")
                break

            total_bytes += len(data)
            frame_count += 1

            if frame_count <= 5 or frame_count % 10 == 0:
                print(f"   帧 {frame_count}: {len(data)} 字节 (累计: {total_bytes} 字节)")

    except socket.timeout:
        print(f"   超时（已接收 {total_bytes} 字节）")
    except Exception as e:
        print(f"   [ERROR] 接收数据失败: {e}")

    elapsed = time.time() - start_time
    print(f"\n=== 统计 ===")
    print(f"总帧数: {frame_count}")
    print(f"总字节: {total_bytes}")
    print(f"耗时: {elapsed:.2f} 秒")
    if elapsed > 0:
        print(f"平均速率: {total_bytes / elapsed:.2f} 字节/秒")

    if frame_count > 0:
        print(f"\n[SUCCESS] 视频流正常工作！")
    else:
        print(f"\n[FAIL] 未接收到视频数据")

    # 清理
    sock.close()
    server_proc.terminate()
    run_adb("forward --remove-all")
    print("\n清理完成")

if __name__ == "__main__":
    main()
