"""scrcpy-server 版本唯一分发点（方案 44 §1）。

只允许本文件声明版本号；其余全仓位置（constants.py、scripts/fetch_tools.py、
tests/manual 脚本等）一律从此读取/推导。升级 scrcpy 时只改这里 + 重新
下载回填 jar 哈希（见 docs/经验记录.md E028 升级 SOP）。

本文件必须保持零 import、零依赖：scripts/ 与 tests/manual/ 以
runpy.run_path 直接执行本文件读取常量，绕开 app 包导入链
（app/scrcpy/__init__.py 会拉起 app.core.config → config.settings，
独立脚本环境不可用）。
"""

SCRCPY_SERVER_VERSION = "5.0.1"
