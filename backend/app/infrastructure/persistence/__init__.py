"""
持久化实现
===========

infrastructure/ 的子包，提供仓库协议的具体实现。

当前可用：
    sqlite.py — SqliteDebugRepository, SqliteDeviceRepository（aiosqlite）

未来选项：
    - PostgreSQL（用于生产多用户部署）
    - Redis（用于带 TTL 过期的会话数据）
    - 内存（用于测试）
"""
