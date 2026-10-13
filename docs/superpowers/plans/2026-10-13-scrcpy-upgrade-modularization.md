# scrcpy 升级模块化（方案 44）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 全仓 scrcpy-server 版本单源化 + 升级校验自动化 + `.bak` 误恢复加固，并以 v5.0.1 完成一次真实升级试跑（真机出流 + 控制注入）验证升级通道。

**Architecture:** 新建零依赖纯常量文件 `backend/app/scrcpy/scrcpy_version.py` 作为唯一版本分发点（脚本以 `runpy.run_path` 读取，绕开 app 包导入链——实测 `import app.scrcpy.constants` 会拉起 `app.core.config → config.settings`，独立脚本环境不可用）；`constants.py` 重导出保持全仓既有 import 不变；`fetch_tools.py` 的 JAR_URL 由模板推导；`server_manager._bak_restore` 判据由「仅大小」升级为「大小（ls -l 列解析）+ sha256（设备端 sha256sum）」双判据，sha256sum 不可用时降级大小判据。

**Tech Stack:** Python 3.10 / runpy / hashlib / re（全标准库，零新依赖）/ pytest / adb r36.0.0

**Spec:** `方案/44-scrcpy升级模块化方案.md`

## Global Constraints

- **版本唯一分发点**：`backend/app/scrcpy/scrcpy_version.py`。本计划完成后，升级 scrcpy 版本 = 改该文件 1 处 + 回填 `fetch_tools.py` 的 jar 哈希 + 按 SOP 核对文档引用（不再散点四处改）。
- **目标升级版本**：`5.0.1`（Task 5 执行；Task 1–4 期间版本保持 `4.1`）。
- **master 直做**；**禁 push**（push 需用户逐次显式授权）。
- **TDD**：每个任务先写失败测试（RED）→ 最小实现（GREEN）→ 全量门禁。
- **零新依赖**：仅用 `runpy` / `hashlib` / `re` / `argparse`（标准库）。
- **中文提交信息**；不添加 Co-Authored-By 尾注。
- **门禁（每任务收尾必跑，CI 同款）**：
  ```bash
  python -m ruff check backend/app
  python -m mypy backend/app
  PYTHONPATH=backend:. python -m pytest tests/ --ignore=tests/e2e -q --cov=app --cov-fail-under=80
  ```
  （改动涉及 `scripts/` 或 `tests/` 时附加 `python -m ruff check scripts tests`，若报历史存量问题则只核对本次新增行。）
- **测试文件一律放 `tests/`**。
- **真机验证设备**：`192.168.8.25:5555`（备用 `.18`/`.33`）；本机 adb 用 `tools/adb.exe`（与运行时一致）。**不通用户 8765 端口**（用户实例在跑）。
- **Windows/Git Bash 注意**：adb shell 命令带路径参数时设 `MSYS_NO_PATHCONV=1`；curl 落盘用相对路径（msys `/tmp` 不识别）。
- **jar 二进制被 gitignore**：不提交 `scrcpy-server.jar` 本体，只提交版本常量与哈希清单。

## Review Focus

以下是 spec 隐含、需要测试或显式验证步骤兜住的输入类/失效模式（逐条在对应任务有落点）：

1. **fetch_tools 在裸环境独立运行**（无 `PYTHONPATH`、任意 cwd）——版本读取必须零依赖可达（Task 1 Step 6 验证步骤：从 `$HOME` 跑）。
2. **设备端 sha256sum 缺失**（老 toybox / 裁剪 ROM）——`.bak` 恢复降级大小判据、不阻断恢复路径（Task 4 `test_bak_hash_unavailable_falls_back_to_size`）。
3. **`.bak` 与本地 jar 同尺寸不同内容**（升级场景核心：设备端残留旧版 bak）——hash 判据必须拦截误恢复（Task 4 `test_bak_same_size_different_hash_pushes`）。
4. **启动命令版本参数漂移**（`scrcpy.py:218` 被改成字面量）——单测锁定 + 突变验证（Task 3 `test_server_command_uses_version_constant` + 临时改字面量验证变红）。
5. **设备端 shell 脚本无 python**（`test_scrcpy_minimal.sh` 在设备 `/system/bin/sh` 运行）——版本必须由宿主注入且未设置时显式失败（Task 2 该文件改为 `"$SCRCPY_VERSION"` 必填变量语义）。

---

### Task 1: 版本单源化核心（scrcpy_version.py + constants + fetch_tools + 测试断言）

**Files:**
- Create: `backend/app/scrcpy/scrcpy_version.py`
- Modify: `backend/app/scrcpy/constants.py`（顶部 import 区 + `:127`）
- Modify: `scripts/fetch_tools.py`（`:26-41` 区）
- Modify: `tests/unit/test_fetch_tools.py`（`:44-47`）
- Modify: `tests/scrcpy/test_constants.py`（`:181-183`）

**Interfaces:**
- Produces: `scrcpy_version.py` 模块常量 `SCRCPY_SERVER_VERSION: str`（零 import）；`fetch_tools.SCRCPY_SERVER_VERSION: str`、`fetch_tools.JAR_URL: str`（模块级名字保持，值改为推导）。
- Consumes: 无（本任务是最底层）。

- [ ] **Step 1: RED — 改两处测试断言为「引用分发点」**

`tests/unit/test_fetch_tools.py`，将现状 `:44-47` 的硬编码断言整体替换为：

```python
def _single_source_version() -> str:
    """版本唯一分发点读取（runpy 同机制，方案 44 T1）。"""
    import runpy

    path = Path(__file__).resolve().parents[2] / "backend" / "app" / "scrcpy" / "scrcpy_version.py"
    return runpy.run_path(str(path))["SCRCPY_SERVER_VERSION"]


def test_jar_url_aligns_server_version():
    # URL 由 scrcpy_version.py 推导——升级只改一处分发点（方案 44 T1）
    version = _single_source_version()
    jar = next(e for e in fetch_tools.TOOLS if e.name == "scrcpy-server.jar")
    assert jar.url.endswith(f"/v{version}/scrcpy-server-v{version}")
```

`tests/scrcpy/test_constants.py`，将 `:181-183` 的 `test_server_version` 替换为两个测试（文件顶部 import 区补 `import re`）：

```python
    def test_server_version(self):
        """版本号格式合法（升级不改本测试，方案 44 T1）。"""
        assert re.fullmatch(r"\d+\.\d+(\.\d+)?", SCRCPY_SERVER_VERSION)

    def test_server_version_single_source(self):
        """constants 的版本与唯一分发点一致（方案 44 T1）。"""
        import runpy
        from pathlib import Path

        path = (
            Path(__file__).resolve().parents[2]
            / "backend" / "app" / "scrcpy" / "scrcpy_version.py"
        )
        assert runpy.run_path(str(path))["SCRCPY_SERVER_VERSION"] == SCRCPY_SERVER_VERSION
```

- [ ] **Step 2: 验证 RED**

Run: `PYTHONPATH=backend:. python -m pytest tests/unit/test_fetch_tools.py::test_jar_url_aligns_server_version tests/scrcpy/test_constants.py::TestScrcpyServerConfig -q`
Expected: FAIL — 两条均因 `FileNotFoundError`（`scrcpy_version.py` 尚不存在）；`test_server_version` 格式断言本身应 PASS。

- [ ] **Step 3: 创建分发点文件**

`backend/app/scrcpy/scrcpy_version.py`（全新，内容如下，一字不多）：

```python
"""scrcpy-server 版本唯一分发点（方案 44 §1）。

只允许本文件声明版本号；其余全仓位置（constants.py、scripts/fetch_tools.py、
tests/manual 脚本等）一律从此读取/推导。升级 scrcpy 时只改这里 + 重新
下载回填 jar 哈希（见 docs/经验记录.md E028 升级 SOP）。

本文件必须保持零 import、零依赖：scripts/ 与 tests/manual/ 以
runpy.run_path 直接执行本文件读取常量，绕开 app 包导入链
（app/scrcpy/__init__.py 会拉起 app.core.config → config.settings，
独立脚本环境不可用）。
"""

SCRCPY_SERVER_VERSION = "4.1"
```

- [ ] **Step 4: constants.py 改重导出**

顶部 import 区（现有 `from app.domain.ports import EncoderOpts as EncoderOpts` 之后）加：

```python
from .scrcpy_version import SCRCPY_SERVER_VERSION as SCRCPY_SERVER_VERSION
```

`:127` 行原 `SCRCPY_SERVER_VERSION = "4.1"  # scrcpy-server 版本（使用 v4.1 协议）` 替换为一行注释：

```python
# SCRCPY_SERVER_VERSION 见本文件顶部重导出（唯一分发点：scrcpy_version.py，方案 44 §1）
```

- [ ] **Step 5: fetch_tools.py 改模板推导**

import 区加 `import runpy`（保持字母序：hashlib 与 sys 之间）；`REPO_ROOT` 定义之后加：

```python
VERSION_FILE = REPO_ROOT / "backend" / "app" / "scrcpy" / "scrcpy_version.py"


def _server_version() -> str:
    """从版本唯一分发点读取（runpy：绕开 app 包导入链，见 scrcpy_version.py 注释）。"""
    return str(runpy.run_path(str(VERSION_FILE))["SCRCPY_SERVER_VERSION"])


SCRCPY_SERVER_VERSION = _server_version()
```

`JAR_URL` 行（现 `:41`）替换为：

```python
JAR_URL = (
    f"https://github.com/Genymobile/scrcpy/releases/download/"
    f"v{SCRCPY_SERVER_VERSION}/scrcpy-server-v{SCRCPY_SERVER_VERSION}"
)
```

顶部 docstring 中「来源：Genymobile/scrcpy GitHub Release v4.1（对齐 app/scrcpy/constants.py 的 SCRCPY_SERVER_VERSION = "4.1"）」改为「来源：Genymobile/scrcpy GitHub Release（版本对齐 backend/app/scrcpy/scrcpy_version.py 唯一分发点）」。

- [ ] **Step 6: 验证 GREEN + 裸环境独立运行**

Run:
```bash
PYTHONPATH=backend:. python -m pytest tests/unit/test_fetch_tools.py tests/scrcpy/test_constants.py -q
cd ~ && python -c "import sys; sys.path.insert(0, 'D:/tangzk/py/scrcpy-web/scripts'); import fetch_tools; print(fetch_tools.JAR_URL)"
```
Expected: 测试全 PASS；第二命令从 `$HOME` 打印 `https://github.com/Genymobile/scrcpy/releases/download/v4.1/scrcpy-server-v4.1`（验证 Review Focus #1：无 PYTHONPATH、任意 cwd 可用）。

- [ ] **Step 7: 全量门禁 + 提交**

```bash
python -m ruff check backend/app scripts tests
python -m mypy backend/app
PYTHONPATH=backend:. python -m pytest tests/ --ignore=tests/e2e -q --cov=app --cov-fail-under=80
git add backend/app/scrcpy/scrcpy_version.py backend/app/scrcpy/constants.py scripts/fetch_tools.py tests/unit/test_fetch_tools.py tests/scrcpy/test_constants.py
git commit -m "refactor: scrcpy 版本单源化核心——scrcpy_version.py 唯一分发点 + fetch_tools URL 模板推导（方案 44 T1）"
```

---

### Task 2: 散点清理（8a/8b 全清 + README）

**Files:**
- Modify: `tests/scrcpy/debug_scrcpy_server.py`（`:67`）
- Modify: `tests/scrcpy/debug_scrcpy_minimal.py`（`:72`）
- Modify: `tests/scrcpy/debug_scrcpy_direct.py`（`:51`）
- Modify: `tests/manual/probe_encoder_stability.py`（`:5-6`、`:122`）
- Modify: `tests/manual/spike_stream_reuse.py`（`:37`）
- Modify: `tests/scrcpy/test_scrcpy_manual.sh`（`:18`）
- Modify: `tests/scrcpy/test_scrcpy_minimal.sh`（`:4`）
- Modify: `backend/app/scrcpy/README.md`（`:213`、`:264`、`:337`）

**Interfaces:**
- Consumes: Task 1 的 `backend/app/scrcpy/scrcpy_version.py`。
- Produces: 无（叶子改动）。

**模式说明**：三类脚本三种读法——
- **无 path 补齐的独立 Python 脚本**（debug ×3、spike）：`runpy.run_path` 直读分发点（不引入 app/config 依赖）；
- **已挂 app 导入链的脚本**（probe）：已有 `_ROOT` path 补齐且已 `import app.scrcpy.control_sender`，直接 `from app.scrcpy.constants import SCRCPY_SERVER_VERSION`；
- **shell / 设备端脚本**：宿主注入。

- [ ] **Step 1: debug_scrcpy_server.py**

import 区（`import asyncio` / `import secrets` 之后）加：

```python
import runpy
from pathlib import Path

SCRCPY_VERSION = runpy.run_path(
    str(Path(__file__).resolve().parents[2] / "backend" / "app" / "scrcpy" / "scrcpy_version.py")
)["SCRCPY_SERVER_VERSION"]
```

`:67` 行 `"4.1",` → `SCRCPY_VERSION,`

- [ ] **Step 2: debug_scrcpy_minimal.py**

同 Step 1 模式（两台脚本头结构一致）：顶部加同样的 `runpy` 读取块；`:72` 行 `"4.1",` → `SCRCPY_VERSION,`

- [ ] **Step 3: debug_scrcpy_direct.py（2.4 残留）**

同 Step 1 模式（该脚本头为 `import asyncio` / `import struct`）：顶部加同样的 `runpy` 读取块；`:51` 行 `"2.4",` → `SCRCPY_VERSION,`

- [ ] **Step 4: probe_encoder_stability.py**

`:122` 所在 f-string 内 `com.genymobile.scrcpy.Server 4.1 ` → `com.genymobile.scrcpy.Server {SCRCPY_SERVER_VERSION} `；import 区（现有 `from app.scrcpy.control_sender import ...` 旁）加：

```python
from app.scrcpy.constants import SCRCPY_SERVER_VERSION
```

顶部 docstring `:5-6`「按 v4.1 协议读流」改为「按 12B 包头协议读流」（版本无关表述）。

- [ ] **Step 5: spike_stream_reuse.py**

顶部 import 区加 `import runpy`；`:37` 行 `SCRCPY_VERSION = "4.1"` 替换为（复用同文件 `:33` 已定义的 `ROOT`）：

```python
SCRCPY_VERSION = runpy.run_path(
    str(ROOT / "backend" / "app" / "scrcpy" / "scrcpy_version.py")
)["SCRCPY_SERVER_VERSION"]
```

- [ ] **Step 6: test_scrcpy_manual.sh（2.4 残留）**

`ADB=...` 行之后加：

```sh
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SCRCPY_VERSION=$(python -c "import runpy,sys; print(runpy.run_path(sys.argv[1])['SCRCPY_SERVER_VERSION'])" \
  "$REPO_ROOT/backend/app/scrcpy/scrcpy_version.py")
```

`:18` 行 `com.genymobile.scrcpy.Server 2.4` → `com.genymobile.scrcpy.Server $SCRCPY_VERSION`

- [ ] **Step 7: test_scrcpy_minimal.sh（设备端脚本，无 python）**

整个文件改为（Review Focus #5：版本必填，未注入即失败而非静默旧版）：

```sh
#!/system/bin/sh
# 设备端最小启动脚本：版本号由宿主注入（唯一分发点 backend/app/scrcpy/scrcpy_version.py）
# 用法：adb shell "SCRCPY_VERSION=<版本> sh /data/local/tmp/test_scrcpy_minimal.sh"
export CLASSPATH=/data/local/tmp/scrcpy-server.jar
exec app_process / com.genymobile.scrcpy.Server "$SCRCPY_VERSION"
```

- [ ] **Step 8: backend/app/scrcpy/README.md 三处 2.4 → 4.1**

- `:213` 示例命令行 `com.genymobile.scrcpy.Server 2.4 \` → `com.genymobile.scrcpy.Server 4.1 \`
- `:264` `- 当前版本：v2.4` → `- 当前版本：v4.1（唯一分发点 backend/app/scrcpy/scrcpy_version.py）`
- `:337` `- [ ] 下载并集成 scrcpy-server.jar v2.4` → `- [ ] 下载并集成 scrcpy-server.jar v4.1`（保留原件勾选状态）

- [ ] **Step 9: 验证（无 CI 覆盖的手动脚本用「编译 + 语法 + 残留扫描」）**

Run:
```bash
python -m py_compile tests/scrcpy/debug_scrcpy_server.py tests/scrcpy/debug_scrcpy_minimal.py tests/scrcpy/debug_scrcpy_direct.py tests/manual/probe_encoder_stability.py tests/manual/spike_stream_reuse.py
bash -n tests/scrcpy/test_scrcpy_manual.sh
grep -rn 'Server 2\.4\|Server 4\.1\|"2\.4"\|"4\.1"' tests/ backend/app/scrcpy/ scripts/
```
Expected: 编译与语法检查零输出；grep 仅剩 `backend/app/scrcpy/scrcpy_version.py` 的 `"4.1"` 一条（其余命中均为注释/历史文档字样，人工核对）。

- [ ] **Step 10: 全量门禁 + 提交**

```bash
python -m ruff check backend/app scripts tests
python -m mypy backend/app
PYTHONPATH=backend:. python -m pytest tests/ --ignore=tests/e2e -q --cov=app --cov-fail-under=80
git add tests/scrcpy/debug_scrcpy_server.py tests/scrcpy/debug_scrcpy_minimal.py tests/scrcpy/debug_scrcpy_direct.py tests/manual/probe_encoder_stability.py tests/manual/spike_stream_reuse.py tests/scrcpy/test_scrcpy_manual.sh tests/scrcpy/test_scrcpy_minimal.sh backend/app/scrcpy/README.md
git commit -m "refactor: scrcpy 版本散点全清——debug/manual 脚本改读唯一分发点，2.4 残留归零（方案 44 T1）"
```

---

### Task 3: 校验自动化（启动参数一致性单测 + fetch_tools --verify-local）

**Files:**
- Modify: `tests/scrcpy/test_scrcpy_encoder.py`（`test_server_command_normalizes_opts` 之后加测试）
- Modify: `scripts/fetch_tools.py`（新增 `verify_local()` + `--verify-local`）
- Modify: `tests/unit/test_fetch_tools.py`（新增 3 测试）

**Interfaces:**
- Consumes: Task 1 的 `fetch_tools.TOOLS` / `sha256_of`。
- Produces: `fetch_tools.verify_local() -> None`（失败 `SystemExit`）；CLI `python scripts/fetch_tools.py --verify-local`。

- [ ] **Step 1: 启动参数一致性单测（锁定既有正确行为）**

`tests/scrcpy/test_scrcpy_encoder.py` 在 `test_server_command_normalizes_opts` 之后加：

```python
async def test_server_command_uses_version_constant():
    """启动命令的版本参数取自 SCRCPY_SERVER_VERSION（防消费点漂移，方案 44 T2）。"""
    encoder = ScrcpyEncoder()
    harness = SubprocessHarness(server_stderr=FakeStderr([b"Device: test-device\n"], block=True))
    await drive_start(encoder, harness=harness)

    cmd = harness.server_cmd()
    assert cmd[cmd.index(SCRCPY_SERVER_CLASS) + 1] == SCRCPY_SERVER_VERSION
```

（该文件顶部已 import `SCRCPY_SERVER_VERSION` 与 `SCRCPY_SERVER_CLASS`；`drive_start` / `SubprocessHarness` / `FakeStderr` 均为同文件既有设施。）

- [ ] **Step 2: RED 验证（突变法——锁定测试的「看着它失败」等效）**

Run: `PYTHONPATH=backend:. python -m pytest tests/scrcpy/test_scrcpy_encoder.py::test_server_command_uses_version_constant -q`
Expected: PASS（锁定现状）。

再临时把 `backend/app/infrastructure/stream/scrcpy.py:218` 的 `SCRCPY_SERVER_VERSION,` 改为 `"0.0",` 重跑 →
Expected: **FAIL**（证明测试能抓住漂移）。改回 `SCRCPY_SERVER_VERSION,` 重跑 → PASS。还原后 `git diff` 应无该文件改动。

- [ ] **Step 3: fetch_tools verify_local 的 RED（测试先行）**

`tests/unit/test_fetch_tools.py` 末尾加：

```python
PAYLOADS = {
    "adb.exe": b"adb-binary",
    "AdbWinApi.dll": b"api-dll",
    "AdbWinUsbApi.dll": b"usb-dll",
    "scrcpy-server.jar": b"jar-binary",
}


def _locked_local_tools(tmp_path):
    """四条目指向 tmp 下真实文件、哈希按实际内容锁定。"""
    locked = []
    for e in fetch_tools.TOOLS:
        d = tmp_path / e.name
        d.mkdir(exist_ok=True)
        (d / e.name).write_bytes(PAYLOADS[e.name])
        locked.append(
            _replace_entry(e, sha256=hashlib.sha256(PAYLOADS[e.name]).hexdigest(), target_dir=d)
        )
    return locked


def test_verify_local_ok(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch_tools, "TOOLS", _locked_local_tools(tmp_path))
    fetch_tools.verify_local()  # 不抛即通过


def test_verify_local_missing_file(tmp_path, monkeypatch):
    locked = [_replace_entry(e, target_dir=tmp_path / "empty") for e in fetch_tools.TOOLS]
    monkeypatch.setattr(fetch_tools, "TOOLS", locked)
    with pytest.raises(SystemExit):
        fetch_tools.verify_local()


def test_verify_local_hash_mismatch(tmp_path, monkeypatch):
    locked = [
        _replace_entry(e, sha256="0" * 64, target_dir=d)
        for e, d in [(e, tmp_path / e.name) for e in fetch_tools.TOOLS]
    ]
    monkeypatch.setattr(fetch_tools, "TOOLS", _locked_local_tools(tmp_path)[:0] + locked)
    with pytest.raises(SystemExit):
        fetch_tools.verify_local()
```

（`test_verify_local_hash_mismatch` 里 `_locked_local_tools(tmp_path)[:0]` 仅为保持 tmp 文件与 locked 目录一致的最简写法——实施时可简化为：先调 `_locked_local_tools(tmp_path)` 造文件，再用 `"0"*64` 覆盖锁定哈希。）

Run: `PYTHONPATH=backend:. python -m pytest tests/unit/test_fetch_tools.py -q`
Expected: 3 新测试 FAIL（`AttributeError: module 'fetch_tools' has no attribute 'verify_local'`）。

- [ ] **Step 4: 实现 verify_local + CLI**

`scripts/fetch_tools.py` 在 `ensure_all` 之后加：

```python
def verify_local() -> None:
    """离线校验：本地已存在文件与清单 SHA256 一致（不下载，方案 44 T2）。"""
    for entry in TOOLS:
        target = entry.target_dir / entry.name
        if not target.exists():
            raise SystemExit(f"missing file: {target}")
        actual = sha256_of(target)
        if actual != entry.sha256:
            raise SystemExit(f"SHA256 mismatch for {target}: expected {entry.sha256}, got {actual}")
        print(f"{entry.name}: OK ({actual})")
    print("OK: local tools verified")
```

`main()` 改为：

```python
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="下载并校验外置工具（方案 15 任务 0.5）")
    parser.add_argument(
        "--print-hash",
        action="store_true",
        help="下载后打印实际 SHA256 而不校验（首次锁定/升级版本用，回填进 TOOLS 清单）",
    )
    parser.add_argument(
        "--verify-local",
        action="store_true",
        help="不下载：校验本地已存在文件与清单 SHA256 一致（方案 44 T2）",
    )
    args = parser.parse_args(argv)
    if args.verify_local:
        verify_local()
        return 0
    ensure_all(print_hash=args.print_hash)
    return 0
```

- [ ] **Step 5: 验证 GREEN**

Run: `PYTHONPATH=backend:. python -m pytest tests/unit/test_fetch_tools.py tests/scrcpy/test_scrcpy_encoder.py -q`
Expected: 全 PASS。

- [ ] **Step 6: 全量门禁 + 提交**

```bash
python -m ruff check backend/app scripts tests
python -m mypy backend/app
PYTHONPATH=backend:. python -m pytest tests/ --ignore=tests/e2e -q --cov=app --cov-fail-under=80
git add tests/scrcpy/test_scrcpy_encoder.py scripts/fetch_tools.py tests/unit/test_fetch_tools.py
git commit -m "feat: 升级校验自动化——启动命令版本参数单测锁定 + fetch_tools --verify-local 离线哈希校验（方案 44 T2）"
```

---

### Task 4: `.bak` 恢复加固（大小 + sha256 双判据）

**Files:**
- Modify: `backend/app/scrcpy/server_manager.py`（import 区、`_bak_restore`、`push_server`）
- Modify: `tests/scrcpy/test_server_manager.py`（`_ls_bak_ok` + 新增 2 测试）

**Interfaces:**
- Consumes: 无（模块内部私有方法签名变化，调用方仅 `push_server`）。
- Produces: `ServerManager._bak_restore(device_id, expected_size, expected_sha256) -> bool`（私有，测试同步改造）。

- [ ] **Step 1: RED — 改造测试夹具 + 新增两个测试**

`tests/scrcpy/test_server_manager.py`：
1. 顶部加 `import hashlib`
2. `TestPushServerBakRestore._ls_bak_ok` 改为（`b"dummy"` 即 `manager_with_jar` 夹具的 jar 内容，其 sha256 与恢复判据天然一致）：

```python
    @staticmethod
    def _ls_bak_ok(size: int, sha256: str | None = None) -> MagicMock:
        digest = sha256 or hashlib.sha256(b"dummy").hexdigest()
        out = (
            f"-rw-r--r-- 1 shell shell {size} 2026-09-30 12:00 "
            f"/data/local/tmp/scrcpy-server.jar.bak\n"
            f"{digest}  /data/local/tmp/scrcpy-server.jar.bak\n"
        ).encode()
        return TestPushServerBakRestore._proc(0, out=out)
```

3. 在 `test_bak_size_mismatch_pushes_and_refreshes` 之后新增：

```python
    @pytest.mark.asyncio
    async def test_bak_same_size_different_hash_pushes(
        self, mock_device_id, manager_with_jar
    ):
        """同尺寸不同内容 → hash 判据拦截，不误恢复（方案 44 T3，升级关键场景）。"""
        seq = [self._ls_bak_ok(5, sha256="0" * 64), self._proc(0), self._proc(0)]
        with patch('asyncio.create_subprocess_exec', side_effect=seq) as exec_mock:
            success = await manager_with_jar.push_server(mock_device_id)

        assert success is True
        assert exec_mock.call_count == 3
        argv = self._argv_list(exec_mock)
        assert any("push" in a for a in argv)
        assert "cp" in argv[2]

    @pytest.mark.asyncio
    async def test_bak_hash_unavailable_falls_back_to_size(
        self, mock_device_id, manager_with_jar
    ):
        """设备无 sha256sum（输出无 64-hex）→ 降级大小判据，仍走恢复（老设备兼容）。"""
        out = (
            b"-rw-r--r-- 1 shell shell 5 2026-09-30 12:00 "
            b"/data/local/tmp/scrcpy-server.jar.bak\n"
        )
        seq = [self._proc(0, out=out), self._proc(0)]
        with patch('asyncio.create_subprocess_exec', side_effect=seq) as exec_mock:
            success = await manager_with_jar.push_server(mock_device_id)

        assert success is True
        assert exec_mock.call_count == 2
        argv = self._argv_list(exec_mock)
        assert not any("push" in a for a in argv)
        assert "cp" in argv[1]
```

Run: `PYTHONPATH=backend:. python -m pytest tests/scrcpy/test_server_manager.py::TestPushServerBakRestore -q`
Expected: `test_bak_same_size_different_hash_pushes` FAIL（现实现仅大小判据 → 误走恢复路径，断言 `call_count == 3` 与实际 2 不符）；`test_bak_hash_unavailable_falls_back_to_size` PASS（现实现本就大小判据）；其余现有测试 PASS。

- [ ] **Step 2: 实现双判据**

`backend/app/scrcpy/server_manager.py`：
1. import 区加 `import hashlib`、`import re`
2. `_bak_restore` 整体替换为：

```python
    async def _bak_restore(
        self, device_id: str, expected_size: int | None, expected_sha256: str | None
    ) -> bool:
        """
        尝试从 .bak 恢复主 JAR（D1；方案 44 T3 加固为大小 + sha256 双判据）。

        一次 shell 调用（ls -l && sha256sum）取大小与哈希：
        - 大小按 ls -l 第 5 列精确解析（同尺寸不同内容不再误恢复）；
        - 设备无 sha256sum（老 toybox/裁剪 ROM）时降级为仅大小判据；
        - expected 任一为 None（本地 jar 缺失）时不恢复。

        任何失败或校验不符均返回 False，由调用方回退完整 push（D3）。
        cp 而非 mv：恢复后的主 JAR 会再次被退出中的 server 自删（E002），
        bak 必须常驻供下次恢复。

        参数：
            device_id: 设备的 ADB 序列号。
            expected_size: 本地 JAR 字节数；None 时直接不恢复。
            expected_sha256: 本地 JAR 的 SHA256 十六进制；None 时直接不恢复。

        返回：
            True 表示已从 bak 恢复，False 表示需要 push。
        """
        if expected_size is None or expected_sha256 is None:
            return False
        try:
            process = await asyncio.create_subprocess_exec(
                settings().adb.path, "-s", device_id, "shell",
                f"ls -l {SCRCPY_SERVER_BAK_PATH} && sha256sum {SCRCPY_SERVER_BAK_PATH}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=CREATE_NO_WINDOW,
            )

            stdout, _ = await asyncio.wait_for(
                process.communicate(),
                timeout=ADB_TIMEOUT_SECONDS
            )

            text = stdout.decode(errors="replace")
            ls_match = re.search(r"^\S+\s+\d+\s+\S+\s+\S+\s+(\d+)\s", text, re.M)
            if ls_match is None or int(ls_match.group(1)) != expected_size:
                return False

            sha_match = re.search(r"\b([0-9a-f]{64})\b", text)
            if sha_match is None:
                # 老设备无 sha256sum：降级仅大小判据（保恢复路径不中断）
                logger.debug("bak_hash_unavailable", device=device_id)
            elif sha_match.group(1) != expected_sha256:
                logger.info("bak_hash_mismatch", device=device_id)
                return False

            process = await asyncio.create_subprocess_exec(
                settings().adb.path, "-s", device_id, "shell",
                "cp", SCRCPY_SERVER_BAK_PATH, SCRCPY_SERVER_REMOTE_PATH,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=CREATE_NO_WINDOW,
            )

            _, _ = await asyncio.wait_for(
                process.communicate(),
                timeout=ADB_TIMEOUT_SECONDS
            )

            logger.info("scrcpy_server_restored_from_bak", device=device_id)
            return process.returncode == 0

        except Exception as e:
            logger.debug("bak_restore_error", device=device_id, error=str(e))
            return False
```

3. `push_server` 中 `expected_size` 计算块替换为：

```python
        try:
            expected_size = self._jar_path.stat().st_size
            expected_sha256 = hashlib.sha256(self._jar_path.read_bytes()).hexdigest()
        except OSError:
            expected_size = None
            expected_sha256 = None
        if await self._bak_restore(device_id, expected_size, expected_sha256):
            return True
```

**注意**：组合命令的返回码不再作为恢复前置（`sha256sum` 不可用时组合返回码非 0，但大小行仍有效）——判定完全由输出解析驱动。

- [ ] **Step 3: 验证 GREEN**

Run: `PYTHONPATH=backend:. python -m pytest tests/scrcpy/test_server_manager.py -q`
Expected: 全 PASS（含既有 4 个 bak 测试——`_ls_bak_ok` 默认 digest 与 `b"dummy"` 一致故恢复路径不变；`test_bak_size_mismatch` 走 999≠5 → push）。

- [ ] **Step 4: 全量门禁 + 提交**

```bash
python -m ruff check backend/app scripts tests
python -m mypy backend/app
PYTHONPATH=backend:. python -m pytest tests/ --ignore=tests/e2e -q --cov=app --cov-fail-under=80
git add backend/app/scrcpy/server_manager.py tests/scrcpy/test_server_manager.py
git commit -m "fix: .bak 恢复加固——大小列解析 + sha256 双判据，拒同尺寸不同内容误恢复（方案 44 T3）"
```

---

### Task 5: v5.0.1 真实升级试跑（验证升级通道）

**Files:**
- Modify: `backend/app/scrcpy/scrcpy_version.py`（`"4.1"` → `"5.0.1"`）
- Modify: `scripts/fetch_tools.py`（TOOLS 清单 jar 的 sha256 回填）
- Modify: `backend/app/scrcpy/README.md`（版本引用 4.1 → 5.0.1）

**Interfaces:**
- Consumes: Task 1–4 的全部设施（分发点、verify_local、加固后的 push_server）。
- Produces: 升级后的真实 jar（不入 git）+ 通道验证结论（回写 Task 6 文档）。

- [ ] **Step 1: 改分发点版本**

`backend/app/scrcpy/scrcpy_version.py`：`SCRCPY_SERVER_VERSION = "4.1"` → `SCRCPY_SERVER_VERSION = "5.0.1"`

- [ ] **Step 2: 下载新 jar 并取哈希**

Run: `cd "D:/tangzk/py/scrcpy-web" && python scripts/fetch_tools.py --print-hash`
Expected: 四件打印实际 sha256；adb 三件与清单现值一致（同源未变）；`scrcpy-server.jar` 为新值（记录之）。若 GitHub 不可达：停止并报告（升级不可半途）。

- [ ] **Step 3: 官方 SHA256SUMS.txt 双源核对**

Run（落盘用相对路径，Windows curl 不识别 msys /tmp）：
```bash
cd "D:/tangzk/py/scrcpy-web" && curl -sL -o SHA256SUMS.txt.tmp https://github.com/Genymobile/scrcpy/releases/download/v5.0.1/SHA256SUMS.txt && grep "scrcpy-server-v5.0.1" SHA256SUMS.txt.tmp
```
Expected: 输出行的哈希与 Step 2 的 jar 哈希一致（双源确认）。核对后删除 `SHA256SUMS.txt.tmp`（不提交）。不一致则停止并报告。

- [ ] **Step 4: 回填清单哈希**

`scripts/fetch_tools.py` 的 `TOOLS` 清单中 `FileEntry("scrcpy-server.jar", ...)` 的 sha256 改为 Step 3 双源确认值。

- [ ] **Step 5: 离线校验 + 单测 + 门禁**

Run:
```bash
cd "D:/tangzk/py/scrcpy-web" && python scripts/fetch_tools.py --verify-local
python -m ruff check backend/app scripts tests
python -m mypy backend/app
PYTHONPATH=backend:. python -m pytest tests/ --ignore=tests/e2e -q --cov=app --cov-fail-under=80
```
Expected: 四件 `OK (...)`；门禁全绿（测试不因升级变红——这正是单源化的意义）。

- [ ] **Step 6: 真机 smoke A — 服务组件升级路径（push_server 直调）**

验证「设备端残留 4.1 bak 被双判据拦截 → 推送 5.0.1 成功」（.25 设备当前应持有 4.1 的 jar/bak）：

Run:
```bash
cd "D:/tangzk/py/scrcpy-web" && PYTHONPATH=backend:. python - <<'EOF'
import asyncio
from app.scrcpy.server_manager import ServerManager

async def main():
    ok = await ServerManager().push_server("192.168.8.25:5555")
    print("push_server:", ok)

asyncio.run(main())
EOF
```
Expected: `push_server: True`；观察日志（若开 info）出现 `pushing_scrcpy_server` 而非 `scrcpy_server_restored_from_bak` 且未出现 `bak_hash_mismatch` 误拦（若设备 bak 大小已不同则在大小判据即拦——两者都正确）。若出现 `restored_from_bak` 则**异常**（说明拦判失效）：停止并排查。

- [ ] **Step 7: 真机 smoke B — 出流 + 控制注入（probe 脚本）**

Run（`--reset-at 30` 经 RESET_VIDEO=17 控制消息催帧，覆盖控制链；脚本输出逐秒帧数）：

```bash
cd "D:/tangzk/py/scrcpy-web" && MSYS_NO_PATHCONV=1 python -u tests/manual/probe_encoder_stability.py 192.168.8.25:5555 --seconds 60 --reset-at 30 --tag v501-smoke
```
Expected: 60 秒全程逐秒 `frames=` 非零持续（允许 RK 设备既有波动）；reset 时刻后出现 `key=1`；**无** server 启动失败/`does not match` 字样。若启动即败且日志含 "does not match" → 设备端 jar 未更新（排查 Step 6）或 jar/常量不配对 → 回滚（改回 4.1 + 恢复旧哈希，git 历史留底）。

- [ ] **Step 8: 文档版本引用更新（升级 SOP 步骤 6 首次演练）**

Run: `grep -rn "4\.1" backend/app/scrcpy/README.md README.md docs/用户手册.md | grep -in "scrcpy\|server" | head -20`
逐个核对：属于 scrcpy 版本引用的改 `5.0.1`（含 `backend/app/scrcpy/README.md:213/264`——Task 2 刚改为 4.1 的处）；属协议描述的历史措辞保持或改版本无关表述。

- [ ] **Step 9: 提交**

```bash
git add backend/app/scrcpy/scrcpy_version.py scripts/fetch_tools.py backend/app/scrcpy/README.md
git commit -m "feat: scrcpy-server 升级 v5.0.1 试跑——分发点常量 + jar 哈希双源回填 + 真机出流/控制验证通过（方案 44 T4）"
```
（jar 本体 gitignore 不入提交；`SHA256SUMS.txt.tmp` 已删。）

---

### Task 6: 文档 SOP 与方案回写

**Files:**
- Modify: `docs/经验记录.md`（新增 E028）
- Modify: `方案/44-scrcpy升级模块化方案.md`（状态头 + 实施记录 + 修正 2 处错误）
- Modify: `方案/README.md`（44 行索引状态）
- Modify: `方案/进度追踪.md`（第七十七次更新）

**Interfaces:**
- Consumes: Task 1–5 的提交号与验证结论。
- Produces: 升级 SOP 文档（未来升级照做）。

- [ ] **Step 1: 经验记录新增 E028**

`docs/经验记录.md` 尾部（E027 之后、经验记录规范节之前）插入：

```markdown
## E028 scrcpy 升级 SOP（模块化通道，方案 44）

**日期**：2026-10-13  
**标签**：`scrcpy` `版本升级` `SOP`

### 背景

方案 44 将 scrcpy-server 版本单源化到 `backend/app/scrcpy/scrcpy_version.py`（零依赖，脚本以 runpy 读取）。此后升级照本节 SOP 执行；E015 为 v2.4→v4.1 的一次性升级记录，二者互补。

### SOP 步骤

1. 改 `backend/app/scrcpy/scrcpy_version.py`（唯一分发点，全仓只此一处版本常量）；
2. `python scripts/fetch_tools.py --print-hash` 下载新 jar 并打印实际 SHA256；
3. 对照官方 `SHA256SUMS.txt`（`https://github.com/Genymobile/scrcpy/releases/download/v{X}/SHA256SUMS.txt`）双源核对 jar 哈希，一致后回填 `scripts/fetch_tools.py` 清单；
4. `python scripts/fetch_tools.py --verify-local` 离线复核；
5. 全量门禁（ruff + mypy + pytest，升级不应使任何测试变红——有红即为隐藏散点）；
6. 文档版本引用核对：`grep -rn "4\.1" backend/app/scrcpy/README.md README.md` 等，scrcpy 版本相关处随升级更新；
7. 真机 smoke（.25）：`probe_encoder_stability.py --seconds 60 --reset-at 30`（出流 + RESET_VIDEO 控制）；直调 `ServerManager().push_server()` 验证 `.bak` 双判据拦截旧版本包；stderr 捕获 "does not match" 即判失败；
8. **跨协议代判定**（升级前必做）：GitHub compare API 比对 `server/src/main/java/com/genymobile/scrcpy/Options.java` 与 `ControlMessage.java`——有差异即为协议断层，停止本 SOP，转协议重核立项；
9. 打包：下次 exe 打包自然携带新 jar（fetch_tools 下载 + spec datas 注入），zip 内 jar 哈希与清单一致性核验。

### 经验

- 升级失败回滚 = 改回分发点常量 + 恢复清单哈希（旧 jar 在 git 历史/官方源可取）；
- `.bak` 误恢复是升级首连的隐蔽风险（设备端残留旧版 bak）——方案 44 T3 的 sha256 判据即为拦截此场景；
- 「有测试变红」是散点清查的探针：单源化彻底的仓库，升级只该让 0 条测试失败。

### 参考/关联

- `方案/44-scrcpy升级模块化方案.md`
- E015（v2.4→v4.1 升级记录）、E002（jar 退出自删）
- 上游版本硬校验：jar 内 BuildConfig.VERSION_NAME 与 app_process 首参不符即拒启
```

- [ ] **Step 2: 方案 44 回写与纠错**

`方案/44-scrcpy升级模块化方案.md`：
1. 状态头 blockquote 改为：`> **状态**：已实施（2026-10-13）——T1-T5 全链落地 + v5.0.1 真实升级试跑通过（真机出流/控制/`.bak` 拦截）；实施记录见第七节。`
2. §二表格第 4 行路径纠错：`backend/app/infrastructure/stream/server_manager.py:301` → **`backend/app/scrcpy/server_manager.py:301`**（`get_server_version` 返回常量；`.bak` 恢复判据在同行 `:206`——立项调研时路径误记，实施期发现）
3. §四 T5 行 `（E015 新增）` 提法纠错：E015 已被占用（v4.1 升级记录）→ 改为 `E028 新增`
4. §五 `（推荐选做）` 标注为 `（已实施）`
5. 文末追加「七、实施记录」小节：任务/提交号/验证结论（含 smoke 实测数据行）

- [ ] **Step 3: 方案/README.md 索引行更新**

`:65` 行 44 项改为：`- [44-scrcpy升级模块化方案.md](./44-scrcpy升级模块化方案.md) - 版本单源化 + 升级 SOP（E028）+ .bak 加固 + v5.0.1 升级试跑（已实施并真机验证 2026-10-13）`

- [ ] **Step 4: 进度追踪第七十七次更新**

`方案/进度追踪.md` 追加 `### 2026-10-13（第七十七次更新 - 方案 44 全链实施 + v5.0.1 升级试跑）` 节（区间提交列表、门禁数据、smoke 数据、发现与纠错），并更新文末「最后更新」行。

- [ ] **Step 5: 提交**

```bash
git add docs/经验记录.md 方案/44-scrcpy升级模块化方案.md 方案/README.md 方案/进度追踪.md
git commit -m "docs: 方案 44 收口——E028 升级 SOP + 方案回写（路径/E015 编号纠错）+ 进度追踪第七十七次更新"
```

---

## 收尾说明

- 本计划完成后：`方案 42`（录屏流程优化与磁盘清理）→ `方案 43`（截图标注编辑器二次设计）另行写实施计划执行（用户已定排期顺延）。
- exe 重打包不在本计划范围：0.7.6 打包时自然携带 v5.0.1 jar（届时按打包清单核验 zip 内 jar 哈希）；打包前仍须排查孤儿 adb 锁 / esbuild 锁两坑。
- push 远程：待用户显式授权后执行（本批为 ae9fde6..本计划全部提交）。