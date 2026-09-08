"""
视频流实现
===========

infrastructure/ 的子包，提供 VideoEncoder 协议的具体实现。

当前可用：
    scrcpy.py — ScrcpyEncoder：基于 scrcpy-server 的 H.264 编码器

未来选项：
    - MediaCodec：Android 原生硬件编码
    - FFmpeg：软件编码（跨平台但性能较低）
    - GStreamer：流媒体框架集成
"""
