"""
ADB 驱动实现
=============

infrastructure/ 的子包，提供 AdbDriver 协议的具体实现。

当前可用：
    cli.py — AdbCliDriver（基于子进程，调用 `adb` 二进制文件）

未来选项：
    - pure-python-adb（无子进程开销）
    - asyncadb（原生异步 ADB 协议实现）
    - 远程 ADB 服务器（用于分布式部署）
"""
