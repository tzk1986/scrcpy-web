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


def _serve(server_app, host: str, port: int) -> None:
    """以可被在线端点优雅停止的方式跑 uvicorn（dev 与 frozen 共用）。

    uvicorn.run 内部即 Config + Server.run，此处显式持有 Server 实例并
    注册停机回调（闭包置 should_exit 标志，当前响应写完后停机，
    同时触发 FastAPI lifespan 关闭清理）。导入放函数内：frozen 下必须先
    接管标准流再加载任何 app 子模块（见 main 开头注释），不破坏该顺序。
    """
    import uvicorn

    from app.core import launcher

    server = uvicorn.Server(uvicorn.Config(server_app, host=host, port=port, reload=False))

    def _trigger_shutdown() -> None:
        # uvicorn 无 request_* 公开停机方法；should_exit 是主循环轮询的优雅停机
        # 标志（信号处理 handle_exit 亦置此属性，方案 23 §5.2-T2 原文语义）。
        # 置位后当前响应写完才收循环，并触发 lifespan 关闭清理。
        server.should_exit = True

    launcher.register_shutdown_handler(_trigger_shutdown)
    server.run()


def main() -> int:
    frozen = bool(getattr(sys, "frozen", False))
    if frozen:
        # 必须在导入 app 之前接管标准流：--noconsole 下 stdout/stderr 为
        # None，导入期日志写入会崩（§8.4-3）；DLL 搜索路径同理提前（§8.4-2）
        from app.core import launcher
        from config.settings import app_base_dir

        launcher.redirect_std_streams(app_base_dir() / "logs" / "openscrcpy.log")
        launcher.fix_dll_search_path()

    from app.core.config import settings
    from app.main import app  # 传对象：PyInstaller 静态分析可收集 app.main（§9.3-1）

    srv = settings().server

    if not frozen:
        # 开发模式：行为与原版一致（reload=False），仅改为显式持有
        # Server 实例并注册退出回调，支持 UI 退出按钮优雅停机（方案 23 T2）
        _serve(app, srv.host, srv.port)
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
        target=_serve, args=(app, host, port),
        daemon=True,
    )
    server_thread.start()
    if not launcher.wait_until_port_ready(host, port):
        # 服务起不来时不得静默开浏览器到死端口（任务 3 审查 Important F1 修正）：
        # frozen 下 stderr 已重定向到日志文件；非零退出码便于双击场景排查
        print(
            f"[OpenScrcpy] 服务在 {host}:{port} 启动超时，请查看 logs/openscrcpy.log",
            file=sys.stderr,
        )
        return 1
    webbrowser.open(f"http://{host}:{port}")
    server_thread.join()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())