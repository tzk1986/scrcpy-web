"""
多设备 WebSocket 视频流测试
============================

测试两台设备并发通过 WebSocket 传输视频流。
"""
import asyncio
import sys
import json
from pathlib import Path

# 添加 backend 到路径
sys.path.insert(0, str(Path(__file__).parent / "backend"))

try:
    import websockets
except ImportError:
    print("需要安装 websockets: pip install websockets")
    sys.exit(1)


async def test_single_device(device_id: str, duration: int = 10):
    """测试单个设备的 WebSocket 视频流"""
    ws_url = f"ws://localhost:8000/ws/video/{device_id}"
    print(f"[{device_id}] 连接到 WebSocket...")

    frame_count = 0
    total_bytes = 0

    try:
        async with websockets.connect(ws_url) as ws:
            print(f"[{device_id}] 连接成功")

            start_time = asyncio.get_event_loop().time()

            while asyncio.get_event_loop().time() - start_time < duration:
                try:
                    data = await asyncio.wait_for(ws.recv(), timeout=2)

                    if isinstance(data, bytes):
                        frame_count += 1
                        total_bytes += len(data)

                        if frame_count <= 3 or frame_count % 50 == 0:
                            print(f"[{device_id}] 帧 {frame_count}: {len(data)} 字节")

                except asyncio.TimeoutError:
                    # 继续接收
                    continue

    except Exception as e:
        print(f"[{device_id}] 错误: {e}")

    print(f"[{device_id}] 测试完成 - 帧数: {frame_count}, 字节: {total_bytes}")
    return frame_count, total_bytes


async def test_multi_device():
    """测试多设备并发视频流"""
    print("=== 多设备 WebSocket 视频流测试 ===\n")

    devices = ["192.168.8.22:5555", "192.168.8.34:5555"]
    duration = 10  # 测试 10 秒

    print(f"测试设备: {devices}")
    print(f"测试时长: {duration} 秒\n")

    # 并发测试所有设备
    tasks = [test_single_device(device_id, duration) for device_id in devices]
    results = await asyncio.gather(*tasks)

    print(f"\n{'='*60}")
    print("测试汇总")
    print(f"{'='*60}")

    total_frames = 0
    total_bytes = 0

    for device_id, (frames, bytes_count) in zip(devices, results):
        print(f"{device_id}: {frames} 帧, {bytes_count} 字节")
        total_frames += frames
        total_bytes += bytes_count

    print(f"\n总计: {total_frames} 帧, {total_bytes} 字节")

    if total_frames > 0:
        print(f"\n[SUCCESS] 多设备 WebSocket 视频流测试通过！")
        return True
    else:
        print(f"\n[FAIL] 未收到视频数据")
        return False


async def main():
    """主函数"""
    result = await test_multi_device()
    return 0 if result else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
