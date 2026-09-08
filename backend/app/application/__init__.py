"""
应用层
=======

层：应用层（用例编排）。

本层包含编排领域逻辑的用例服务。每个服务都是一个薄协调者——
不包含业务规则（那些在 domain/ 中），而是：

    1. 从接口层接收请求（HTTP/WS 处理器）
    2. 调用领域端口（AdbDriver、仓库等）
    3. 将结果返回给接口层

服务依赖 Protocol 类（domain/ports.py），而不是具体实现。
实际的装配发生在 deps.py（组合根）中。

内容：
    device_service.py  — 设备列表、APK 安装、截图
    debug_service.py   — 调试会话生命周期、日志收集、shell 执行
    stream_service.py  — 视频流开始/停止
    session_service.py — 协作会话管理（多用户）
"""
