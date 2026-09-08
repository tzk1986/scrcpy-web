"""
基础设施层
===========

层：基础设施层（最外层）。

本层提供领域 Protocol 类的具体实现。它是唯一直接导入外部库
和系统工具的层。

内容：
    adb/           — ADB 驱动实现
        cli.py         AdbCliDriver：基于子进程的 ADB CLI 封装
    persistence/   — 仓库实现
        sqlite.py      SqliteDebugRepository, SqliteDeviceRepository
    stream/        — 视频编码器实现
        scrcpy.py      ScrcpyEncoder：scrcpy-server H.264 编码器
    transport/     — 网络传输层实现
        websocket.py   WebSocketTransport：封装 FastAPI WebSocket
        webtransport.py WebTransportTransport：Chrome 优化（占位符）

依赖方向：
    infrastructure → domain（实现其 Protocol）
    infrastructure → core（使用配置、日志、异常）
    infrastructure 不导入 application 或 interfaces。
"""
