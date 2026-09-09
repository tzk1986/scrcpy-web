"""
调试数据 WebSocket 端点
=========================

层：接口 → WebSocket。

处理 /ws/debug/{session_id} 连接，提供实时调试数据流。

协议格式（JSON 消息）：
    客户端 → 服务端：
        { "op": "subscribe" }                  — 订阅日志流
        { "op": "filter", "level": "E" }      — 设置过滤条件
        { "op": "exec", "command": "ls" }     — 执行 shell 命令
        { "op": "export" }                    — 导出日志

    服务端 → 客户端：
        { "type": "log", "entry": {...} }      — 日志条目
        { "type": "shell_stream", "line": "..." } — shell 命令输出一行
        { "type": "shell_output", "output": "...", "success": true } — shell 命令完成
        { "type": "session_closed" }            — 会话已关闭

流式 shell 输出：
    exec 命令现在使用流式输出。命令执行时，每行输出立即通过
    shell_stream 消息发送。命令完成后，发送 shell_output 消息
    表示结束（success 字段表示命令是否成功）。

当前状态：
    - exec 命令已实现（流式输出）
    - subscribe: 已实现，实时推送日志
    - filter: 已实现，服务端过滤
    - export: 待实现
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.application.debug_service import DebugService

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
        3. 断开时清理订阅
    """
    await websocket.accept()

    try:
        while True:
            data = await websocket.receive_json()
            op = data.get("op")

            if op == "subscribe":
                # 订阅实时日志推送
                await debug_service.subscribe(session_id, websocket)
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
                # 流式执行 shell 命令：每行输出立即发送
                cmd = data.get("command", "")
                try:
                    async for line in debug_service.exec_shell_stream(session_id, cmd):
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

            elif op == "export":
                # TODO: 实现日志导出
                await websocket.send_json({"type": "error", "message": "Export not implemented yet"})

            elif op == "unsubscribe":
                # 取消订阅
                await debug_service.unsubscribe(session_id, websocket)
                await websocket.send_json({"type": "unsubscribed"})

    except WebSocketDisconnect:
        # 断开时清理订阅
        await debug_service.unsubscribe(session_id, websocket)
