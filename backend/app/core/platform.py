"""跨平台进程窗口细节（PyInstaller windowed 打包相关）。"""

import subprocess

# windowed 打包（console=False）宿主无控制台：spawn 控制台子系统子进程
# （adb.exe 等）时 Windows 会为其新建可见控制台窗口——打包实测设备轮询
# 周期弹窗闪烁。CREATE_NO_WINDOW 仅在 Windows typeshed 定义，直引会让
# Linux CI 的 mypy 报 attr-defined；getattr 兜底后非 Windows 值为 0，
# Popen 忽略该参数，零副作用。同款模式见 app/infrastructure/adb/winpty.py。
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)