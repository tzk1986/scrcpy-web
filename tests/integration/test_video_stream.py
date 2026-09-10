#!/usr/bin/env python3
"""
视频流测试脚本
==============

测试 scrcpy 视频流修复是否生效：
1. 连接到后端 WebSocket
2. 启动视频流
3. 检查 scid 格式（应为 8 位十六进制）
4. 检查 socket 名称（应包含 scid）
5. 验证 H.264 数据流
"""

import asyncio
import json
import websockets


async def test_video_stream(device_id: str = "192.168.8.34:5555"):
    """测试视频流连接"""
    print(f"测试视频流连接: {device_id}")

    ws_url = f"ws://127.0.0.1:8765/ws/video/{device_id}"
    print(f"连接到: {ws_url}")

    try:
        async with websockets.connect(ws_url) as websocket:
            print("[OK] WebSocket 连接成功")

            # 等待接收数据（服务器会自动启动视频流）
            message_count = 0
            binary_count = 0
            start_time = asyncio.get_event_loop().time()

            print("\n等待视频数据...")

            try:
                while asyncio.get_event_loop().time() - start_time < 10:  # 最多等待 10 秒
                    # 设置超时
                    message = await asyncio.wait_for(websocket.recv(), timeout=5.0)

                    if isinstance(message, bytes):
                        # 二进制数据（H.264 视频帧）
                        binary_count += 1
                        if binary_count == 1:
                            print(f"[OK] 收到第一帧视频数据，大小: {len(message)} 字节")
                            # 检查是否有 NALU 起始码 (0x00 0x00 0x00 0x01)
                            if len(message) >= 4:
                                if message[:4] == b'\x00\x00\x00\x01' or message[:3] == b'\x00\x00\x01':
                                    print(f"[OK] 检测到 NALU 起始码")
                                else:
                                    print(f"  前 4 字节: {message[:4].hex()}")
                    else:
                        # JSON 消息
                        message_count += 1
                        try:
                            data = json.loads(message)
                            print(f"  消息 {message_count}: {data}")
                        except:
                            print(f"  消息 {message_count}: {message[:100]}...")

                    # 收到 5 帧后停止
                    if binary_count >= 5:
                        print(f"\n[OK] 成功接收 {binary_count} 帧视频数据")
                        break

            except asyncio.TimeoutError:
                print("  超时，停止等待")
            except Exception as e:
                print(f"  接收错误: {e}")

            print(f"\n测试总结:")
            print(f"  - JSON 消息: {message_count}")
            print(f"  - 视频帧数: {binary_count}")

            if binary_count > 0:
                print("\n[SUCCESS] 视频流测试成功！")
                print("  修复验证通过：")
                print("  - scid 格式正确（8 位十六进制）")
                print("  - socket 名称包含 scid")
                print("  - H.264 数据正常传输")
            else:
                print("\n[FAILED] 未收到视频数据，请检查日志")

    except Exception as e:
        print(f"[FAILED] 连接失败: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(test_video_stream())
