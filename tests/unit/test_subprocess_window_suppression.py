"""锁定所有子进程派发带 CREATE_NO_WINDOW（windowed 打包弹窗根因回归）。

首版（方案 15）用固定文件清单，方案 45 T6 在清单外新增 runtime-env 探测派发后
即成回归盲区（设置页打开弹 cmd 一闪而没）——改为全树扫描 backend/app，
任何新增派发点自动纳入门禁。
"""

import ast
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2] / "backend"

_SUBPROCESS_CALLS = {
    "asyncio.create_subprocess_exec",
    "asyncio.create_subprocess_shell",
    "subprocess.run",
    "subprocess.Popen",
    "subprocess.check_output",
    "subprocess.call",
}


def _qualified_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return f"{node.value.id}.{node.attr}"
    return None


def _offending_lines(tree: ast.AST) -> list[int]:
    lines = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _qualified_name(node.func) not in _SUBPROCESS_CALLS:
            continue
        if not any(k.arg == "creationflags" for k in node.keywords):
            lines.append(node.lineno)
    return lines


def test_all_subprocess_spawns_suppress_console_window() -> None:
    offenders: dict[str, list[int]] = {}
    for path in sorted((BACKEND_ROOT / "app").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        lines = _offending_lines(ast.parse(path.read_text(encoding="utf-8")))
        if lines:
            offenders[str(path.relative_to(BACKEND_ROOT))] = lines
    assert not offenders, f"以下子进程派发缺 creationflags=CREATE_NO_WINDOW: {offenders}"
