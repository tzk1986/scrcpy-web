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
        { "type": "shell_output", "output": "..." } — shell 命令输出

当前状态：
    - exec 命令已实现
    - subscribe、filter、export 为占位符（pass）

未来实现：
    - subscribe: 建立日志推送通道，实时发送新日志
    - filter: 动态更新过滤条件，服务端过滤后推送
    - export: 将日志打包为文件（JSON/CSV）供下载
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
        3. 断开时清理资源（当前为空）
    """
    await websocket.accept()

    try:
        while True:
            data = await websocket.receive_json()
            op = data.get("op")

            if op == "subscribe":
                # TODO: 实现日志订阅推送
                pass
            elif op == "filter":
                # TODO: 实现动态过滤
                pass
            elif op == "exec":
                # 执行 shell 命令并返回结果
                cmd = data.get("command", "")
                output = await debug_service.exec_shell(session_id, cmd)
                await websocket.send_json({"type": "shell_output", "output": output})
            elif op == "export":
                # TODO: 实现日志导出
                pass
    except WebSocketDisconnect:
        pass
