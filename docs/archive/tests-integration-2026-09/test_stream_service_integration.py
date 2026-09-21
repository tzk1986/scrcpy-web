"""
测试 StreamService 集成
"""
import asyncio
import sys
from pathlib import Path

# 添加 backend 到路径
sys.path.insert(0, str(Path(__file__).parent / "backend"))

from app.application.stream_service import StreamService
from app.domain.ports import EncoderOpts


async def test_stream_service():
    """测试 StreamService 与 ScrcpyEncoder 的集成"""
    print("=== StreamService 集成测试 ===\n")

    # 创建 StreamService
    service = StreamService()
    print(f"StreamService 初始化完成")
    print(f"活跃流: {service.get_active_streams()}\n")

    device_id = "192.168.8.22:5555"
    print(f"设备: {device_id}\n")

    # 启动流
    print("启动视频流...")
    frame_count = 0
    total_bytes = 0

    try:
        async for frame in service.start_stream(device_id):
            frame_count += 1
            total_bytes += len(frame)

            if frame_count <= 5 or frame_count % 10 == 0:
                active = service.get_active_streams()
                print(f"帧 {frame_count}: {len(frame)} 字节 (累计: {total_bytes} 字节, 活跃流: {len(active)})")

            # 测试 50 帧后停止
            if frame_count >= 50:
                print("\n已接收 50 帧，停止测试")
                break

    except Exception as e:
        print(f"\n[ERROR] {e}")
        import traceback
        traceback.print_exc()
    finally:
        print("\n停止视频流...")
        await service.stop_stream(device_id)

    print(f"\n=== 统计 ===")
    print(f"总帧数: {frame_count}")
    print(f"总字节: {total_bytes}")
    print(f"活跃流: {service.get_active_streams()}")

    if frame_count > 0:
        print(f"\n[SUCCESS] StreamService 集成测试通过！")
        return True
    else:
        print(f"\n[FAIL] StreamService 集成测试失败")
        return False


if __name__ == "__main__":
    result = asyncio.run(test_stream_service())
    sys.exit(0 if result else 1)
