"""
正确的 scrcpy 测试 - 作为服务器监听连接
"""
import subprocess
import socket
import time
import threading

def run_adb(cmd):
    result = subprocess.run(
        f"tools\\adb.exe -s 192.168.8.22:5555 {cmd}",
        shell=True, capture_output=True, text=True
    )
    return result.returncode, result.stdout, result.stderr

def main():
    print("=== 正确的 scrcpy 测试（作为服务器监听）===\n")

    # 清理
    print("0. 清理...")
    run_adb("forward --remove-all")
    time.sleep(0.5)

    # 推送 JAR
    print("1. 推送 JAR...")
    run_adb("push backend\\app\\scrcpy\\scrcpy-server.jar /data/local/tmp/scrcpy-server.jar")
    run_adb("shell chmod 755 /data/local/tmp/scrcpy-server.jar")

    # 设置端口转发
    print("2. 设置端口转发...")
    port = 37183  # 使用完全不同的端口
    run_adb(f"forward tcp:{port} localabstract:scrcpy")

    # 创建服务器 socket（监听）
    print("3. 创建服务器 socket...")
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind(('127.0.0.1', port))
    server_sock.listen(1)
    server_sock.settimeout(10.0)
    print(f"   监听 127.0.0.1:{port}")

    # 启动服务器（在后台线程中）
    print("4. 启动 scrcpy-server...")
    server_cmd = "tools\\adb.exe -s 192.168.8.22:5555 shell CLASSPATH=/data/local/tmp/scrcpy-server.jar app_process / com.genymobile.scrcpy.Server 2.4 max_size=1080 max_fps=30 video_codec=h264 video_bit_rate=4000000 send_frame_meta=false tunnel_forward=true"

    def start_server():
        subprocess.run(server_cmd, shell=True)

    server_thread = threading.Thread(target=start_server, daemon=True)
    server_thread.start()

    # 等待连接
    print("5. 等待 scrcpy-server 连接...")
    try:
        client_sock, addr = server_sock.accept()
        print(f"   [OK] 服务器已连接: {addr}")
    except socket.timeout:
        print("   [FAIL] 等待超时")
        server_sock.close()
        return

    client_sock.settimeout(15.0)

    # 读取设备信息
    print("\n6. 读取设备信息...")
    try:
        # 读取设备名称（64 字节）
        print("   读取设备名称...")
        device_name = b""
        while len(device_name) < 64:
            chunk = client_sock.recv(64 - len(device_name))
            if not chunk:
                print("   [ERROR] 连接关闭")
                return
            device_name += chunk
            print(f"   收到 {len(chunk)} 字节，总计 {len(device_name)}/64")
        print(f"   设备名称: {device_name.decode('utf-8', errors='ignore')}")

        # 读取设备 ID（32 字节）
        print("   读取设备 ID...")
        device_id = b""
        while len(device_id) < 32:
            chunk = client_sock.recv(32 - len(device_id))
            if not chunk:
                print("   [ERROR] 连接关闭")
                return
            device_id += chunk
            print(f"   收到 {len(chunk)} 字节，总计 {len(device_id)}/32")
        print(f"   设备 ID: {device_id.decode('utf-8', errors='ignore')}")

    except Exception as e:
        print(f"   [ERROR] 读取失败: {e}")
        import traceback
        traceback.print_exc()
        return

    # 读取 H.264 数据
    print("\n7. 接收 H.264 数据...")
    total_bytes = 0
    frame_count = 0
    start_time = time.time()

    try:
        for i in range(50):
            data = client_sock.recv(65536)
            if not data:
                print("   连接关闭")
                break

            total_bytes += len(data)
            frame_count += 1

            if frame_count <= 5 or frame_count % 10 == 0:
                print(f"   帧 {frame_count}: {len(data)} 字节 (累计: {total_bytes} 字节)")

            # 显示前几帧的数据
            if frame_count <= 3:
                print(f"     前 20 字节: {data[:20].hex()}")

    except socket.timeout:
        print(f"   超时")
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
    else:
        print(f"\n[FAIL] 未收到视频数据")

    # 清理
    client_sock.close()
    server_sock.close()
    run_adb("forward --remove-all")
    print("\n清理完成")

    # 保存结果供后续使用
    if frame_count > 0:
        print("\n视频流成功建立！可以用于集成到应用中。")

if __name__ == "__main__":
    main()
