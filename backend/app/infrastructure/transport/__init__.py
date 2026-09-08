"""
网络传输层实现
===============

infrastructure/ 的子包，提供 Transport 协议的具体实现。

当前可用：
    websocket.py    — WebSocketTransport：封装 FastAPI WebSocket
    webtransport.py — WebTransportTransport：Chrome 优化（占位符）

传输层的作用：
    将视频编码器（VideoEncoder）与网络层解耦。
    编码器只负责生成 H.264 帧，传输层负责将帧发送给客户端。

为什么需要抽象？
    - WebSocket：通用，兼容所有浏览器
    - WebTransport：Chrome 专属，延迟更低，适合实时视频
    - 未来可能支持 gRPC-Web、SSE 等

通过 Transport 协议，上层代码无需关心底层用的是哪种传输方式。
"""
