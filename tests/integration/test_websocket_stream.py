#!/usr/bin/env python3
"""
测试 WebSocket 视频流
======================

连接到后端 WebSocket，验证视频数据是否正常流动。
"""

import asyncio
import websockets
import json


async def test_websocket_stream():
    """测试 WebSocket 视频流"""
    device_id = "192.168.8.34:5555"
    uri = f"ws://127.0.0.1:8765/ws/video/{device_id}"

    print(f"连接到: {uri}")

    try:
        async with websockets.connect(uri) as ws:
            print("[OK] 连接成功")

            # 等待设备信息
            print("\n等待设备信息...")
            device_info = await asyncio.wait_for(ws.recv(), timeout=5.0)
            info = json.loads(device_info)
            print(f"  类型: {info.get('type')}")
            if info.get('type') == 'device_info':
                print(f"  设备: {info.get('device_name')}")
                print(f"  分辨率: {info.get('width')}x{info.get('height')}")

            # 接收视频数据
            print("\n接收视频数据（10秒）...")
            frame_count = 0
            total_bytes = 0
            start_time = asyncio.get_event_loop().time()

            while asyncio.get_event_loop().time() - start_time < 10.0:
                try:
                    data = await asyncio.wait_for(ws.recv(), timeout=2.0)
                    if isinstance(data, bytes):
                        frame_count += 1
                        total_bytes += len(data)

                        if frame_count == 1:
                            print(f"  第 1 帧: {len(data)} 字节")
                            if len(data) >= 4:
                                print(f"  前 4 字节: {data[:4].hex()}")
                                # 检查 NALU 起始码
                                if data[:4] == b'\x00\x00\x00\x01':
                                    print("  [OK] 检测到 NALU 起始码")
                                elif data[:3] == b'\x00\x00\x01':
                                    print("  [OK] 检测到 NALU 起始码 (3字节)")
                        elif frame_count <= 5:
                            print(f"  第 {frame_count} 帧: {len(data)} 字节")

                except asyncio.TimeoutError:
                    print("  接收超时")
                    continue

            print(f"\n总计: {frame_count} 帧, {total_bytes} 字节")
            if frame_count > 0:
                print(f"平均帧率: {frame_count / 10:.1f} fps")
                print(f"平均码率: {total_bytes * 8 / 10 / 1000:.1f} kbps")

    except Exception as e:
        print(f"[ERROR] 错误: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(test_websocket_stream())
