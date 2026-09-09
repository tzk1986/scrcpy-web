"""
长时间测试 scrcpy 视频流稳定性
"""
import asyncio
import sys
from pathlib import Path

# 添加 backend 到路径
sys.path.insert(0, str(Path(__file__).parent / "backend"))

from app.infrastructure.stream.scrcpy import ScrcpyEncoder
from app.domain.ports import EncoderOpts


async def test_long_running():
    """测试长时间运行的视频流"""
    print("=== 长时间测试 scrcpy.exe 视频流 ===\n")

    # 配置
    device_id = "192.168.8.22:5555"
    opts = EncoderOpts(
        max_size=1080,
        bit_rate="4M",
        codec="h264",
        fps=30,
    )

    encoder = ScrcpyEncoder()

    print(f"设备: {device_id}")
    print(f"配置: {opts}\n")

    frame_count = 0
    total_bytes = 0
    start_time = asyncio.get_event_loop().time()

    try:
        print("开始接收视频流（目标：运行 60 秒）...\n")

        async for frame in encoder.start(device_id, opts):
            frame_count += 1
            total_bytes += len(frame)

            current_time = asyncio.get_event_loop().time()
            elapsed = current_time - start_time

            # 每 10 帧或每秒显示一次状态
            if frame_count <= 5 or frame_count % 10 == 0:
                print(f"[{elapsed:6.1f}s] 帧 {frame_count:4d}: {len(frame):6d} 字节 "
                      f"(累计: {total_bytes:8d} 字节, "
                      f"平均: {total_bytes/elapsed:.0f} 字节/秒)")

            # 运行 60 秒后停止
            if elapsed >= 60:
                print("\n已运行 60 秒，停止测试")
                break

    except Exception as e:
        print(f"\n[ERROR] {e}")
        import traceback
        traceback.print_exc()
    finally:
        await encoder.stop()

    elapsed = asyncio.get_event_loop().time() - start_time

    print(f"\n{'='*60}")
    print(f"测试完成")
    print(f"{'='*60}")
    print(f"总帧数: {frame_count}")
    print(f"总字节: {total_bytes}")
    print(f"运行时间: {elapsed:.1f} 秒")
    print(f"平均帧率: {frame_count/elapsed:.2f} FPS")
    print(f"平均速率: {total_bytes/elapsed:.0f} 字节/秒")
    print(f"平均帧大小: {total_bytes/frame_count:.0f} 字节/帧")

    if frame_count > 0 and elapsed > 30:
        print(f"\n[SUCCESS] 视频流稳定运行！")
        return True
    else:
        print(f"\n[FAIL] 测试未通过")
        return False


if __name__ == "__main__":
    result = asyncio.run(test_long_running())
    sys.exit(0 if result else 1)
