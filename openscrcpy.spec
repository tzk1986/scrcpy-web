# -*- mode: python ; coding: utf-8 -*-
"""
OpenScrcpy Windows 绿色版 PyInstaller spec（方案 15 §8.5 任务 4 / §9.1）

用法（构建脚本 scripts/build_exe.sh 已封装）：
    干净 venv 内：pyinstaller --distpath dist --workpath build/pyi openscrcpy.spec

构建顺序依赖（§9.3-11）：
    1. python scripts/fetch_tools.py        （外置工具三件套 + jar）
    2. cd frontend && npm ci && npm run build

datas 注入目标与冻结后 __file__ 派生解析一一对应（§9.1 表格）：
    tools/            → settings._detect_adb_path：_internal/tools/adb.exe
    app/scrcpy        → server_manager：__file__ 同目录 scrcpy-server.jar
    config            → settings.config_base_dir / config_watch
    frontend/dist     → app.main.FRONTEND_DIST
"""

from pathlib import Path

SPEC_DIR = Path(SPEC).resolve()  # noqa: F821  # PyInstaller 6 提供 SPEC 全局
REPO = SPEC_DIR.parent

WINPTY_BIN_NAMES = ["conpty.dll", "winpty.dll", "OpenConsole.exe", "winpty-agent.exe"]


def _winpty_binaries() -> list[tuple[str, str]]:
    """pywinpty 无官方 hook（§8.4-1）：显式注入包内 4 个非扩展二进制。

    _winpty.cp310-win_amd64.pyd 为扩展模块，PyInstaller 自动收集，
    不在此列。构建机必须已安装 pywinpty（干净 venv pip install .）。
    """
    import pywinpty

    pkg_dir = Path(pywinpty.__file__).parent
    return [(str(pkg_dir / name), "winpty") for name in WINPTY_BIN_NAMES]


YAML_NAMES = ["base.yaml", "dev.yaml", "development.yaml", "e2e.yaml", "prod.yaml", "production.yaml"]

datas = [
    (str(REPO / "tools" / "adb.exe"), "tools"),
    (str(REPO / "tools" / "AdbWinApi.dll"), "tools"),
    (str(REPO / "tools" / "AdbWinUsbApi.dll"), "tools"),
    (str(REPO / "backend" / "app" / "scrcpy" / "scrcpy-server.jar"), "app/scrcpy"),
    (str(REPO / "frontend" / "dist"), "frontend/dist"),
]
datas += [(str(REPO / "config" / name), "config") for name in YAML_NAMES]

a = Analysis(
    [str(REPO / "run_server.py")],
    pathex=[str(REPO), str(REPO / "backend")],  # app/config 两个顶层包（§9.3-2）
    binaries=_winpty_binaries(),
    datas=datas,
    hiddenimports=["app.main"],  # 双保险（§9.3-1）
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["rich", "pygments", "tkinter", "pytest", "ruff", "mypy", "pywin32"],  # §9.3-8
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="OpenScrcpy",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # 关闭 UPX（§8.4-8：报毒特征与启动代价）
    console=False,  # --noconsole（§8.4-3：文件日志由 run_server 接管）
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="OpenScrcpy",
)