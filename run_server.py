#!/usr/bin/env python3
"""
服务器启动脚本
==============

Windows 上需要在 uvicorn 启动前设置 ProactorEventLoop，
以支持 asyncio.create_subprocess_exec。

使用方式：
    python run_server.py
"""

import sys
import os

# 添加项目根目录和 backend 目录到 Python 路径
project_root = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.join(project_root, "backend")
sys.path.insert(0, project_root)
sys.path.insert(0, backend_dir)

import asyncio

# Windows 上必须使用 ProactorEventLoop 才能支持子进程
if sys.platform == "win32":
    # 设置事件循环策略
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    # 创建并设置 ProactorEventLoop
    loop = asyncio.ProactorEventLoop()
    asyncio.set_event_loop(loop)

import uvicorn

if __name__ == "__main__":
    # 清理旧的临时视频文件（防止磁盘空间占用）
    import shutil
    temp_dir = os.path.join(os.environ.get("TEMP", "/tmp"), "scrcpy_recordings")
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir, ignore_errors=True)
        print(f"Cleaned old temp files: {temp_dir}")

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8765,  # 使用不常用端口，避免冲突
        reload=False,  # 禁用 reload 以确保使用我们设置的事件循环
    )
