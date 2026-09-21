"""
测试多设备视频流并发
"""
import asyncio
import sys
from pathlib import Path

# 添加 backend 到路径
sys.path.insert(0, str(Path(__file__).parent / "backend"))

from app.infrastructure.stream.scrcpy import ScrcpyEncoder
from app.domain.ports import EncoderOpts


async def test_single_device(device_id: str, duration: int = 30):
    """测试单个设备的视频流"""
    print(f"\n[{device_id}] 开始测试...")

    opts = EncoderOpts(
        max_size=720,  # 降低分辨率以节省资源
        bit_rate="2M",
        codec="h264",
        fps=20,
    )

    encoder = ScrcpyEncoder()
    frame_count = 0
    total_bytes = 0

    try:
        start_time = asyncio.get_event_loop().time()

        async for frame in encoder.start(device_id, opts):
            frame_count += 1
            total_bytes += len(frame)

            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed >= duration:
                print(f"[{device_id}] 已运行 {elapsed:.1f} 秒，停止")
                break

    except Exception as e:
        print(f"[{device_id}] 错误: {e}")
    finally:
        await encoder.stop()

    print(f"[{device_id}] 完成: {frame_count} 帧, {total_bytes} 字节")
    return frame_count, total_bytes


async def test_multi_device():
    """测试多设备并发"""
    print("=== 多设备视频流测试 ===\n")

    # 两台设备
    devices = [
        "192.168.8.22:5555",
        "192.168.8.34:5555",
    ]

    # 先检查设备是否在线
    import subprocess
    for device in devices:
        result = subprocess.run(
            f"tools\\adb.exe -s {device} get-state",
            shell=True, capture_output=True, text=True
        )
        if result.returncode != 0 or "device" not in result.stdout:
            print(f"[{device}] 设备不在线，跳过")
            devices.remove(device)

    if len(devices) < 2:
        print("\n需要至少 2 台在线设备进行测试")
        return False

    print(f"在线设备: {devices}")

    # 并发测试（每台设备运行 30 秒）
    print("\n开始并发测试（每台设备 30 秒）...")
    tasks = [test_single_device(device, duration=30) for device in devices]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # 统计结果
    print("\n" + "="*60)
    print("测试结果汇总")
    print("="*60)

    total_frames = 0
    total_bytes = 0

    for device, result in zip(devices, results):
        if isinstance(result, Exception):
            print(f"[{device}] 失败: {result}")
        else:
            frames, bytes_ = result
            total_frames += frames
            total_bytes += bytes_
            print(f"[{device}] 成功: {frames} 帧, {bytes_} 字节")

    print(f"\n总计: {total_frames} 帧, {total_bytes} 字节")

    if total_frames > 0:
        print("\n[SUCCESS] 多设备测试通过！")
        return True
    else:
        print("\n[FAIL] 多设备测试失败")
        return False


if __name__ == "__main__":
    result = asyncio.run(test_multi_device())
    sys.exit(0 if result else 1)
