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
        { "op": "resize", "cols": N, "rows": M } — 调整设备 PTY 窗口大小（正整数）
        { "op": "unsubscribe" }               — 取消订阅

    服务端 → 客户端：
        { "type": "log", "entry": {...} }      — 日志条目
        { "type": "shell_stream", "line": "..." } — shell 命令输出一行
        { "type": "shell_output", "output": "...", "success": true } — shell 命令完成
        { "type": "error", "message": "..." }  — 错误消息
        { "type": "session_closed" }            — 会话已关闭

日志导出不走本 WS：使用 HTTP 端 GET /api/debug/sessions/{id}/logs/export
（json/csv 文件下载），此前 WS 的 export 操作未实现、恒回错误，已移除。

流式 shell 输出：
    exec 命令使用流式输出。命令执行时，每行输出通过 shell_stream 消息发送。
    命令完成后，发送 shell_output 消息表示结束（success 字段表示是否成功）。

PTY 模式 input 操作：
    input 操作将原始按键数据（base64 编码）发送到设备 shell。
    shell 输出由后台任务持续读取并转发给客户端。

当前状态：
    - exec 命令已实现（流式输出，使用 InteractiveShell.execute）
    - input 操作已实现（PTY 模式透传，后台读取器转发输出）
    - resize 操作已实现（ConPTY set_size；管道降级 no-op）
    - subscribe: 已实现，实时推送日志 + shell 输出
    - filter: 已实现，服务端过滤
"""

import base64
from contextlib import aclosing
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.application.debug_service import DebugService
from app.core.logging import get_logger
from app.interfaces.ws.protocol import parse_ws_message

logger = get_logger(__name__)

router = APIRouter(tags=["debug"])

_BAD_MESSAGE = {"type": "error", "code": "BAD_MESSAGE", "message": "invalid message"}


async def _safe_send_json(websocket: WebSocket, payload: dict[str, Any]) -> None:
    """尽力回发消息；客户端已断开时静默忽略（断线清理由外层 finally 负责）。"""
    try:
        await websocket.send_json(payload)
    except Exception:
        pass


async def _send_bad_message(websocket: WebSocket) -> None:
    await _safe_send_json(websocket, dict(_BAD_MESSAGE))


async def debug_stream(websocket: WebSocket, session_id: str, debug_service: DebugService) -> None:
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
            data = parse_ws_message(await websocket.receive_text())
            if data is None:
                await _send_bad_message(websocket)
                continue
            op = data.get("op")

            if op == "subscribe":
                # 订阅实时日志推送；带 from_seq 时先补发断线期间的日志（断线续传）
                from_seq = data.get("from_seq")
                if from_seq is not None and not (
                    isinstance(from_seq, int) and not isinstance(from_seq, bool)
                ):
                    await _send_bad_message(websocket)
                    continue
                # 方案 51 C7：会话失效（如被 idle reaper 回收）等异常回
                # 可见 error 帧而非异常断连（协议头已承诺 error 消息类型）
                try:
                    if from_seq is not None:
                        await debug_service.subscribe(session_id, websocket)
                        logs, missing = await debug_service.get_logs_since(
                            session_id, from_seq)
                        await websocket.send_json({
                            "type": "log_batch", "logs": logs, "missing": missing})
                    else:
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
                except WebSocketDisconnect:
                    raise
                except Exception as e:
                    logger.error("subscribe_failed", session=session_id, error=str(e))
                    await _safe_send_json(websocket, {
                        "type": "error",
                        "code": "SUBSCRIBE_FAILED",
                        "message": "failed to subscribe",
                    })

            elif op == "filter":
                # 设置服务端过滤条件（减少网络流量）
                level = data.get("level")
                tag = data.get("tag")
                paused = data.get("paused", False)
                if (
                    (level is not None and not isinstance(level, str))
                    or (tag is not None and not isinstance(tag, str))
                    or not isinstance(paused, bool)
                ):
                    await _send_bad_message(websocket)
                    continue
                applied = await debug_service.set_subscriber_filter(
                    session_id, websocket, level, tag, paused)
                await websocket.send_json({
                    "type": "filter_applied",
                    # 方案 51 C16：未命中订阅者（未先 subscribe）回 false，消除假确认
                    "filter_applied": applied,
                    "level": level,
                    "tag": tag,
                    "paused": paused,
                })

            elif op == "exec":
                # 流式执行 shell 命令
                # aclosing：客户端中断时立即关闭生成器，释放会话锁并落盘历史
                cmd = data.get("command")
                if not isinstance(cmd, str):
                    await _send_bad_message(websocket)
                    continue
                try:
                    async with aclosing(
                        debug_service.exec_shell_stream_raw(session_id, cmd)
                    ) as stream:
                        async for line in stream:
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
                except WebSocketDisconnect:
                    # 客户端中途断开：不是命令执行错误，交由外层 finally 清理
                    raise
                except Exception as e:
                    logger.error("exec_failed", session=session_id, error=str(e))
                    await _safe_send_json(websocket, {
                        "type": "shell_output",
                        "output": "internal error",
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
                    logger.error("input_failed", session=session_id, error=str(e))
                    await _safe_send_json(websocket, {
                        "type": "error",
                        "message": "failed to send input",
                    })

            elif op == "resize":
                # 终端 resize：cols/rows 必须为正整数（pywinpty set_size 约束）
                cols_raw = data.get("cols")
                rows_raw = data.get("rows")
                valid = all(
                    isinstance(v, int) and not isinstance(v, bool) and v > 0
                    for v in (cols_raw, rows_raw)
                )
                if not valid:
                    await _safe_send_json(websocket, {
                        "type": "error",
                        "message": "resize requires positive integer cols/rows",
                    })
                    continue
                # valid 已保证两者为 int（排除 bool）；显式收窄供类型检查
                cols = cols_raw if isinstance(cols_raw, int) else 0
                rows = rows_raw if isinstance(rows_raw, int) else 0
                try:
                    await debug_service.resize_shell(session_id, cols, rows)
                except Exception as e:
                    logger.error("resize_failed", session=session_id, error=str(e))
                    await _safe_send_json(websocket, {
                        "type": "error",
                        "message": "failed to resize shell",
                    })

            elif op == "unsubscribe":
                # 取消订阅：仅订阅者清零才停输出转发（方案 51 C2，多客户端不互掐）
                await debug_service.unsubscribe_and_maybe_stop_forwarding(
                    session_id, websocket)
                await websocket.send_json({"type": "unsubscribed"})

    except WebSocketDisconnect:
        # 客户端断开属正常退出路径，清理由 finally 统一执行
        pass
    finally:
        # 任何退出路径（客户端断开 / 其它异常）都清理订阅；输出转发仅在
        # 订阅者清零时停（方案 51 C2），避免残留订阅者与转发任务泄漏
        await debug_service.unsubscribe_and_maybe_stop_forwarding(
            session_id, websocket)
