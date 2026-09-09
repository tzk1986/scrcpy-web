"""
测试修正后的 ScrcpyEncoder
"""
import asyncio
import sys
from pathlib import Path

# 添加 backend 到路径
sys.path.insert(0, str(Path(__file__).parent / "backend"))

from app.infrastructure.stream.scrcpy import ScrcpyEncoder
from app.domain.ports import EncoderOpts

async def test_encoder():
    """测试编码器"""
    print("=== 测试修正后的 ScrcpyEncoder ===\n")

    encoder = ScrcpyEncoder()

    # 编码器配置
    opts = EncoderOpts(
        max_size=1080,
        bit_rate="4M",
        codec="h264",
        fps=30,
    )

    device_id = "192.168.8.22:5555"

    print(f"启动编码器...")
    print(f"设备: {device_id}")
    print(f"配置: {opts}\n")

    frame_count = 0
    total_bytes = 0

    try:
        async for frame in encoder.start(device_id, opts):
            frame_count += 1
            total_bytes += len(frame)

            if frame_count <= 5 or frame_count % 10 == 0:
                print(f"帧 {frame_count}: {len(frame)} 字节 (累计: {total_bytes} 字节)")

            # 测试 50 帧后停止
            if frame_count >= 50:
                print("\n已接收 50 帧，停止测试")
                break

    except Exception as e:
        print(f"\n错误: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await encoder.stop()

    print(f"\n=== 统计 ===")
    print(f"设备名称: {encoder.device_name}")
    print(f"设备 ID: {encoder.device_id}")
    print(f"总帧数: {frame_count}")
    print(f"总字节: {total_bytes}")

    if frame_count > 0:
        print(f"\n[SUCCESS] 编码器工作正常！")
        return True
    else:
        print(f"\n[FAIL] 未收到视频数据")
        return False

if __name__ == "__main__":
    result = asyncio.run(test_encoder())
    sys.exit(0 if result else 1)
