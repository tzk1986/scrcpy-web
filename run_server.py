#!/usr/bin/env python3
"""
服务器启动脚本
==============

开发（源码运行）：python run_server.py —— uvicorn 直启，行为与原版一致。
绿色版（PyInstaller 冻结运行）：单实例互斥、端口探测回退、文件日志、
    服务就绪后自动打开浏览器；默认绑定 127.0.0.1。

Windows 上需要在 uvicorn 启动前设置 ProactorEventLoop，
以支持 asyncio.create_subprocess_exec。
"""

import asyncio
import os
import sys

# 添加项目根目录和 backend 目录到 Python 路径
project_root = os.path.dirname(os.path.abspath(__file__))
backend_dir = os.path.join(project_root, "backend")
sys.path.insert(0, project_root)
sys.path.insert(0, backend_dir)

# Windows 上必须使用 ProactorEventLoop 才能支持子进程
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())


def main() -> int:
    frozen = bool(getattr(sys, "frozen", False))
    if frozen:
        # 必须在导入 app 之前接管标准流：--noconsole 下 stdout/stderr 为
        # None，导入期日志写入会崩（§8.4-3）；DLL 搜索路径同理提前（§8.4-2）
        from app.core import launcher
        from config.settings import app_base_dir

        launcher.redirect_std_streams(app_base_dir() / "logs" / "openscrcpy.log")
        launcher.fix_dll_search_path()

    import uvicorn

    from app.core.config import settings
    from app.main import app  # 传对象：PyInstaller 静态分析可收集 app.main（§9.3-1）

    srv = settings().server

    if not frozen:
        # 开发模式：行为与原版 run_server.py 一致
        uvicorn.run(app, host=srv.host, port=srv.port, reload=False)
        return 0

    import threading
    import webbrowser

    from app.core import launcher
    from config.settings import app_base_dir

    host = launcher.resolve_bind_host(
        srv.host, frozen=True, explicit_env=os.getenv("BACKEND_HOST")
    )
    if not launcher.acquire_single_instance_mutex():
        # 另一实例在跑：直接开浏览器指向其端口（§8.4-6）
        port = launcher.read_runtime_port(app_base_dir() / "data" / "port.txt") or srv.port
        webbrowser.open(f"http://{host}:{port}")
        return 0

    port = launcher.find_available_port(host, srv.port)
    launcher.write_runtime_port(app_base_dir() / "data" / "port.txt", port)

    server_thread = threading.Thread(
        target=lambda: uvicorn.run(app, host=host, port=port, reload=False),
        daemon=True,
    )
    server_thread.start()
    launcher.wait_until_port_ready(host, port)
    webbrowser.open(f"http://{host}:{port}")
    server_thread.join()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())