"""
视频流 WebSocket 端点
========================

层：接口 → WebSocket。

处理 /ws/video/{device_id} 连接，负责：
    1. 接受 WebSocket 连接
    2. 创建 WebSocketTransport 封装
    3. 从 StreamService 获取 H.264 帧流
    4. 将帧通过 WebSocket 发送给客户端
    5. 同时接收客户端的输入事件（触摸、滑动、按键）并转发给设备

协议格式：
    服务端 → 客户端：二进制帧（H.264 NAL 单元）
    客户端 → 服务端：JSON 消息
        { "action": "touch", "x": 100, "y": 200 }
        { "action": "swipe", "x1": 100, "y1": 200, "x2": 300, "y2": 400, "duration": 300 }
        { "action": "key", "keycode": 4 }
        { "action": "text", "text": "hello" }

输入处理：
    当前通过直接调用 `adb shell input` 实现。
    未来应通过 AdbDriver 接口，保持架构一致性。

连接生命周期：
    - 客户端连接时，启动视频流
    - 客户端断开时，停止视频流并释放编码器资源
"""

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.application.stream_service import StreamService
from app.infrastructure.transport.websocket import WebSocketTransport

router = APIRouter(tags=["video"])


async def video_stream(websocket: WebSocket, device_id: str, stream_service: StreamService):
    """
    视频流 WebSocket 处理函数。

    参数：
        websocket: FastAPI WebSocket 实例。
        device_id: 目标设备的 ADB 序列号。
        stream_service: 视频流服务（注入）。

    流程：
        1. 接受 WebSocket 连接
        2. 创建传输层封装
        3. 循环：发送视频帧 + 检查输入事件（非阻塞，超时 1ms）
        4. 断开时停止视频流
    """
    await websocket.accept()
    transport = WebSocketTransport(websocket)

    try:
        async for frame in stream_service.start_stream(device_id):
            await transport.send(frame)

            # 非阻塞检查客户端输入（超时 1ms）
            # 这样可以在发送视频帧的间隙处理输入事件
            try:
                data = await asyncio.wait_for(websocket.receive_json(), timeout=0.001)
                await _handle_input(device_id, data)
            except asyncio.TimeoutError:
                pass
    except WebSocketDisconnect:
        await stream_service.stop_stream(device_id)


async def _handle_input(device_id: str, data: dict):
    """
    处理来自客户端的输入事件。

    参数：
        device_id: 目标设备的 ADB 序列号。
        data: JSON 消息，包含 action 字段和相应的坐标/键值。

    支持的 action：
        - touch: 触摸事件（x, y）
        - swipe: 滑动事件（x1, y1, x2, y2, duration）
        - key: 按键事件（keycode）
        - text: 文本输入（text）

    注意：
        当前直接调用 adb 子进程。未来应重构为通过 AdbDriver 接口调用，
        以保持架构一致性并支持 mock 测试。
    """
    action = data.get("action")

    if action == "touch":
        x, y = data["x"], data["y"]
        await asyncio.create_subprocess_exec(
            "adb", "-s", device_id, "shell", "input", "tap", str(x), str(y)
        )
    elif action == "swipe":
        x1, y1 = data["x1"], data["y1"]
        x2, y2 = data["x2"], data["y2"]
        duration = data.get("duration", 300)
        await asyncio.create_subprocess_exec(
            "adb",
            "-s",
            device_id,
            "shell",
            "input",
            "swipe",
            str(x1),
            str(y1),
            str(x2),
            str(y2),
            str(duration),
        )
    elif action == "key":
        keycode = data["keycode"]
        await asyncio.create_subprocess_exec(
            "adb", "-s", device_id, "shell", "input", "keyevent", str(keycode)
        )
    elif action == "text":
        text = data["text"]
        await asyncio.create_subprocess_exec(
            "adb", "-s", device_id, "shell", "input", "text", text
        )
