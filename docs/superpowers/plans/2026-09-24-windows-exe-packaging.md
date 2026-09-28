# Windows 绿色 exe 打包实施计划（方案 15）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将方案 15 的 7 个剩余任务（0.5~6）落成可双击运行的 Windows 绿色版：外置工具获取、冻结路径感知、前端托管、启动器改造、spec 与构建脚本、发布文档、干净环境全链路真机验证。

**Architecture:** 打包骨架已被 spike 一次性构建实证（35c4ad3：onedir 42MiB/zip 18.9MiB，运行验证全通过）。本计划在骨架上补"体验层"：资源路径全部冻结感知（`config/settings.py` 的 `app_base_dir`/`config_base_dir` 锚定 exe 同级）、`run_server.py` 改造为绿色版入口（传对象启动、单实例、端口探测、文件日志、自动开浏览器）、`openscrcpy.spec` + `scripts/build_exe.sh` 固化可复现构建。数据与配置的"exe 同级优先"策略已由 51534c2 部分落地（data 路径），config 部分在任务 1 完成。

**Tech Stack:** Python 3.10.11（已统一口径）、PyInstaller 6.x（onedir、关 UPX）、hatchling（`pip install .` 发布口径）、Vite/Vue3（`npm ci && npm run build`）、urllib+zipfile（fetch_tools，零新依赖）。

**Spec:** [方案/15-Windows绿色exe打包方案.md](../../../方案/15-Windows绿色exe打包方案.md)（§8.4 外部经验、§8.5 任务分解修订、§8.6 待实测清单、§九外置文件/体积/走查/对标）

## Global Constraints

- **测试位置**：所有测试文件放 `tests/`（单测 `tests/unit/`）；门禁命令（本地 Windows / Git Bash）：
  - `PYTHONPATH=backend:. python -m pytest tests/ --ignore=tests/e2e -q --cov=app --cov-fail-under=80`
  - `python -m ruff check backend/app`、`python -m mypy backend/app`
- **Python 3.10 口径**：ruff `target-version=py310`、mypy `python_version=3.10`、构建用本机 3.10.11（与真机验证环境一致，§8.3-2，勿改回 3.11）。
- **不改坏 dev 运行流程**：源码运行 `python run_server.py`、docker（`uvicorn app.main:app`）、前端 dev 代理（8080→8765）行为不变；每任务完成后必须跑全量 pytest 确认无回归。
- **提交**：每任务一个独立提交；中文提交信息；以 `Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>` 结尾；**不推送**（推送需用户授权）。
- **真机/e2e 验证只本地跑**，不入 CI（`tests/e2e`、`tests/manual` 均在 CI 门禁命令之外）。
- Windows + Git Bash 语境；无 emoji；代码注释用中文，风格与现有代码一致（docstring 用 reST 风格）。
- 若任务执行中遇到与计划不符的根因（如依赖版本行为差异），按 CLAUDE.md 约束 7：先调研根因、经用户确认再改方案，禁止盲改试错。

## 任务重映射裁决（相对方案 §8.5 原文）

| 裁决 | 说明 |
|------|------|
| §8.5 任务 1 的"winpty 4 二进制注入"移入任务 4 | 注入必须写在 spec 的 `binaries` 中，而 spec 文件在任务 4 才入库，拆开会产生无宿主改动 |
| §8.5 任务 1 的"scrcpy_recordings 死代码删除"移入任务 3 | 死代码位于 `run_server.py:35-40`，任务 3 恰恰改造该文件 |
| §8.5 任务 5 死配置清理范围 = `scrcpy_path` + `jwt_secret`/`jwt_algorithm`/`jwt_expire_minutes` | §9.3-9 原文；`cors_origins` 保留（dev 前端 CORS 仍用），不做顺手扩大 |
| §8.4-6 "端口试绑、失败回退并写回配置"实现为"回退端口写入 `data/port.txt`" | 不污染用户 config（只读化管理），第二实例从 port.txt 读端口开浏览器，语义等价且更干净 |
| platform-tools 锁定 r36.0.0 | server jar 锁 v4.1（对齐 `SCRCPY_SERVER_VERSION`=4.1 硬约束）；platform-tools 选 r36.0.0 与本机真机验证环境（adb 1.0.41/36.0.0）一致；Google 不发布官方 SHA256 表，hash 由 `--print-hash` 首轮实测回填（步骤可执行、值由运行产生） |

---

### Task 0.5: scripts/fetch_tools.py（外置工具获取 + SHA256 锁定）+ 删除 aiofiles

**Files:**
- Create: `scripts/fetch_tools.py`
- Create: `tests/unit/test_fetch_tools.py`
- Modify: `pyproject.toml`（删除 `aiofiles>=23.2.0` 依赖行；删除 `[tool.mypy.overrides]` 中的 `"aiofiles"`）

**Interfaces:**
- Consumes: 无（首个任务）。仓库根定位 = `Path(__file__).resolve().parent.parent`。
- Produces:
  - `fetch_tools.TOOLS: list[ZipEntry | FileEntry]` — 四条目清单（三件套 + jar），含 url/member/sha256/target_dir；
  - `fetch_tools.sha256_of(path: Path) -> str`、`fetch_tools.verify(path: Path, expected: str) -> None`（不匹配抛 `SystemExit`）、`fetch_tools.download(url: str, dest: Path) -> None`（`urllib.request.urlretrieve`）、`fetch_tools.ensure_all(print_hash: bool) -> None`、`fetch_tools.main(argv) -> int`；
  - 常量 `fetch_tools.PLATFORM_TOOLS_URL`、`fetch_tools.JAR_URL`（测试 monkeypatch 用）；
  - 落地文件：`tools/adb.exe`、`tools/AdbWinApi.dll`、`tools/AdbWinUsbApi.dll`、`backend/app/scrcpy/scrcpy-server.jar`（任务 4 spec 的 datas 源，任务 6 构建链路依赖）。

- [ ] **Step 1: 写测试（红）**

创建 `tests/unit/test_fetch_tools.py`：

```python
"""
外置工具获取脚本测试（方案 15 任务 0.5）
======================================

不触网：download 一律 monkeypatch 成本地假文件；真实 SHA256 锁定值由
fetch_tools 清单自带（构建可复现），测试只验证机制与清单完整性。
"""

import dataclasses
import hashlib
import sys
import zipfile
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import fetch_tools  # noqa: E402


def _replace_entry(entry, *, sha256=None, target_dir=None):
    kwargs = {}
    if sha256 is not None:
        kwargs["sha256"] = sha256
    if target_dir is not None:
        kwargs["target_dir"] = target_dir
    return dataclasses.replace(entry, **kwargs)


def test_manifest_covers_tools_trio_and_jar():
    names = {e.name for e in fetch_tools.TOOLS}
    assert names == {"adb.exe", "AdbWinApi.dll", "AdbWinUsbApi.dll", "scrcpy-server.jar"}


def test_manifest_hashes_are_locked():
    # 发布构建可复现的前提：四条目 SHA256 全部锁定（64 位十六进制）
    for e in fetch_tools.TOOLS:
        assert len(e.sha256) == 64
        assert all(c in "0123456789abcdef" for c in e.sha256)


def test_jar_url_aligns_server_version():
    # 必须与 app/scrcpy/constants.py 的 SCRCPY_SERVER_VERSION = "4.1" 对齐
    jar = next(e for e in fetch_tools.TOOLS if e.name == "scrcpy-server.jar")
    assert jar.url.endswith("/v4.1/scrcpy-server-v4.1")


def test_zip_entries_point_into_platform_tools_archive():
    for e in fetch_tools.TOOLS:
        if isinstance(e, fetch_tools.ZipEntry):
            assert e.member.startswith("platform-tools/")


def test_sha256_of(tmp_path):
    p = tmp_path / "f.bin"
    p.write_bytes(b"abc")
    assert fetch_tools.sha256_of(p) == hashlib.sha256(b"abc").hexdigest()


def test_verify_mismatch_raises(tmp_path):
    p = tmp_path / "f.bin"
    p.write_bytes(b"abc")
    with pytest.raises(SystemExit):
        fetch_tools.verify(p, "0" * 64)


def _fake_download(mapping):
    def download(url: str, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(mapping[url].read_bytes())
    return download


def test_ensure_all_extracts_zip_members_and_jar(tmp_path, monkeypatch):
    fake_zip = tmp_path / "platform-tools.zip"
    with zipfile.ZipFile(fake_zip, "w") as zf:
        zf.writestr("platform-tools/adb.exe", b"adb-binary")
        zf.writestr("platform-tools/AdbWinApi.dll", b"api-dll")
        zf.writestr("platform-tools/AdbWinUsbApi.dll", b"usb-dll")
    fake_jar = tmp_path / "scrcpy-server"
    fake_jar.write_bytes(b"jar-binary")

    tools_out = tmp_path / "tools_out"
    jar_out = tmp_path / "jar_out"
    locked = [
        _replace_entry(e, sha256=hashlib.sha256(payload).hexdigest(), target_dir=dir_)
        for e, payload, dir_ in [
            (fetch_tools.TOOLS[0], b"adb-binary", tools_out),
            (fetch_tools.TOOLS[1], b"api-dll", tools_out),
            (fetch_tools.TOOLS[2], b"usb-dll", tools_out),
            (fetch_tools.TOOLS[3], b"jar-binary", jar_out),
        ]
    ]
    monkeypatch.setattr(fetch_tools, "TOOLS", locked)
    monkeypatch.setattr(
        fetch_tools,
        "download",
        _fake_download({fetch_tools.PLATFORM_TOOLS_URL: fake_zip, fetch_tools.JAR_URL: fake_jar}),
    )

    fetch_tools.ensure_all(print_hash=False)

    assert (tools_out / "adb.exe").read_bytes() == b"adb-binary"
    assert (tools_out / "AdbWinApi.dll").read_bytes() == b"api-dll"
    assert (tools_out / "AdbWinUsbApi.dll").read_bytes() == b"usb-dll"
    assert (jar_out / "scrcpy-server.jar").read_bytes() == b"jar-binary"


def test_ensure_all_hash_mismatch_fails(tmp_path, monkeypatch):
    # ensure_all 先处理 platform-tools zip 条目，假下载映射必须同时覆盖两个 URL
    fake_zip = tmp_path / "platform-tools.zip"
    with zipfile.ZipFile(fake_zip, "w") as zf:
        zf.writestr("platform-tools/adb.exe", b"adb-binary")
        zf.writestr("platform-tools/AdbWinApi.dll", b"api-dll")
        zf.writestr("platform-tools/AdbWinUsbApi.dll", b"usb-dll")
    fake_jar = tmp_path / "scrcpy-server"
    fake_jar.write_bytes(b"jar-binary")
    locked = [_replace_entry(e, sha256="0" * 64, target_dir=tmp_path / "out") for e in fetch_tools.TOOLS]
    monkeypatch.setattr(fetch_tools, "TOOLS", locked)
    monkeypatch.setattr(
        fetch_tools,
        "download",
        _fake_download({fetch_tools.PLATFORM_TOOLS_URL: fake_zip, fetch_tools.JAR_URL: fake_jar}),
    )
    with pytest.raises(SystemExit):
        fetch_tools.ensure_all(print_hash=False)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=backend:. python -m pytest tests/unit/test_fetch_tools.py -q`
Expected: FAIL（`No module named 'fetch_tools'` / collection error）

- [ ] **Step 3: 实现脚本（SHA256 留空）**

创建 `scripts/fetch_tools.py`：

```python
#!/usr/bin/env python3
"""
外置工具获取脚本（方案 15 §8.5 任务 0.5）
=========================================

从官方源下载并校验外置工具，使发布构建可复现（工具二进制均被
gitignore，无本脚本则换机/CI 无法构建）：

    - adb 三件套（adb.exe + AdbWinApi.dll + AdbWinUsbApi.dll）
      来源：Google Android SDK Platform-Tools（r36.0.0，与本机真机验证
      环境 adb 1.0.41 / 36.0.0 一致），从 zip 内 platform-tools/ 提取
    - scrcpy-server.jar
      来源：Genymobile/scrcpy GitHub Release v4.1（对齐
      app/scrcpy/constants.py 的 SCRCPY_SERVER_VERSION = "4.1"）

SHA256 锁定：清单内 sha256 非空时下载后强制比对，不匹配立即失败；
升级版本时先运行 ``python scripts/fetch_tools.py --print-hash`` 拿到
新 hash 并回填清单（Google 官方不发布 platform-tools 的 SHA256 表，
锁定值以本仓库实测为准）。

用法：
    python scripts/fetch_tools.py              # 下载并校验全部工具
    python scripts/fetch_tools.py --print-hash # 下载并打印实际 SHA256（锁定/升级用）
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = REPO_ROOT / "tools"
JAR_DIR = REPO_ROOT / "backend" / "app" / "scrcpy"
# 注意：Google 自 r36 起 Windows 归档后缀为 -win.zip（r35 及以前是 -windows.zip，
# 实测 r36.0.0 的 -windows.zip 为 404）；升级版本时同步核对后缀
PLATFORM_TOOLS_URL = "https://dl.google.com/android/repository/platform-tools_r36.0.0-win.zip"
JAR_URL = "https://github.com/Genymobile/scrcpy/releases/download/v4.1/scrcpy-server-v4.1"


@dataclass(frozen=True)
class ZipEntry:
    """zip 内提取：下载 zip 后取指定成员落盘。"""

    name: str  # 落盘文件名
    url: str
    member: str  # zip 内成员路径
    sha256: str  # 锁定 hash（64 hex）；空串=未锁定（--print-hash 回填）
    target_dir: Path


@dataclass(frozen=True)
class FileEntry:
    """直接下载的文件。"""

    name: str
    url: str
    sha256: str
    target_dir: Path


TOOLS: list[ZipEntry | FileEntry] = [
    ZipEntry("adb.exe", PLATFORM_TOOLS_URL, "platform-tools/adb.exe", "", TOOLS_DIR),
    ZipEntry("AdbWinApi.dll", PLATFORM_TOOLS_URL, "platform-tools/AdbWinApi.dll", "", TOOLS_DIR),
    ZipEntry("AdbWinUsbApi.dll", PLATFORM_TOOLS_URL, "platform-tools/AdbWinUsbApi.dll", "", TOOLS_DIR),
    FileEntry("scrcpy-server.jar", JAR_URL, "", JAR_DIR),
]


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(url, dest)  # noqa: S310  # 固定官方 URL，非用户输入


def verify(path: Path, expected: str) -> None:
    actual = sha256_of(path)
    if expected and actual != expected:
        raise SystemExit(f"SHA256 mismatch for {path.name}: expected {expected}, got {actual}")


def ensure_all(print_hash: bool = False) -> None:
    archive_cache: dict[str, Path] = {}
    with tempfile.TemporaryDirectory(prefix="openscrcpy-tools-") as tmp:
        tmp_dir = Path(tmp)
        for entry in TOOLS:
            if isinstance(entry, ZipEntry):
                if entry.url not in archive_cache:
                    archive = tmp_dir / f"zip-{len(archive_cache)}.zip"
                    download(entry.url, archive)
                    archive_cache[entry.url] = archive
                with zipfile.ZipFile(archive_cache[entry.url]) as zf:
                    payload = zf.read(entry.member)
                target = entry.target_dir / entry.name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
            else:
                target = entry.target_dir / entry.name
                download(entry.url, target)
            actual = sha256_of(target)
            print(f"{entry.name}: sha256={actual}")
            if print_hash:
                continue
            verify(target, entry.sha256)
    print("OK: all tools fetched")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="下载并校验外置工具（方案 15 任务 0.5）")
    parser.add_argument(
        "--print-hash",
        action="store_true",
        help="下载后打印实际 SHA256 而不校验（首次锁定/升级版本用，回填进 TOOLS 清单）",
    )
    ensure_all(print_hash=parser.parse_args(argv).print_hash)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: 删除 aiofiles（§9.3-7 零使用）**

`pyproject.toml`：删除 `dependencies` 中的 `    "aiofiles>=23.2.0",` 行；删除 mypy overrides 中的 `"aiofiles"`（该行变为 `module = ["yaml", "winpty", "structlog.*", "aiosqlite"]`）。

- [ ] **Step 5: 回填 SHA256（联网，官方源约 12MB）**

Run: `python scripts/fetch_tools.py --print-hash`
Then: 把四个打印值回填 `scripts/fetch_tools.py` 的 `TOOLS` 清单（`sha256=""` → 实测值）。回填后再次运行 `python scripts/fetch_tools.py` 确认 `OK: all tools fetched` 且 `tools/`、`backend/app/scrcpy/scrcpy-server.jar` 到位（`git status` 确认这些文件仍被 gitignore，不会进入提交）。

- [ ] **Step 6: 跑测试与门禁**

Run: `PYTHONPATH=backend:. python -m pytest tests/unit/test_fetch_tools.py -q` → PASS
Run: `python -m ruff check backend/app scripts`、`python -m mypy backend/app`、全量 `PYTHONPATH=backend:. python -m pytest tests/ --ignore=tests/e2e -q --cov=app --cov-fail-under=80` → 全绿（aiofiles 删除后应无引用报错；若有引用说明 §9.3-7 结论过时，停下调研而非盲改）

- [ ] **Step 7: 提交**

```bash
git add scripts/fetch_tools.py tests/unit/test_fetch_tools.py pyproject.toml
git commit -m "feat: 新增 fetch_tools.py 外置工具获取脚本（方案 15 任务 0.5）

官方源下载 adb 三件套（platform-tools r36.0.0）与 scrcpy-server v4.1
jar，SHA256 锁定保证可复现构建；顺手删除零使用的 aiofiles 依赖。

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 1: config 冻结感知（exe 同级优先）+ config_watch 同步

**Files:**
- Modify: `config/settings.py`（`app_base_dir` 重构出 `_frozen_base_dir`；新增 `config_base_dir`；`load_yaml_config` 改用 `config_base_dir`）
- Modify: `backend/app/core/config_watch.py`（删除模块级 `CONFIG_DIR` 常量，新增 `config_dir()` 函数，`watched_paths` 改调用）
- Create: `tests/unit/test_config_frozen_paths.py`

**Interfaces:**
- Consumes: 任务 0.5 无接口；现有 `_is_writable`（settings.py 内部）、现有测试 `tests/unit/test_config_data_path.py`（必须保持全绿——`app_base_dir` 语义不变）。
- Produces:
  - `config.settings._frozen_base_dir() -> Path | None`（frozen：exe 同级可写→exe 同级；不可写→`%LOCALAPPDATA%/OpenScrcpy`；源码运行 None）；
  - `config.settings.config_base_dir() -> Path`（frozen：exe 同级 `config/` 存在 `base.yaml` 才用，否则 bundle 内 `Path(__file__).parent`；源码：仓库 `config/`）——任务 4 spec 的 datas 注入目标 `config` 与之对齐；
  - `app.core.config_watch.config_dir() -> Path`（返回 `config_settings.config_base_dir()`）——`watched_paths()` 动态调用，热重载监听与 YAML 加载点保持同一目录。

- [ ] **Step 1: 写测试（红）**

创建 `tests/unit/test_config_frozen_paths.py`：

```python
"""
冻结环境 config 路径解析测试（方案 15 任务 1）
==============================================

覆盖 §8.4-7 / §9.3-4：
1. load_yaml_config：frozen 时优先 exe 同级 config/（存在 base.yaml 才生效），
   否则回落 bundle 内 basconfig
2. config_watch.watched_paths 与加载路径同步（§9.3-4：CONFIG_DIR 独立常量
   必须同改，否则热重载仍监听 bundle 内文件）
"""

import sys
from pathlib import Path

import pytest

import config.settings as settings_module
from config.settings import load_yaml_config

REPO_CONFIG = Path(settings_module.__file__).resolve().parent


@pytest.fixture
def _fake_frozen(monkeypatch, tmp_path):
    """假装冻结运行于 tmp_path/OpenScrcpy.exe（可写）。"""
    exe = tmp_path / "OpenScrcpy.exe"
    exe.write_bytes(b"")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    return tmp_path


def test_source_run_uses_repo_config():
    assert load_yaml_config("base")["app"]["name"] == "OpenScrcpy"


def test_frozen_prefers_exe_side_config(_fake_frozen):
    exe_config = _fake_frozen / "config"
    exe_config.mkdir()
    (exe_config / "base.yaml").write_text("app:\n  name: ExeSide\n", encoding="utf-8")
    assert load_yaml_config("base")["app"]["name"] == "ExeSide"


def test_frozen_falls_back_to_bundle_config(_fake_frozen):
    # exe 同级无 config/base.yaml → 读回 bundle 内（仓库 config/）
    assert load_yaml_config("base")["app"]["name"] == "OpenScrcpy"


def test_frozen_exe_side_env_file_preferred(_fake_frozen, monkeypatch):
    monkeypatch.setenv("APP_ENV", "dev")
    exe_config = _fake_frozen / "config"
    exe_config.mkdir()
    (exe_config / "base.yaml").write_text("app:\n  name: ExeSide\n", encoding="utf-8")
    (exe_config / "dev.yaml").write_text("app:\n  name: ExeSideDev\n", encoding="utf-8")
    assert load_yaml_config("dev")["app"]["name"] == "ExeSideDev"


def test_watched_paths_tracks_exe_side_config(_fake_frozen):
    from app.core.config_watch import watched_paths

    exe_config = _fake_frozen / "config"
    exe_config.mkdir()
    (exe_config / "base.yaml").write_text("app:\n  name: ExeSide\n", encoding="utf-8")
    assert watched_paths()[0] == exe_config / "base.yaml"


def test_watched_paths_defaults_to_repo_config():
    from app.core.config_watch import watched_paths

    assert watched_paths()[0] == REPO_CONFIG / "base.yaml"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=backend:. python -m pytest tests/unit/test_config_frozen_paths.py -q`
Expected: FAIL（`load_yaml_config("base")` 读不到 `ExeSide` / `watched_paths()` 仍指向仓库 config——前 4 个用例红）

- [ ] **Step 3: 实现 settings.py 改造**

`config/settings.py` 中：把 `app_base_dir` 拆出 `_frozen_base_dir`（位于 `_is_writable` 之后），新增 `config_base_dir`，`load_yaml_config` 换目录来源：

```python
def _frozen_base_dir() -> Path | None:
    """冻结运行的基目录：exe 同级（可写时）；不可写（Program Files 等）回落
    %LOCALAPPDATA%/OpenScrcpy。源码运行返回 None。"""
    if not getattr(sys, "frozen", False):
        return None
    exe_dir = Path(sys.executable).parent
    if _is_writable(exe_dir):
        return exe_dir
    local_appdata = os.environ.get("LOCALAPPDATA")
    return Path(local_appdata) / "OpenScrcpy" if local_appdata else exe_dir


def app_base_dir() -> Path:
    """
    应用数据基目录：数据等相对路径的锚点。

    - 源码运行：仓库根（不随启动 cwd 漂移）
    - 冻结运行（PyInstaller）：exe 同级（绿色版可整体迁移/备份）；
      同级不可写（如 Program Files）时回落 %LOCALAPPDATA%/OpenScrcpy
    """
    frozen_dir = _frozen_base_dir()
    return frozen_dir if frozen_dir is not None else Path(__file__).parent.parent


def config_base_dir() -> Path:
    """
    配置文件搜索目录（load_yaml_config 与 config_watch 共用）。

    - 源码运行：仓库 config/
    - 冻结运行：exe 同级 config/ 优先（存在 base.yaml 才生效，用户可改
      并触发热重载），否则回落 bundle 内 config/（只读基线）
    """
    frozen_dir = _frozen_base_dir()
    if frozen_dir is not None:
        exe_config = frozen_dir / "config"
        if (exe_config / "base.yaml").exists():
            return exe_config
    return Path(__file__).parent
```

`load_yaml_config` 内 `config_dir = Path(__file__).parent` 改为 `config_dir = config_base_dir()`（函数体其余不变）。

- [ ] **Step 4: 实现 config_watch.py 同步**

`backend/app/core/config_watch.py`：

```python
CONFIG_DIR = Path(config_settings.__file__).parent
```

删除（grep 已确认 `CONFIG_DIR` 仅 `watched_paths` 内部引用，无外部依赖），替换为：

```python
def config_dir() -> Path:
    """当前配置文件目录（frozen 下 exe 同级优先），与 config.settings 的加载路径一致。"""
    return config_settings.config_base_dir()
```

`watched_paths()` 内两处 `CONFIG_DIR` 改为 `config_dir()`。

- [ ] **Step 5: 跑测试与门禁**

Run: `PYTHONPATH=backend:. python -m pytest tests/unit/test_config_frozen_paths.py tests/unit/test_config_data_path.py -q` → PASS（data 路径旧测试必须保持全绿：`app_base_dir` 语义未变）
Run: 全量 `PYTHONPATH=backend:. python -m pytest tests/ --ignore=tests/e2e -q --cov=app --cov-fail-under=80`、`python -m ruff check backend/app`、`python -m mypy backend/app` → 全绿

- [ ] **Step 6: 提交**

```bash
git add config/settings.py backend/app/core/config_watch.py tests/unit/test_config_frozen_paths.py
git commit -m "feat: config 冻结感知——exe 同级优先 + config_watch 同步（方案 15 任务 1）

frozen 下配置文件优先读 exe 同级 config/（存在 base.yaml 才生效），
否则回落 bundle 内；config_watch 的 CONFIG_DIR 常量改为动态取值，
热重载监听与加载点保持同一目录（§9.3-4）。

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 2: 前端 StaticFiles 托管 + SPA fallback

**Files:**
- Modify: `backend/app/main.py`（新增 `FRONTEND_DIST` 常量 + create_app 内条件挂载 assets + catch-all fallback）
- Create: `tests/unit/test_static_frontend.py`

**Interfaces:**
- Consumes: 无跨任务依赖；uses `FILE`/`FRONTEND_DIST`（新建）。`tests/unit/http_testkit.py` 的 `make_client()` 绑定默认 app 实例，本任务测试需直接 `TestClient(create_app(), raise_server_exceptions=False)`。
- Produces:
  - `app.main.FRONTEND_DIST: Path` = `Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"`（源码=仓库 `frontend/dist`；frozen=`<产物根>/frontend/dist`，两条推导一致，任务 4 spec 注入目标 `frontend/dist` 与之对齐）；
  - `create_app()` 在 dist 存在时挂载 `/assets` 静态目录 + 注册 catch-all `GET /{frontend_path:path}`（SPA fallback，history 路由必选项 §9.3-10）。

- [ ] **Step 1: 写测试（红）**

创建 `tests/unit/test_static_frontend.py`：

```python
"""
前端静态托管与 SPA fallback 测试（方案 15 任务 2）
==================================================

覆盖 §8.5 任务 2 / §9.3-10：dist 存在时挂载静态资源，
history 深链 fallback 到 index.html，API 路由优先命中，
路径不得逃逸 dist 目录（../ 防护）。
"""

import sys
from pathlib import Path

import app.main as main_module
from app.main import create_app
from fastapi.testclient import TestClient


def _make_fake_dist(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>SPA</title>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text('console.log("assets")', encoding="utf-8")
    (dist / "about.html").write_text("about-page", encoding="utf-8")
    return dist


def _client_with_dist(tmp_path, monkeypatch):
    # 不用 with：不触发 lifespan 副作用（与项目其他 TestClient 用例一致）
    monkeypatch.setattr(main_module, "FRONTEND_DIST", _make_fake_dist(tmp_path))
    return TestClient(create_app(), raise_server_exceptions=False)


def test_frontend_dist_source_points_repo():
    assert main_module.FRONTEND_DIST == (
        Path(main_module.__file__).resolve().parent.parent.parent / "frontend" / "dist"
    )


def test_frontend_dist_frozen_uses_meipass(monkeypatch, tmp_path):
    # frozen 下 __file__ = _MEIPASS/app/main.py，dist 在 _MEIPASS/frontend/dist
    # （上溯级数与源码不同，审查 Critical 回归锁定）
    fake_meipass = tmp_path / "_internal"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(fake_meipass), raising=False)
    assert main_module._compute_frontend_dist() == fake_meipass / "frontend" / "dist"


def test_index_served_at_root(tmp_path, monkeypatch):
    client = _client_with_dist(tmp_path, monkeypatch)
    r = client.get("/")
    assert r.status_code == 200
    assert "SPA" in r.text


def test_deep_link_falls_back_to_index(tmp_path, monkeypatch):
    # history 路由（createWebHistory 已实测）：/devices/abc 无对应文件 → index.html
    client = _client_with_dist(tmp_path, monkeypatch)
    r = client.get("/devices/abc")
    assert r.status_code == 200
    assert "SPA" in r.text


def test_assets_directory_served(tmp_path, monkeypatch):
    client = _client_with_dist(tmp_path, monkeypatch)
    r = client.get("/assets/app.js")
    assert r.status_code == 200
    assert "assets" in r.text


def test_real_file_in_dist_served(tmp_path, monkeypatch):
    client = _client_with_dist(tmp_path, monkeypatch)
    r = client.get("/about.html")
    assert r.status_code == 200
    assert r.text == "about-page"


def test_api_routes_take_precedence(tmp_path, monkeypatch):
    client = _client_with_dist(tmp_path, monkeypatch)
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_path_traversal_blocked(tmp_path, monkeypatch):
    secret = tmp_path / "secret.txt"
    secret.write_text("top-secret", encoding="utf-8")
    client = _client_with_dist(tmp_path, monkeypatch)
    # httpx 会把 /../secret.txt 归一化为 /secret.txt（触达不到服务端防护），
    # 用百分号编码形式让服务端真实收到逃逸路径，验证 containment 防护生效
    r = client.get("/%2e%2e%2fsecret.txt")
    assert "top-secret" not in r.text
    assert "SPA" in r.text


def test_unknown_api_path_stays_404(tmp_path, monkeypatch):
    # API 命名空间未注册路径不得被 SPA fallback 吞成 200 HTML
    client = _client_with_dist(tmp_path, monkeypatch)
    assert client.get("/api/nonexistent").status_code == 404
    assert client.get("/api").status_code == 404
    # httpx 的 get() 不接受 method= 参数（实测 0.28.1 TypeError），用 head()/post()
    assert client.head("/api/nonexistent").status_code == 404
    assert client.post("/api/nonexistent").status_code != 200


def test_no_dist_no_mount(tmp_path, monkeypatch):
    monkeypatch.setattr(main_module, "FRONTEND_DIST", tmp_path / "nonexistent")
    client = TestClient(create_app(), raise_server_exceptions=False)
    r = client.get("/")
    assert r.status_code == 404
    assert client.get("/health").status_code == 200
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=backend:. python -m pytest tests/unit/test_static_frontend.py -q`
Expected: FAIL（index 未托管：`/` 404）

- [ ] **Step 3: 实现 main.py 改造**

`backend/app/main.py`：
1. imports 增加 `from pathlib import Path`、`from fastapi import HTTPException`、`from fastapi.responses import FileResponse`、`from fastapi.staticfiles import StaticFiles`；
2. 模块级常量（`create_app` 之前）——**frozen 感知推导（Task 2 审查 Critical 修正）**：

```python
def _compute_frontend_dist() -> Path:
    """
    前端 dist 目录。

    - 源码运行：仓库根（backend/app/main.py 上溯三级）→ <仓库>/frontend/dist
    - 冻结运行（PyInstaller 6 onedir）：bundle 根 sys._MEIPASS（= <产物>/_internal）
      → <产物>/_internal/frontend/dist，与 spec datas 目标 "frontend/dist" 一致。
      注意：frozen 下 __file__ = _MEIPASS/app/main.py，上溯级数与源码不同
      （无 backend/ 段），不能复用同一表达式。
    """
    if getattr(sys, "frozen", False):
        # PyInstaller 6 冻结运行时必设 _MEIPASS（onedir 下 = <产物>/_internal）；
        # 若缺失宁可导入时报错（响亮暴露构建异常）也不静默指向错误路径
        return Path(getattr(sys, "_MEIPASS")) / "frontend" / "dist"
    return Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"


FRONTEND_DIST = _compute_frontend_dist()
```

3. `create_app()` 末尾（`return app` 之前）追加：

```python
    # --- 前端静态托管（方案 15 任务 2）--------------------------------------
    # dist 完整（已构建或打包注入，index.html 存在为完整性判据）才挂载；
    # 源码运行未构建/构建中断时跳过，避免 fallback 指向不存在的文件。
    # 顺序敏感：本块必须在所有 API/WS 路由之后，否则 catch-all 会吞掉 API。
    if FRONTEND_DIST.is_dir() and (FRONTEND_DIST / "index.html").is_file():
        root = FRONTEND_DIST.resolve()
        assets_dir = root / "assets"
        if assets_dir.is_dir():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

        # 用 api_route 而非 @app.get：FastAPI 的 get() 不接受 methods= 参数
        # （实测 0.141.1 TypeError），@app.api_route 是注册多方法路由的正式入口
        @app.api_route("/{frontend_path:path}", methods=["GET", "HEAD"], include_in_schema=False)
        async def spa_fallback(frontend_path: str) -> FileResponse:
            # API/WS 命名空间（首路径段）的未匹配路径保持 404 语义，
            # 不被前端 fallback 吞成 200 HTML
            if frontend_path.split("/", 1)[0] in ("api", "ws"):
                raise HTTPException(status_code=404, detail="Not Found")
            # 命中 dist 内真实文件则直接返回（favicon 等根级资源），
            # 否则一律 fallback 到 index.html（createWebHistory 深链/刷新）；
            # containment 判定先于 stat，杜绝符号链接/编码逃逸路径被直接返回
            candidate = (root / frontend_path).resolve()
            if frontend_path and candidate.is_relative_to(root) and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(root / "index.html")
```

- [ ] **Step 4: 跑测试与门禁**

Run: `PYTHONPATH=backend:. python -m pytest tests/unit/test_static_frontend.py -q` → PASS
Run: 全量 `PYTHONPATH=backend:. python -m pytest tests/ --ignore=tests/e2e -q --cov=app --cov-fail-under=80`、`python -m ruff check backend/app`、`python -m mypy backend/app` → 全绿（尤其确认既有路由/WS 测试无回归——catch-all 位置正确则 API 全命中）

- [ ] **Step 5: 提交**

```bash
git add backend/app/main.py tests/unit/test_static_frontend.py
git commit -m "feat: 前端 StaticFiles 托管 + SPA fallback（方案 15 任务 2）

dist 存在时挂载 /assets 并注册 catch-all：真实文件直返、深链回退
index.html；路径解析做 dist 目录逃逸防护；API 路由优先命中。
history 路由绿深链/刷新 404 问题（§9.3-10）。

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

---

### Task 3: 绿色版启动器（backend/app/core/launcher.py + run_server.py 改造）

**Files:**
- Create: `backend/app/core/launcher.py`
- Modify: `run_server.py`（改造入口：传对象、frozen 分支、删除 scrcpy_recordings 死代码）
- Create: `tests/unit/test_launcher.py`

**Interfaces:**
- Consumes: 任务 1 的 `config.settings.app_base_dir()`（日志与 port.txt 落点）；`backend/app/main.py` 的 `app` 对象。
- Produces（任务 6 验证与后续打包依赖）:
  - `launcher.fix_dll_search_path() -> None`（`SetDllDirectoryW(None)`，§8.4-2）；
  - `launcher.acquire_single_instance_mutex(name: str = _SINGLE_INSTANCE_MUTEX) -> bool`（已存在返回 False，§8.4-6）；
  - `launcher.find_available_port(host: str, start_port: int, attempts: int = 5) -> int`（全失败抛 `RuntimeError`）；
  - `launcher.redirect_std_streams(log_file: Path) -> None`（--noconsole 下 stdout/stderr=None → 文件，§8.4-3）；
  - `launcher.read_runtime_port(port_file: Path) -> int | None` / `launcher.write_runtime_port(port_file: Path, port: int) -> None`；
  - `launcher.wait_until_port_ready(host: str, port: int, timeout: float = 15.0) -> bool`；
  - `launcher.resolve_bind_host(configured: str, *, frozen: bool, explicit_env: str | None) -> str`；
  - `run_server.main() -> int`：frozen 分支全流程（日志重定向 → mutex → 端口探测 → 后台 uvicorn → 就绪开浏览器）；源码运行分支与原行为完全一致（**dev 流程不回退**，硬约束）。

- [ ] **Step 1: 写测试（红）**

创建 `tests/unit/test_launcher.py`：

```python
"""
绿色版启动器辅助函数测试（方案 15 任务 3）
==========================================

覆盖 §8.4-2/3/6、§9.3-5：DLL 搜索路径修复、单实例互斥、端口探测
回退、文件日志重定向、就绪轮询、frozen 绑定 127.0.0.1。
"""

import socket
import sys
import uuid

import pytest

from app.core import launcher


def test_resolve_bind_host_frozen_defaults_loopback():
    assert launcher.resolve_bind_host("0.0.0.0", frozen=True, explicit_env=None) == "127.0.0.1"


def test_resolve_bind_host_explicit_env_wins():
    assert launcher.resolve_bind_host("0.0.0.0", frozen=True, explicit_env="0.0.0.0") == "0.0.0.0"


def test_resolve_bind_host_source_run_keeps_configured():
    assert launcher.resolve_bind_host("0.0.0.0", frozen=False, explicit_env=None) == "0.0.0.0"


def test_find_available_port_skips_occupied():
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", 0))
        probe.listen(1)
        occupied = probe.getsockname()[1]
        assert launcher.find_available_port("127.0.0.1", occupied, attempts=2) == occupied + 1
    finally:
        probe.close()


def test_find_available_port_exhausted_raises():
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", 0))
        probe.listen(1)
        occupied = probe.getsockname()[1]
        with pytest.raises(RuntimeError):
            launcher.find_available_port("127.0.0.1", occupied, attempts=1)
    finally:
        probe.close()


def test_port_file_roundtrip(tmp_path):
    launcher.write_runtime_port(tmp_path / "port.txt", 8766)
    assert launcher.read_runtime_port(tmp_path / "port.txt") == 8766


def test_read_runtime_port_missing_or_garbage_returns_none(tmp_path):
    assert launcher.read_runtime_port(tmp_path / "missing.txt") is None
    garbage = tmp_path / "garbage.txt"
    garbage.write_text("not-a-port", encoding="utf-8")
    assert launcher.read_runtime_port(garbage) is None


def test_wait_until_port_ready_true():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        assert launcher.wait_until_port_ready("127.0.0.1", port, timeout=2.0)
    finally:
        server.close()


def test_wait_until_port_ready_timeout():
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()  # 释放后无人监听（小概率被抢注，取 64xxx-65xxx 之外的高端口）
    assert launcher.wait_until_port_ready("127.0.0.1", port, timeout=0.3) is False


def test_redirect_std_streams_writes_to_file(tmp_path):
    old_out, old_err = sys.stdout, sys.stderr
    try:
        launcher.redirect_std_streams(tmp_path / "logs" / "openscrcpy.log")
        print("hello-from-launcher")
        assert sys.stdout is not None and sys.stderr is not None
        sys.stdout.flush()
        content = (tmp_path / "logs" / "openscrcpy.log").read_text(encoding="utf-8")
        assert "hello-from-launcher" in content
    finally:
        sys.stdout, sys.stderr = old_out, old_err


def test_single_instance_mutex_second_acquire_false():
    name = f"openscrcpy-test-mutex-{uuid.uuid4().hex}"
    assert launcher.acquire_single_instance_mutex(name) is True
    assert launcher.acquire_single_instance_mutex(name) is False


def test_fix_dll_search_path_noop_on_non_frozen():
    launcher.fix_dll_search_path()  # 不抛异常即可（pty 环境无 win32 断言）
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=backend:. python -m pytest tests/unit/test_launcher.py -q`
Expected: FAIL（`No module named 'app.core.launcher'`）

- [ ] **Step 3: 实现 launcher.py**

创建 `backend/app/core/launcher.py`：

```python
"""
绿色版启动器辅助（方案 15 §8.5 任务 3 / §8.4-2/3/6、§9.3-5）
=============================================================

仅在 PyInstaller 冻结环境（sys.frozen）使用：文件日志重定向、
SetDllDirectoryW 修复、单实例互斥、端口探测回退、就绪轮询后开浏览器。
源码运行（dev）不经过本模块，行为保持不变。
"""

from __future__ import annotations

import ctypes
import socket
import sys
import time
from pathlib import Path

_SINGLE_INSTANCE_MUTEX = "OpenScrcpy-SingleInstance-Mutex"
_ERROR_ALREADY_EXISTS = 183


def fix_dll_search_path() -> None:
    """解除 PyInstaller bootloader 对子进程的 DLL 搜索路径限制（§8.4-2）。"""
    if sys.platform == "win32":
        ctypes.windll.kernel32.SetDllDirectoryW(None)


def acquire_single_instance_mutex(name: str = _SINGLE_INSTANCE_MUTEX) -> bool:
    """Windows CreateMutex 单实例锁：已存在（另一实例在跑）返回 False。

    句柄不释放——进程退出时由系统回收，持有期即实例存活期。
    非 Windows 恒返回 True（打包目标仅 Windows）。
    """
    if sys.platform != "win32":
        return True
    # use_last_error 必须走 WinDLL 构造参数：缓存的 _NamedFuncPointer 没有
    # use_last_error 实例属性（对函数指针直接赋值是静默 no-op，get_last_error
    # 恒 0——任务 3 修复轮实证）；构造参数把 _FUNCFLAG_USELASTERROR 写入该
    # DLL 全部函数指针，调用后紧跟捕获 GetLastError，规避中间调用污染
    create_mutex = ctypes.WinDLL("kernel32", use_last_error=True).CreateMutexW
    create_mutex.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    create_mutex.restype = ctypes.c_void_p
    create_mutex(None, False, name)
    return ctypes.get_last_error() != _ERROR_ALREADY_EXISTS


def find_available_port(host: str, start_port: int, attempts: int = 5) -> int:
    """从 start_port 起逐个试绑（探测后立即释放），返回第一个可用端口。

    Windows 下 SO_REUSEADDR 不阻止重复绑定，改用 SO_EXCLUSIVEADDRUSE
    （8.4-6 端口冲突回退的探测正确性依赖此语义）。
    """
    for offset in range(attempts):
        port = start_port + offset
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            except (AttributeError, OSError):
                pass  # 非 Windows 无此选项（AttributeError），默认语义已足够
            try:
                sock.bind((host, port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"no available port from {start_port} to {start_port + attempts - 1}")


def redirect_std_streams(log_file: Path) -> None:
    """--noconsole 下 stdout/stderr 为 None，重定向到日志文件（§8.4-3）。

    structlog 的 PrintLoggerFactory 与 uvicorn 默认日志都走这两条流，
    重定向后统一落文件；行缓冲便于 DebugView/tail 实时排错。
    """
    log_file.parent.mkdir(parents=True, exist_ok=True)
    stream = open(log_file, "a", encoding="utf-8", buffering=1)
    sys.stdout = stream
    sys.stderr = stream


def read_runtime_port(port_file: Path) -> int | None:
    try:
        return int(port_file.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def write_runtime_port(port_file: Path, port: int) -> None:
    port_file.parent.mkdir(parents=True, exist_ok=True)
    port_file.write_text(str(port), encoding="utf-8")


def wait_until_port_ready(host: str, port: int, timeout: float = 15.0) -> bool:
    """TCP 连通即返回 True，超时返回 False（慢机场景由调用方决定重试）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def resolve_bind_host(configured: str, *, frozen: bool, explicit_env: str | None) -> str:
    """冻结环境默认绑 127.0.0.1（防火墙弹窗 + WebCodecs 安全上下文双重理由，
    §9.3-5）；BACKEND_HOST 环境变量显式设置时尊重用户选择。"""
    if explicit_env:
        return explicit_env
    if frozen:
        return "127.0.0.1"
    return configured
```

- [ ] **Step 4: 改造 run_server.py**

整文件重写为（删除原 :35-40 的 scrcpy_recordings 死代码——backend 无生产者，§8.5 任务 1 已裁决移入本任务）：

```python
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
```

注意：`launcher`/`app_base_dir`/`uvicorn` 在 frozen 与源码两条路径各 import 一次，mypy strict 下重复导入合法；若 ruff 报 `PLC0415`（import-outside-toplevel）则保留现状——入口脚本的冻结分支必须延迟导入（stdout 接管先于 import），这是有意为之，在计划层面预授权该告警的 noqa/忽略。

- [ ] **Step 5: 跑测试与门禁**

Run: `PYTHONPATH=backend:. python -m pytest tests/unit/test_launcher.py -q` → PASS
Run: `PYTHONPATH=backend:. python -m pytest tests/ --ignore=tests/e2e -q --cov=app --cov-fail-under=80`、`python -m ruff check backend/app`、`python -m mypy backend/app` → 全绿
再手工冒烟 dev 流程：`python run_server.py` 正常启动 → `curl http://localhost:8765/health` 返回 `{"status":"ok"}` → Ctrl+C 停止（验证源码运行零回退）

- [ ] **Step 6: 提交**

```bash
git add backend/app/core/launcher.py run_server.py tests/unit/test_launcher.py
git commit -m "feat: 绿色版启动器改造（方案 15 任务 3）

run_server 冻结分支：文件日志接管、SetDllDirectoryW、单实例互斥、
端口探测回退（port.txt 互认）、就绪后自动开浏览器、默认绑 127.0.0.1；
入口改传 app 对象（§9.3-1）；删除 scrcpy_recordings 死代码。
dev 源码运行行为不变。

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 4: openscrcpy.spec 入库 + scripts/build_exe.sh 构建脚本

**Files:**
- Create: `openscrcpy.spec`
- Create: `scripts/build_exe.sh`
- 无测试文件（spec/脚本属构建资产，由任务 6 全链路验证覆盖）

**Interfaces:**
- Consumes: 任务 0.5 的产物（`tools/` 三件套、`backend/app/scrcpy/scrcpy-server.jar`——spec datas 源）；任务 1 的 `config_base_dir()`（datas 注入目标 `config` 与其冻结后 `__file__` 解析一致）；任务 2 的 `FRONTEND_DIST`（注入目标 `frontend/dist`）；任务 3 的 `run_server.py`（Analysis 入口脚本）；winpty 注入（§8.5 任务 1 移入项，spike 已实证 4 文件收全）。
- Produces: `pyinstaller --distpath dist --workpath build/pyi openscrcpy.spec` → `dist/OpenScrcpy/OpenScrcpy.exe`；`scripts/build_exe.sh` → `dist/OpenScrcpy-win64-<version>.zip`（排除运行时 data/）+ 输出 sha256sum。任务 6 消费。

- [ ] **Step 1: 写 openscrcpy.spec**

创建 `openscrcpy.spec`：

```python
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
```

- [ ] **Step 2: 写 scripts/build_exe.sh**

创建 `scripts/build_exe.sh`：

```bash
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

echo "==> [5/6] 清理运行时数据并打包 zip（§9.5：zip 不得携带 data/）"
rm -rf dist/OpenScrcpy/data
python -m zipfile -c "dist/OpenScrcpy-win64-$VERSION.zip" dist/OpenScrcpy

echo "==> [6/6] 校验和"
sha256sum "dist/OpenScrcpy-win64-$VERSION.zip"
echo "构建完成：dist/OpenScrcpy-win64-$VERSION.zip"
```

- [ ] **Step 3: 干跑验证**

Run: `bash -n scripts/build_exe.sh`（语法）＋ `python -c "import ast; ast.parse(open('openscrcpy.spec', encoding='utf-8').read())"`（spec 语法）
再快速全量烟测（可选，约 30s 构建）：`bash scripts/build_exe.sh`——若用户暂不允许完整构建，则跳过并在任务 6 一并验证；跳过需在提交信息注明"构建验证留待任务 6"。

- [ ] **Step 4: 提交**

```bash
git add openscrcpy.spec scripts/build_exe.sh
git commit -m "feat: PyInstaller spec 与构建脚本入库（方案 15 任务 4）

datas/pathex/hiddenimports/excludes 按 §9.1 表格与 spike 实证对齐；
winpty 4 二进制显式注入（§8.4-1）；构建脚本封装
fetch_tools -> npm build -> 干净 venv -> pyinstaller -> zip 全链路。

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 5: 死配置清理 + 发布文档

**Files:**
- Modify: `config/settings.py`（删除 `StreamConfig.scrcpy_path`、`SecurityConfig.jwt_secret/jwt_algorithm/jwt_expire_minutes`）
- Modify: `config/base.yaml`（删除 `security.jwt_algorithm/jwt_expire_minutes` 两行）
- Create: `docs/Windows-exe-发布说明.md`
- 无测试改动（删除零引用字段；现有测试对这几项无断言——grep 已确认 backend/config/tests 除定义处外零引用）

**Interfaces:**
- Consumes: 任务 4 的构建产物形态（发布文档描述之）。
- Produces: 发布文档 `docs/Windows-exe-发布说明.md`——任务 6 验证清单与其对照；§8.4-6/§9.3-13/§9.4 要求的全部用户可见说明（adb 5037 冲突、升级数据保留、杀软、局域网限制、DebugView 排错）。

- [ ] **Step 1: 死配置清理**

`config/settings.py`：
1. 删除 `StreamConfig` 中的 `scrcpy_path` 行（`scrcpy_path: str = Field(default="D:/scrcpy-win64-v4.1/scrcpy.exe", alias="SCRCPY_PATH")` 及上方注释）——后端只用 jar 直连协议，不依赖 scrcpy.exe（§8.7）；
2. 删除 `SecurityConfig` 中的 `jwt_secret`、`jwt_algorithm`、`jwt_expire_minutes` 三行——JWT 零使用（§9.3-9），`SecurityConfig` 仅保留 `cors_origins`。

`config/base.yaml`：删除 `security:` 下的 `jwt_algorithm: HS256`、`jwt_expire_minutes: 1440` 两行。

- [ ] **Step 2: 门禁确认无引用**

Run: `Grep "scrcpy_path\|jwt_secret\|jwt_algorithm\|jwt_expire" backend config tests` → 仅 settings.py 注释中允许零命中（应为 0 结果）
Run: 全量 `PYTHONPATH=backend:. python -m pytest tests/ --ignore=tests/e2e -q --cov=app --cov-fail-under=80`、`python -m ruff check backend/app`、`python -m mypy backend/app` → 全绿

- [ ] **Step 3: 写发布文档**

创建 `docs/Windows-exe-发布说明.md`（面向用户，事实全部来自方案 15 实证结论）：

````markdown
# OpenScrcpy Windows 绿色版发布说明

## 系统要求

- Windows 10/11 x64
- Chrome 浏览器（最新两个稳定版）：视频解码依赖 WebCodecs，仅支持 Chrome
- 除 Chrome 外**零安装**：adb、scrcpy-server、Python 运行时全部随包分发

## 安装与启动

1. 解压 `OpenScrcpy-win64-<版本>.zip` 到任意**可写**目录（建议非 OneDrive/网络盘；中文路径未验证）
2. 双击 `OpenScrcpy.exe`，服务启动后自动打开浏览器（默认 http://127.0.0.1:8765）
3. 首次运行会在 exe 同级创建 `data/`（SQLite 库）与 `logs/`（运行日志）

**头部无窗口属正常现象**：本包以 `--noconsole` 模式运行，日志落入
`logs/openscrcpy.log` 而非控制台。启动期（Python 拦截显隐窗口之前）出错时
无任何输出，此时用 [DebugView](https://learn.microsoft.com/sysinternals/downloads/debugview)
（Sysinternals）抓取 bootloader 的调试输出。

## 端口与单实例

- 默认绑定 127.0.0.1:8765（仅本机访问：一方面避免 Windows 防火墙弹窗，
  另一方面非 HTTPS 的局域网 http 不具备安全上下文、WebCodecs 不可用，
  视频会退化为截图模式——局域网使用请自行承担该后果）
- 8765 被占时自 8765 起依次尝试共 5 个端口（8766…最多到 8769），实际端口记录在 `data/port.txt`
- 重复双击不会起第二实例：自动打开浏览器指向已运行实例

## adb 端口冲突（5037）

本包自带 adb 并默认使用标准 adb server 端口 5037。若本机已有 Android
Studio 等其他 adb 环境且其 server 版本与本包不一致，adb 客户端会**杀掉旧
server** 重新拉起（adb 官方行为）。隔离方案：为绿色版单独设置环境变量后启动

```powershell
$env:ANDROID_ADB_SERVER_PORT = 5039
.\OpenScrcpy.exe
```

## 数据位置与升级

- 用户数据（`data/`、`config/`、`logs/`）位于 exe 同级；exe 同级不可写
  （如装在 Program Files）时回落 `%LOCALAPPDATA%\OpenScrcpy`
- 配置文件改成 exe 同级的 `config/base.yaml` 后 2 秒内热重载生效（无需重启；端口、数据路径等仅启动时读取的配置除外）
- **升级**：解压新版并覆盖 `OpenScrcpy.exe` 与 `_internal/` 时，**保留**
  `data/` 与 `config/`（用户数据永不写入 `_internal`，覆盖即升级、数据不丢）

## 杀软误报

PyInstaller 产物可能被 Defender/360/火绒等启发式误报。本包已关闭 UPX
（UPX 压缩是常见误报特征）。若触发误报，添加信任或改用压缩包内解压
运行；代码签名不在当前范围内（§8.6-5 未实测）。
````

- [ ] **Step 4: 提交**

```bash
git add config/settings.py config/base.yaml docs/Windows-exe-发布说明.md
git commit -m "docs: 死配置清理 + Windows 绿色版发布说明（方案 15 任务 5）

删除零引用的 scrcpy_path 与 JWT 三字段（§9.3-9）；新增发布文档：
端口单实例/adb 5037 隔离/数据与升级保留/杀软误报/DebugView 排错。

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

### Task 6: 干净环境全链路验证（含真机）+ 方案回写

**Files:**
- Create: `tests/manual/verify_packaged_exe.py`（半自动探测脚本，真机人工步骤的自动化前置）
- Modify: `方案/15-Windows绿色exe打包方案.md`（§8 状态、§8.6 实测清单落结、§9.5 追加正式版验证小节）

**Interfaces:**
- Consumes: 任务 4 的 `scripts/build_exe.sh` 产物；任务 3 的 launcher（port.txt/logs 位置）；任务 5 发布文档（验证清单对照）。真机设备按现网可用清单：`.18 / .33 / .25`（`.22` 已不可用）。
- Produces: `tests/manual/verify_packaged_exe.py <base_url>` 打印 PASS/FAIL 报告；方案 15 文档回写。

- [ ] **Step 1: 写半自动探测脚本**

创建 `tests/manual/verify_packaged_exe.py`（不入 CI，人工跑；httpx 由 dev extras 提供）：

```python
"""
打包产物半自动验证脚本（方案 15 任务 6，本地人工运行，不入 CI）
================================================================

用法（先启动打包产物 OpenScrcpy.exe，或先运行本脚本拉起）：
    PYTHONPATH=backend:. python tests/manual/verify_packaged_exe.py [--base http://127.0.0.1:8765]

探测项（自动）：/health、/api/devices、SPA 页面、根路径；
报告项（打印复查指引，需人工确认）：data/port.txt/logs 落点、
adb server 随包路径（ANDROID_ADB_SERVER_PORT 隔离法，见方案 §9.5）。
"""

from __future__ import annotations

import argparse
import sys

import httpx

CHECKS: list[tuple[str, str]] = [
    ("GET /health", "/health"),
    ("GET /api/devices", "/api/devices"),
    ("SPA 首页", "/"),
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="OpenScrcpy 打包产物验证")
    parser.add_argument("--base", default="http://127.0.0.1:8765")
    args = parser.parse_args(argv)

    failed = 0
    with httpx.Client(base_url=args.base, timeout=5.0) as client:
        for label, path in CHECKS:
            try:
                r = client.get(path)
                ok = r.status_code == 200
            except httpx.HTTPError as e:
                ok = False
                r = e
            print(f"{'PASS' if ok else 'FAIL'}  {label}: {r}")
            failed += 0 if ok else 1

    print()
    print("人工复查项（自动探测无法覆盖）：")
    print("  1. 数据落点：exe 同级 data/debug.sqlite 存在（从其他目录启动亦同）")
    print("  2. 单实例：双击第二次 exe 应只开浏览器、不起新进程")
    print("  3. 端口回退：占用 8765 后再启动，port.txt 应为 8766 且浏览器指向新端口")
    print("  4. 日志文件：logs/openscrcpy.log 持续增长且无堆栈异常")
    print("  5. adb 随包路径：ANDROID_ADB_SERVER_PORT=5039 启动后用")
    print("     Get-CimInstance Win32_Process -Filter \"name='adb.exe'\" | Select ExecutablePath")
    print("     新 server 应为 <产物>\\_internal\\tools\\adb.exe")
    print("  6. 真机 .18/.33/.25：视频帧出流、鼠标控制、截图模式回退、debug 会话")
    return failed


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: 干净环境全链路构建 + 自动探测**

Run（每步独立可失败，失败即按 CLAUDE.md 约束 7 停改调研）:
1. `bash scripts/build_exe.sh` → 构建成功，记录 onedir 体积与 zip 体积（对齐 spike 42MiB/18.9MiB 数量级；zip 内不得有 `data/`：`python -m zipfile -l dist/OpenScrcpy-win64-0.1.0.zip | grep data/` 应无结果）
2. 启动 `dist/OpenScrcpy/OpenScrcpy.exe`（后台）→ `PYTHONPATH=backend:. python tests/manual/verify_packaged_exe.py` → 自动项全 PASS
3. 浏览器人工确认：首页加载、深链刷新不 404（SPA fallback 真实验证）、连接 .18 设备后视频出流
4. 单实例 + 端口回退：先 `python -c "import socket,time; s=socket.socket(); s.bind(('127.0.0.1',8765)); s.listen(1); time.sleep(120)"` 占 8765，再启动 exe → 浏览器应指向 8766，`data/port.txt` 内容 8766；期间二次双击仅开浏览器
5. 日志：`cat dist/OpenScrcpy/logs/openscrcpy.log` 无异常堆栈
6. adb 随包实证（§9.5 验证技巧）：`ANDROID_ADB_SERVER_PORT=5039` 环境启动 + `Get-CimInstance` 断言 `_internal\tools\adb.exe`

- [ ] **Step 3: 真机全链路（需用户在场配合设备）**

设备清单按记忆 project_verification_devices：`.18` / `.33` / `.25`。逐台验证：
1. `adb connect <ip>:5555`（随包 adb）→ 设备出现在 /api/devices
2. 视频流：H.264 帧出流（纬证 WebCodecs 于 127.0.0.1 下可用）
3. 控制链路：点击/滑动/文本输入生效
4. 截图模式回退（swdecode 或协议失败场景）与 debug 会话（PTY 运行期交互——§8.6-1 遗留实测项）
5. 断开重连一次

- [ ] **Step 4: 回写方案 15**

修改 `方案/15-Windows绿色exe打包方案.md`：
1. 头部状态行与 §8.5 任务表：任务 0.5~5 标"已完成（提交哈希）"，任务 6 标"验证通过（日期）"；
2. §8.6 待实测清单：1（PTY 运行期）/3（Nuitka 不排）/6（浏览器时序）逐项落结；4/5（OneDrive/杀软）标"发布后观察项"；
3. §9.5 追加"正式版全链路验证（2026-09-24）"小节：构建脚本输出、实测体积、全部验证项结论（含真机）。
4. 更新记忆 `C:\Users\apfn\.claude\projects\D--tangzk-py-scrcpy-web\memory\project_exe_packaging.md`：状态改为"正式实施完成"、补构建产物实测体积与验证日期、fetch_tools 锁定版本（r36.0.0/v4.1 + SHA256 已锁定）。

- [ ] **Step 5: 提交**

```bash
git add tests/manual/verify_packaged_exe.py 方案/15-Windows绿色exe打包方案.md
git commit -m "docs: 方案 15 正式实施落结——干净环境全链路验证通过（任务 6）

构建脚本输出 zip 体积实测 XXMiB；自动探测全 PASS；单实例/端口回退/
随包 adb 路径实证；真机 .18/.33/.25 视频与控制链路通过（详情见 §9.5）。

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Self-Review 记录

**1. Spec 覆盖**：§8.5 七个任务全部有对应 Task（0.5→Task 0.5；1→Task 1+3+4 拆分已裁决；2→Task 2；3→Task 3；4→Task 4；5→Task 5；6→Task 6）；§8.4 九项经验逐条落位（1 任务 4 spec / 2-3-6 任务 3 / 4 任务 4 上游 hook / 5 任务 1-2-4 路径 / 7 任务 1 已部分 51534c2 / 8 任务 4 excludes / 9 任务 6 验证）；§9.3 十五项走查全部有处置落点。
**2. 占位符扫描**：无 TBD/TODO；SHA256 锁定值与任务 6 实测体积为"运行产出值"，对应步骤给出了确切产生命令。
**3. 类型一致性**：`config_base_dir`/`FRONTEND_DIST`/`launcher.*` 在任务间签名一致；`find_available_port` 的 SO_EXCLUSIVEADDRUSE 语义与任务 3 测试的"占位探测"自洽。
**4. 冲突裁决**：已在"任务重映射裁决"表列全（winpty 移任务 4、死代码移任务 3、port.txt 取代写回配置、platform-tools 锁 r36.0.0）。