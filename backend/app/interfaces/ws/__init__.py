"""
WebSocket 端点
===============

接口层的 WebSocket 子包。

提供实时数据流的 WebSocket 连接：
    video.py — /ws/video/{device_id}：视频流（H.264 帧）
    debug.py — /ws/debug/{session_id}：调试数据流（日志、shell 输出）

WebSocket 端点在 main.py 中通过 @app.websocket() 装饰器注册。
注意：FastAPI 的 WebSocket 路由不支持 APIRouter 的路径参数，
所以端点定义在 main.py，但处理逻辑在这里的模块中。
"""
