"""
最简单的 scrcpy 测试 - 只尝试读取数据
"""
import subprocess
import socket
import time
import select

def run_adb(cmd):
    result = subprocess.run(
        f"tools\\adb.exe -s 192.168.8.22:5555 {cmd}",
        shell=True, capture_output=True, text=True
    )
    return result.returncode, result.stdout, result.stderr

def main():
    print("=== 简单 scrcpy 测试 ===\n")

    # 清理
    run_adb("forward --remove-all")
    time.sleep(0.5)

    # 推送 JAR
    print("推送 JAR...")
    run_adb("push backend\\app\\scrcpy\\scrcpy-server.jar /data/local/tmp/scrcpy-server.jar")
    run_adb("shell chmod 755 /data/local/tmp/scrcpy-server.jar")

    # 设置端口转发
    print("设置端口转发...")
    run_adb("forward tcp:27183 localabstract:scrcpy")

    # 启动服务器
    print("启动服务器...")
    server_proc = subprocess.Popen(
        "tools\\adb.exe -s 192.168.8.22:5555 shell CLASSPATH=/data/local/tmp/scrcpy-server.jar app_process / com.genymobile.scrcpy.Server 2.4",
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )

    # 等待
    print("等待 2 秒...")
    time.sleep(2)

    # 检查进程
    if server_proc.poll() is not None:
        print(f"服务器已退出: {server_proc.returncode}")
        out, err = server_proc.communicate()
        print(f"stdout: {out.decode('utf-8', errors='ignore')}")
        print(f"stderr: {err.decode('utf-8', errors='ignore')}")
        return

    print("服务器正在运行")

    # 连接
    print("\n连接 socket...")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5.0)
    sock.connect(('127.0.0.1', 27183))
    print("连接成功")

    # 尝试读取所有可用数据
    print("\n尝试读取数据（10 秒）...")
    sock.settimeout(1.0)

    all_data = b""
    start_time = time.time()

    while time.time() - start_time < 10:
        try:
            # 使用 select 检查是否有数据可读
            readable, _, _ = select.select([sock], [], [], 0.1)
            if readable:
                data = sock.recv(65536)
                if not data:
                    print("连接关闭")
                    break
                all_data += data
                print(f"收到 {len(data)} 字节 (总计: {len(all_data)} 字节)")
                if len(all_data) > 0 and len(all_data) < 200:
                    print(f"  数据: {all_data.hex()}")
            else:
                # 没有数据，检查服务器是否还在运行
                if server_proc.poll() is not None:
                    print("服务器已退出")
                    break
        except socket.timeout:
            continue
        except Exception as e:
            print(f"错误: {e}")
            break

    print(f"\n总计收到: {len(all_data)} 字节")

    if len(all_data) > 0:
        print(f"前 100 字节: {all_data[:100].hex()}")
        print(f"前 100 字节（ASCII）: {all_data[:100]}")
        print("\n[SUCCESS] 收到数据！")
    else:
        print("\n[FAIL] 未收到数据")

    # 检查日志
    print("\n检查服务器日志...")
    code, out, err = run_adb("logcat -d -t 50 | grep -i scrcpy")
    print(out)

    # 清理
    sock.close()
    server_proc.terminate()
    run_adb("forward --remove-all")

if __name__ == "__main__":
    main()
