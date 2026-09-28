#!/bin/bash
# OpenScrcpy Windows 绿色版构建脚本（方案 15 §8.5 任务 4）
#
# 完整构建链路：外置工具 -> 前端构建 -> 干净 venv 安装 -> PyInstaller -> zip。
# 产物：dist/OpenScrcpy/（onedir）与 dist/OpenScrcpy-win64-<VERSION>.zip
#
# 用法（Git Bash / 任意有 python+npm 的 Windows 环境）：
#   bash scripts/build_exe.sh
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="$PWD"

VERSION="0.1.0"  # 与 pyproject.toml 的 project.version 保持同步
PYINSTALLER_PIN="6.22.3"  # spike 实证版本（35c4ad3）

echo "==> [1/6] 获取外置工具（adb 三件套 + scrcpy-server.jar）"
python scripts/fetch_tools.py

echo "==> [2/6] 构建前端（dist 未入库，必须先构建）"
(cd frontend && npm ci && npm run build)

echo "==> [3/6] 准备干净构建 venv（本机 dev 环境已漂移，§9.3-7）"
rm -rf build/venv build/pyi dist/OpenScrcpy
python -m venv build/venv
# shellcheck disable=SC1091
source build/venv/Scripts/activate
python -m pip install --upgrade pip -q
python -m pip install . "pyinstaller==$PYINSTALLER_PIN"

echo "==> [4/6] PyInstaller 构建"
pyinstaller --distpath dist --workpath build/pyi openscrcpy.spec

echo "==> [5/6] 清理运行时数据、注入 stop.bat 并打包 zip（§9.5：zip 不得携带 data/）"
rm -rf dist/OpenScrcpy/data
cp scripts/stop.bat dist/OpenScrcpy/stop.bat
unix2dos dist/OpenScrcpy/stop.bat  # bat 需 CRLF 行尾（LF 下多行块解析有兼容坑）
python -m zipfile -c "dist/OpenScrcpy-win64-$VERSION.zip" dist/OpenScrcpy

echo "==> [6/6] 校验和"
sha256sum "dist/OpenScrcpy-win64-$VERSION.zip"
echo "构建完成：dist/OpenScrcpy-win64-$VERSION.zip"