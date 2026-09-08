"""
接口层
=======

层：接口（最外层）。

负责将 HTTP 请求和 WebSocket 消息路由到应用层服务。

这一层应该尽可能"薄"——只做参数解析、服务调用、结果格式化。
不包含任何业务逻辑（业务逻辑在 application/ 层）。

内容：
    http/   — RESTful HTTP 端点
        devices.py   GET/POST /api/devices（设备管理）
        debug.py     GET/POST /api/debug（调试会话）
        sessions.py  GET/POST /api/sessions（协作会话）
    ws/     — WebSocket 端点
        video.py     /ws/video/{device_id}（视频流）
        debug.py     /ws/debug/{session_id}（调试数据流）
"""
