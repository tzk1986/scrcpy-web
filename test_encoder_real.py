"""
测试 ScrcpyEncoder 是否能正常工作
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "backend"))

from app.scrcpy.encoder import ScrcpyEncoder
from app.domain.ports import EncoderOpts


async def test_encoder():
    """测试 ScrcpyEncoder"""
    device_id = "192.168.8.22:5555"

    print("=== ScrcpyEncoder 测试 ===")
    print(f"设备: {device_id}")
    print()

    encoder = ScrcpyEncoder()
    opts = EncoderOpts(
        max_size=1080,
        bit_rate="4M",
        codec="h264",
        fps=30,
    )

    print("启动编码器...")
    frame_count = 0
    total_bytes = 0
    start_time = asyncio.get_event_loop().time()

    try:
        async for nalu in encoder.start(device_id, opts):
            frame_count += 1
            total_bytes += len(nalu)

            if frame_count % 10 == 0:
                elapsed = asyncio.get_event_loop().time() - start_time
                print(f"  已接收 {frame_count} 个 NALU, {total_bytes} 字节, {elapsed:.1f}s")

            # 同时读取 stderr
            if encoder.process and encoder.process.stderr:
                try:
                    stderr_line = await asyncio.wait_for(
                        encoder.process.stderr.readline(),
                        timeout=0.01
                    )
                    if stderr_line:
                        print(f"  [stderr] {stderr_line.decode().strip()}")
                except asyncio.TimeoutError:
                    pass

            # 读取 5 秒后停止
            if asyncio.get_event_loop().time() - start_time > 5.0:
                print(f"\n已读取 5 秒，停止测试")
                break

    except Exception as e:
        print(f"[X] 错误: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        await encoder.stop()

    elapsed = asyncio.get_event_loop().time() - start_time
    print()
    print(f"[OK] 测试完成")
    print(f"  总 NALU: {frame_count}")
    print(f"  总字节: {total_bytes}")
    print(f"  时间: {elapsed:.2f}s")
    print(f"  平均帧率: {frame_count / elapsed:.1f} fps")
    print(f"  平均速率: {total_bytes / elapsed / 1024:.1f} KB/s")

    return True


if __name__ == "__main__":
    success = asyncio.run(test_encoder())
    sys.exit(0 if success else 1)
