"""
测试 scrcpy-server 连接和视频数据接收
"""
import socket
import time

def test_connection():
    """测试连接到 scrcpy-server 并读取数据"""
    print("连接到 scrcpy-server...")

    # 连接到本地转发端口
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5.0)

    try:
        sock.connect(('127.0.0.1', 27183))
        print("[OK] 连接成功")

        # 读取设备名称（64 字节）
        print("\n读取设备名称...")
        device_name = sock.recv(64)
        print(f"[OK] 设备名称: {device_name}")

        # 读取设备 ID（32 字节）
        print("\n读取设备 ID...")
        device_id = sock.recv(32)
        print(f"[OK] 设备 ID: {device_id}")

        # 读取 H.264 数据
        print("\n开始接收 H.264 数据...")
        sock.settimeout(10.0)

        total_bytes = 0
        frame_count = 0
        start_time = time.time()

        for i in range(10):  # 读取 10 次
            try:
                data = sock.recv(65536)
                if not data:
                    print("连接关闭")
                    break

                total_bytes += len(data)
                frame_count += 1
                print(f"帧 {frame_count}: 接收 {len(data)} 字节 (累计: {total_bytes} 字节)")

                # 显示前 20 字节（十六进制）
                if len(data) > 0:
                    hex_str = ' '.join(f'{b:02x}' for b in data[:20])
                    print(f"  前 20 字节: {hex_str}")

            except socket.timeout:
                print(f"超时（已接收 {total_bytes} 字节）")
                break

        elapsed = time.time() - start_time
        print(f"\n总计: {frame_count} 帧, {total_bytes} 字节, {elapsed:.2f} 秒")
        if elapsed > 0:
            print(f"平均速率: {total_bytes / elapsed:.2f} 字节/秒")

    except Exception as e:
        print(f"[ERROR] 错误: {e}")
        import traceback
        traceback.print_exc()
    finally:
        sock.close()

if __name__ == "__main__":
    test_connection()
