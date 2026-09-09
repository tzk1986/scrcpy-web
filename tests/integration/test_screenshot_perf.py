"""
截屏性能测试
==============

测试优化后的截屏 API 性能。
"""
import asyncio
import time
import aiohttp


async def test_screenshot_performance():
    """测试截屏 API 的响应时间和帧率"""
    device_id = "192.168.8.22:5555"
    url = f"http://localhost:8000/api/devices/{device_id}/screenshot"

    print("=== 截屏 API 性能测试 ===\n")

    # 测试单次截屏
    async with aiohttp.ClientSession() as session:
        start = time.time()
        async with session.get(url) as resp:
            data = await resp.read()
            elapsed = time.time() - start

        print(f"单次截屏:")
        print(f"  状态码: {resp.status}")
        print(f"  数据大小: {len(data)} 字节")
        print(f"  耗时: {elapsed*1000:.0f} ms")
        print()

    # 测试连续截屏（模拟前端刷新）
    print("连续截屏测试（10次）:")
    times = []
    async with aiohttp.ClientSession() as session:
        for i in range(10):
            start = time.time()
            async with session.get(url) as resp:
                data = await resp.read()
                elapsed = time.time() - start
                times.append(elapsed)

            print(f"  #{i+1}: {elapsed*1000:.0f} ms, {len(data)} 字节")

    avg_time = sum(times) / len(times)
    min_time = min(times)
    max_time = max(times)

    print()
    print(f"统计:")
    print(f"  平均耗时: {avg_time*1000:.0f} ms")
    print(f"  最快: {min_time*1000:.0f} ms")
    print(f"  最慢: {max_time*1000:.0f} ms")
    print(f"  理论最大 FPS: {1/avg_time:.1f}")
    print()

    # 建议刷新间隔
    if avg_time < 0.2:
        print("建议刷新间隔: 300ms (~3 FPS)")
    elif avg_time < 0.5:
        print("建议刷新间隔: 500ms (~2 FPS)")
    else:
        print(f"建议刷新间隔: {int(avg_time*1000*2)}ms")


if __name__ == "__main__":
    asyncio.run(test_screenshot_performance())
