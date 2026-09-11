"""
Scrcpy 模块使用示例
====================

演示如何使用 scrcpy 模块的各种功能。
"""

import asyncio
from app.scrcpy import (
    EncoderOpts,
    H264Parser,
    ControlSender,
    ServerManager,
    KEYCODE_HOME,
    KEYCODE_BACK,
    DEFAULT_ENCODER_OPTS,
    LOW_LATENCY_ENCODER_OPTS,
)
from app.infrastructure.stream.scrcpy import ScrcpyEncoder


async def example_video_stream():
    """
    示例 1：视频流传输。

    启动编码器，读取 H264 帧，解析为 NALU。
    """
    device_id = "emulator-5554"
    encoder = ScrcpyEncoder()

    # 使用默认配置：1080p, 4Mbps, 30fps
    opts = DEFAULT_ENCODER_OPTS

    print(f"开始视频流: {device_id}")

    try:
        async for nalu in encoder.start(device_id, opts):
            # nalu 是完整的 H264 NALU（包含起始码）
            # 可以通过 WebSocket 发送给前端
            print(f"收到 NALU: {len(nalu)} 字节")

            # 可以在这里解析 NALU 类型
            parser = H264Parser()
            nalu_type = parser.get_nalu_type(nalu)
            if parser.is_key_frame(nalu):
                print("  -> 关键帧 (IDR)")
            elif parser.is_sps(nalu):
                print("  -> SPS")
            elif parser.is_pps(nalu):
                print("  -> PPS")

            # 只处理前 10 个 NALU 作为演示
            # 实际使用时会持续运行
            break

    finally:
        await encoder.stop()
        print("视频流已停止")


async def example_low_latency_stream():
    """
    示例 2：低延迟视频流。

    使用 720p 配置降低延迟。
    """
    device_id = "emulator-5554"
    encoder = ScrcpyEncoder()

    # 使用低延迟配置：720p, 2Mbps, 30fps
    opts = LOW_LATENCY_ENCODER_OPTS

    print(f"开始低延迟视频流: {device_id}")
    print(f"配置: {opts.max_size}p, {opts.bit_rate}, {opts.fps}fps")

    async for nalu in encoder.start(device_id, opts):
        # 处理帧...
        print(f"收到帧: {len(nalu)} 字节")
        break

    await encoder.stop()


async def example_input_control():
    """
    示例 3：输入控制（通过 scrcpy 二进制协议）。

    演示通过 encoder.send_input() 发送各种输入操作。
    延迟 <5ms（对比 adb shell input 的 50-200ms）。
    """
    device_id = "emulator-5554"
    encoder = ScrcpyEncoder()
    opts = DEFAULT_ENCODER_OPTS

    print(f"开始输入控制: {device_id}")

    # 启动编码器（建立控制 socket）
    stream = encoder.start(device_id, opts)

    # 触摸点击 (100, 200)
    await encoder.send_input({"action": "touch", "x": 100, "y": 200})
    print("执行: 触摸点击 (100, 200)")

    # 滑动 (100, 200) -> (300, 400)，持续 300ms
    await encoder.send_input({
        "action": "swipe",
        "x1": 100, "y1": 200,
        "x2": 300, "y2": 400,
        "duration": 300,
    })
    print("执行: 滑动")

    # 按 HOME 键
    await encoder.send_input({"action": "key", "keycode": KEYCODE_HOME})
    print("执行: 按 HOME 键")

    # 按返回键
    await encoder.send_input({"action": "key", "keycode": KEYCODE_BACK})
    print("执行: 按返回键")

    # 输入文本（注意：不支持中文）
    await encoder.send_input({"action": "text", "text": "hello world"})
    print("执行: 输入文本 'hello world'")

    await encoder.stop()
    print("输入控制完成")


async def example_server_management():
    """
    示例 4：Server 管理。

    演示 scrcpy-server.jar 的部署和管理。
    """
    device_id = "emulator-5554"
    manager = ServerManager()

    print(f"开始 Server 管理: {device_id}")

    # 检查并推送 server（如果不存在）
    success = await manager.ensure_server(device_id)
    if success:
        print("Server 已就绪")
    else:
        print("Server 部署失败")
        return

    # 获取 server 版本
    version = await manager.get_server_version(device_id)
    print(f"Server 版本: {version}")

    # 获取本地 jar 路径
    jar_path = manager.get_local_jar_path()
    print(f"本地 JAR 路径: {jar_path}")

    # 强制重新推送
    await manager.push_server(device_id)
    print("Server 已重新推送")

    # 删除 server（可选）
    # await manager.delete_server(device_id)
    # print("Server 已删除")


async def example_h264_parsing():
    """
    示例 5：H264 解析。

    演示如何使用 H264Parser 解析 NALU。
    """
    parser = H264Parser()

    # 模拟从编码器读取的数据块
    # 实际使用时，这些数据来自 ScrcpyEncoder
    sample_data = b'\x00\x00\x00\x01\x67\x42\x00\x1e'  # SPS
    sample_data += b'\x00\x00\x00\x01\x68\xce\x38\x80'  # PPS
    sample_data += b'\x00\x00\x00\x01\x65\x88\x84\x00'  # IDR

    print("开始 H264 解析")

    # 喂入数据
    nalus = parser.feed(sample_data)

    print(f"解析出 {len(nalus)} 个 NALU")

    for i, nalu in enumerate(nalus):
        nalu_type = parser.get_nalu_type(nalu)
        print(f"  NALU {i}: {len(nalu)} 字节, 类型: {nalu_type}")

        if parser.is_sps(nalu):
            print("    -> SPS (序列参数集)")
        elif parser.is_pps(nalu):
            print("    -> PPS (图像参数集)")
        elif parser.is_key_frame(nalu):
            print("    -> IDR 关键帧")


async def example_websocket_integration():
    """
    示例 6：WebSocket 集成。

    演示如何将 scrcpy 模块集成到 WebSocket 端点。
    """
    from fastapi import WebSocket
    from app.infrastructure.stream.scrcpy import ScrcpyEncoder

    # 这个示例展示了完整的 WebSocket 端点结构
    # 实际实现需要在 FastAPI 路由中使用

    async def video_endpoint(websocket: WebSocket, device_id: str):
        """视频流 WebSocket 端点"""
        await websocket.accept()

        encoder = ScrcpyEncoder()
        opts = DEFAULT_ENCODER_OPTS

        try:
            # 启动视频流
            stream = encoder.start(device_id, opts)

            async for nalu in stream:
                # 发送视频帧给客户端
                await websocket.send_bytes(nalu)

                # 同时接收客户端输入
                try:
                    data = await asyncio.wait_for(
                        websocket.receive_json(),
                        timeout=0.001
                    )
                    # 通过二进制控制协议发送输入（<5ms 延迟）
                    await encoder.send_input(data)
                except asyncio.TimeoutError:
                    pass

        except Exception as e:
            print(f"错误: {e}")
        finally:
            await encoder.stop()

    print("WebSocket 集成示例（伪代码）")


async def main():
    """运行所有示例"""
    print("=" * 60)
    print("Scrcpy 模块使用示例")
    print("=" * 60)

    # 取消注释以运行特定示例
    # 注意：需要有连接的 Android 设备或模拟器

    # print("\n示例 1: 视频流传输")
    # await example_video_stream()

    # print("\n示例 2: 低延迟视频流")
    # await example_low_latency_stream()

    # print("\n示例 3: 输入控制")
    # await example_input_control()

    # print("\n示例 4: Server 管理")
    # await example_server_management()

    # print("\n示例 5: H264 解析")
    # await example_h264_parsing()

    # print("\n示例 6: WebSocket 集成")
    # await example_websocket_integration()

    print("\n示例代码已准备好，取消注释以运行")


if __name__ == "__main__":
    asyncio.run(main())
