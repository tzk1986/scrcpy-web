"""
调试数据 WebSocket 端点
=========================

层：接口 → WebSocket。

处理 /ws/debug/{session_id} 连接，提供实时调试数据流。

协议格式（JSON 消息）：
    客户端 → 服务端：
        { "op": "subscribe" }                  — 订阅日志流 + shell 输出
        { "op": "filter", "level": "E" }      — 设置过滤条件
        { "op": "exec", "command": "ls" }     — 执行 shell 命令（流式输出）
        { "op": "input", "data": "<base64>" } — 发送原始按键到设备 shell（PTY 模式）
        { "op": "export" }                    — 导出日志

    服务端 → 客户端：
        { "type": "log", "entry": {...} }      — 日志条目
        { "type": "shell_stream", "line": "..." } — shell 命令输出一行
        { "type": "shell_output", "output": "...", "success": true } — shell 命令完成
        { "type": "error", "message": "..." }  — 错误消息
        { "type": "session_closed" }            — 会话已关闭

流式 shell 输出：
    exec 命令使用流式输出。命令执行时，每行输出通过 shell_stream 消息发送。
    命令完成后，发送 shell_output 消息表示结束（success 字段表示是否成功）。

PTY 模式 input 操作：
    input 操作将原始按键数据（base64 编码）发送到设备 shell。
    shell 输出由后台任务持续读取并转发给客户端。

当前状态：
    - exec 命令已实现（流式输出，使用 InteractiveShell.execute）
    - input 操作已实现（PTY 模式透传，后台读取器转发输出）
    - subscribe: 已实现，实时推送日志 + shell 输出
    - filter: 已实现，服务端过滤
    - export: 待实现
"""

import base64

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.application.debug_service import DebugService
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["debug"])


async def debug_stream(websocket: WebSocket, session_id: str, debug_service: DebugService):
    """
    调试数据 WebSocket 处理函数。

    参数：
        websocket: FastAPI WebSocket 实例。
        session_id: 调试会话 ID。
        debug_service: 调试服务（注入）。

    流程：
        1. 接受 WebSocket 连接
        2. 循环接收 JSON 消息，根据 op 字段分发处理
        3. 断开时清理订阅和输出转发
    """
    await websocket.accept()

    try:
        while True:
            data = await websocket.receive_json()
            op = data.get("op")

            if op == "subscribe":
                # 订阅实时日志推送
                await debug_service.subscribe(session_id, websocket)
                # 创建 shell（内部已启动输出转发任务）
                shell = await debug_service.get_or_create_shell(session_id)
                # 直接发送初始输出（设备 prompt），不依赖转发任务的时序
                initial_output = shell.get_initial_output()
                if initial_output:
                    await websocket.send_json({"type": "shell_stream", "line": initial_output})
                    logger.info("initial_output_sent_via_subscribe", session=session_id,
                                length=len(initial_output), output=repr(initial_output[:200]))
                else:
                    logger.warning("initial_output_empty", session=session_id)
                await websocket.send_json({"type": "subscribed", "session_id": session_id})

            elif op == "filter":
                # 设置服务端过滤条件（减少网络流量）
                level = data.get("level")
                tag = data.get("tag")
                await debug_service.set_subscriber_filter(session_id, websocket, level, tag)
                await websocket.send_json({
                    "type": "filter_applied",
                    "level": level,
                    "tag": tag,
                })

            elif op == "exec":
                # 流式执行 shell 命令
                cmd = data.get("command", "")
                try:
                    async for line in debug_service._exec_shell_stream_raw(session_id, cmd):
                        await websocket.send_json({
                            "type": "shell_stream",
                            "line": line,
                            "done": False,
                        })
                    # 命令完成
                    await websocket.send_json({
                        "type": "shell_output",
                        "output": "",
                        "success": True,
                        "done": True,
                    })
                except Exception as e:
                    await websocket.send_json({
                        "type": "shell_output",
                        "output": str(e),
                        "success": False,
                        "done": True,
                    })

            elif op == "input":
                # PTY 模式：发送原始按键到设备 shell
                # 输出由后台读取器自动转发（通过 shell_stream 消息）
                try:
                    data_b64 = data.get("data", "")
                    if not data_b64:
                        continue
                    # 解码 base64
                    input_data = base64.b64decode(data_b64)
                    # 获取或创建 shell，发送按键
                    shell = await debug_service.get_or_create_shell(session_id)
                    await shell.send_input(input_data)
                    # 不需要读取输出 — 后台读取器会自动转发
                except Exception as e:
                    await websocket.send_json({
                        "type": "error",
                        "message": f"Failed to send input: {e}"
                    })

            elif op == "export":
                # TODO: 实现日志导出
                await websocket.send_json({"type": "error", "message": "Export not implemented yet"})

            elif op == "unsubscribe":
                # 取消订阅
                await debug_service.unsubscribe(session_id, websocket)
                await debug_service.stop_output_forwarding(session_id)
                await websocket.send_json({"type": "unsubscribed"})

    except WebSocketDisconnect:
        # 断开时清理订阅和输出转发
        await debug_service.unsubscribe(session_id, websocket)
        await debug_service.stop_output_forwarding(session_id)
