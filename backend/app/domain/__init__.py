"""
领域层
=======

层：领域层（最内层）。

这是应用的核心——纯业务逻辑，零框架依赖。这里不导入 FastAPI、SQLite、
asyncio.subprocess 或任何其他基础设施。

内容：
    device.py  — DeviceInfo 实体（Android 设备数据）
    session.py — DebugSession 实体（调试会话状态）
    ports.py   — Protocol 类（基础设施的抽象接口）

依赖倒置：
    领域层定义它所需的接口（ports.py 中的 Protocol 类）。
    基础设施层提供具体实现。
    应用层通过 deps.py（组合根）进行装配。

这意味着你可以将 SQLite 换成 PostgreSQL，或将 ADB CLI 换成 pure-python-adb，
而不需要修改任何业务逻辑代码。
"""
