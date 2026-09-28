"""锁定所有 adb 子进程 spawn 带 CREATE_NO_WINDOW（windowed 打包弹窗根因回归）。"""

import ast
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2] / "backend"

TARGET_FILES = [
    "app/infrastructure/adb/cli.py",
    "app/infrastructure/adb/shell.py",
    "app/infrastructure/stream/scrcpy.py",
    "app/scrcpy/server_manager.py",
]


def test_all_adb_spawns_suppress_console_window() -> None:
    for rel in TARGET_FILES:
        source = (BACKEND_ROOT / rel).read_text(encoding="utf-8")
        tree = ast.parse(source)
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "create_subprocess_exec"
        ]
        assert calls, f"{rel}: 应有 create_subprocess_exec 调用"
        for call in calls:
            kwargs = {kw.arg for kw in call.keywords}
            assert "creationflags" in kwargs, (
                f"{rel}:{call.lineno} 缺少 creationflags（windowed 打包会弹控制台窗）"
            )