"""
测试 WebSocket 视频流端点
==========================

端到端测试 /ws/video/{device_id} 端点：
1. 启动 FastAPI 应用
2. 连接 WebSocket
3. 接收视频数据
4. 发送输入事件
5. 验证完整流程
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


async def test_websocket_stream():
    """测试 WebSocket 视频流"""
    print("=== WebSocket 视频流测试 ===\n")

    device_id = "192.168.8.22:5555"
    ws_url = f"ws://localhost:8000/ws/video/{device_id}"

    print(f"连接设备: {device_id}")
    print(f"WebSocket URL: {ws_url}\n")

    # 连接到 WebSocket
    print("连接到 WebSocket...")
    try:
        async with websockets.connect(ws_url) as ws:
            print("[OK] 连接成功\n")

            # 接收视频数据
            print("开始接收视频数据...")
            frame_count = 0
            total_bytes = 0

            # 设置接收超时
            try:
                while True:
                    # 接收二进制帧（H.264 数据）- 30 秒超时
                    data = await asyncio.wait_for(ws.recv(), timeout=30)

                    if isinstance(data, bytes):
                        frame_count += 1
                        total_bytes += len(data)

                        if frame_count <= 5 or frame_count % 10 == 0:
                            print(f"帧 {frame_count}: {len(data)} 字节 (累计: {total_bytes} 字节)")

                        # 每 5 帧发送一个触摸事件测试
                        if frame_count % 5 == 0:
                            input_event = {
                                "action": "touch",
                                "x": 100,
                                "y": 200
                            }
                            await ws.send(json.dumps(input_event))
                            print(f"  -> 发送触摸事件: {input_event}")

                    # 测试 20 帧后停止
                    if frame_count >= 20:
                        print("\n已接收 20 帧，测试完成")
                        break

            except asyncio.TimeoutError:
                print("\n接收超时（30 秒）")

    except ConnectionRefusedError:
        print("[FAIL] 无法连接到服务器")
        print("\n请确保 FastAPI 应用正在运行:")
        print("  cd backend")
        print("  uvicorn app.main:app --reload\n")
        return False
    except Exception as e:
        print(f"[FAIL] 连接错误: {e}")
        import traceback
        traceback.print_exc()
        return False

    print(f"\n{'='*60}")
    print(f"测试完成")
    print(f"{'='*60}")
    print(f"总帧数: {frame_count}")
    print(f"总字节: {total_bytes}")

    if frame_count > 0:
        print(f"\n[SUCCESS] WebSocket 视频流测试通过！")
        return True
    else:
        print(f"\n[FAIL] 未收到视频数据")
        return False


async def main():
    """主函数"""
    result = await test_websocket_stream()
    return 0 if result else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
