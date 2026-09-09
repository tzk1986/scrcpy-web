"""
完整的 scrcpy-server 测试脚本
"""
import subprocess
import socket
import time
import sys

def run_adb_command(cmd):
    """运行 ADB 命令"""
    full_cmd = f"tools\\adb.exe -s 192.168.8.22:5555 {cmd}"
    result = subprocess.run(full_cmd, shell=True, capture_output=True, text=True)
    return result.returncode, result.stdout, result.stderr

def main():
    print("=== scrcpy-server 完整测试 ===\n")

    # 1. 推送 JAR 文件
    print("1. 推送 scrcpy-server.jar...")
    code, out, err = run_adb_command("push backend\\app\\scrcpy\\scrcpy-server.jar /data/local/tmp/scrcpy-server.jar")
    if code != 0:
        print(f"   推送失败: {err}")
        return
    print("   [OK] 推送成功\n")

    # 2. 设置权限
    print("2. 设置权限...")
    run_adb_command("shell chmod 755 /data/local/tmp/scrcpy-server.jar")
    print("   [OK] 权限设置完成\n")

    # 3. 创建启动脚本
    print("3. 创建启动脚本...")
    script_content = """#!/system/bin/sh
export CLASSPATH=/data/local/tmp/scrcpy-server.jar
exec app_process / com.genymobile.scrcpy.Server 2.4 max_size=1080 max_fps=30 video_codec=h264 video_bit_rate=4000000 send_frame_meta=false tunnel_forward=true log_level=debug
"""
    run_adb_command(f'shell "cat > /data/local/tmp/start_server.sh << EOF\n{script_content}EOF"')
    run_adb_command("shell chmod +x /data/local/tmp/start_server.sh")
    print("   [OK] 脚本创建完成\n")

    # 4. 设置端口转发
    print("4. 设置端口转发...")
    run_adb_command("forward tcp:27183 localabstract:scrcpy")
    print("   [OK] 端口转发设置完成\n")

    # 5. 启动服务器（后台）
    print("5. 启动 scrcpy-server...")
    server_proc = subprocess.Popen(
        "tools\\adb.exe -s 192.168.8.22:5555 shell /data/local/tmp/start_server.sh",
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    print("   [OK] 服务器已启动（后台）")
    print("   等待 2 秒让服务器初始化...")
    time.sleep(2)

    # 6. 检查服务器进程
    print("\n6. 检查服务器进程...")
    code, out, err = run_adb_command("shell ps -A | grep scrcpy")
    if "scrcpy" in out:
        print(f"   [OK] 服务器进程正在运行")
        print(f"   {out.strip()}\n")
    else:
        print("   [WARN] 未找到 scrcpy 进程，可能已退出")
        # 检查日志
        code, out, err = run_adb_command("logcat -d -t 20 | grep -i scrcpy")
        print(f"   日志: {out}\n")

    # 7. 连接 socket
    print("7. 连接到 scrcpy socket...")
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5.0)
        sock.connect(('127.0.0.1', 27183))
        print("   [OK] 连接成功\n")

        # 8. 读取设备信息
        print("8. 读取设备信息...")
        device_name = sock.recv(64)
        print(f"   设备名称: {device_name}")

        device_id = sock.recv(32)
        print(f"   设备 ID: {device_id}\n")

        # 9. 读取 H.264 数据
        print("9. 接收 H.264 数据...")
        sock.settimeout(10.0)

        total_bytes = 0
        frame_count = 0
        start_time = time.time()

        for i in range(20):
            try:
                data = sock.recv(65536)
                if not data:
                    print("   连接关闭")
                    break

                total_bytes += len(data)
                frame_count += 1

                if frame_count <= 3 or frame_count % 5 == 0:
                    print(f"   帧 {frame_count}: {len(data)} 字节 (累计: {total_bytes} 字节)")

            except socket.timeout:
                print(f"   超时（已接收 {total_bytes} 字节）")
                break

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

        sock.close()

    except Exception as e:
        print(f"   [ERROR] 连接失败: {e}")
        import traceback
        traceback.print_exc()

    # 10. 清理
    print("\n10. 清理...")
    server_proc.terminate()
    run_adb_command("forward --remove tcp:27183")
    print("   [OK] 清理完成")

if __name__ == "__main__":
    main()
