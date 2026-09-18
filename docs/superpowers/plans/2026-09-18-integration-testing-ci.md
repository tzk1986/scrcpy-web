# 集成测试与部署（地基版）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 scrcpy-web 具备可发布的质量地基——既有测试转绿、静态检查清零、CI 绿门禁、部署文档更新（不含 exe 打包，Docker 降级为开发可选）。

**Architecture:** 在 GitHub 仓库上引入 GitHub Actions CI（ubuntu runner），以「后端 pytest + ruff + mypy(strict)、前端 build + vitest + eslint」为阻塞门禁；为此先修复 14 个漂移测试（针对旧架构的 encoder 测试 + server_manager 验证测试），再系统性补齐 mypy strict 类型注解并完成 ESLint v9 flat-config 迁移。E2E(playwright) 需真机 adb，保持本地运行、不进 CI。

**Tech Stack:** Python 3.11 / FastAPI / pytest / pytest-asyncio / ruff / mypy / Vue 3 + Vite / vue-tsc / vitest / ESLint 9 / Playwright / GitHub Actions。

**Spec:** `方案/10-集成测试与部署.md`（本计划据其现实改写后的目标版）。注意原方案是早期设想（端口 8000、`backend/tests`、`main` 分支、Docker-Linux 部署），已按下列决策修正：交付目标=Windows 绿色 exe 为主、Docker 降为开发可选、CI 触发分支=master、端口默认 8765（见 #7）。

## Global Constraints

- **仅支持 Chrome**（最新两个稳定版）；前端特性可用 WebCodecs/WebTransport。
- **四层架构**：domain / application / infrastructure / interfaces，禁止跨层调用，依赖倒置用 `Protocol`。
- **测试文件只放 `tests/` 下**，按 `tests/{unit,application,infrastructure,scrcpy,integration,e2e}/` 归类；禁止在仓库根或 `backend/` 下建测试。
- **运行测试命令**：`PYTHONPATH=backend python -m pytest tests/ -v`（从仓库根执行）。
- **Windows 兼容**：`run_server.py` 用 `ProactorEventLoop`；Git Bash 执行 adb 设 `MSYS_NO_PATHCONV=1`。
- **默认后端端口 8765**，可被 `BACKEND_PORT`/`BACKEND_HOST` 覆盖（见 #7）。
- **CI 触发**：`push` / `pull_request` 到 **master**（本仓库默认分支是 master，非 main）。
- **CI 运行器**：`ubuntu-latest`（重写的测试须为纯 mock、跨平台，不依赖真实 adb/子进程/ConPTY）。
- **不进 CI**：playwright E2E（需真机）。本地跑：`E2E_DEVICE=192.168.8.22:5555 APP_ENV=e2e npx playwright test --config=tests/e2e/playwright.config.ts`（基线：12 passed + 2 已知 .22 ROM 差异）。
- **每任务一次提交**；提交信息用中文，结尾 `Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>`；**完成后不自动 push**，向用户询问。
- **问题先调研再改**（CLAUDE.md 约束 7）：静态检查/测试失败先定位根因，禁止盲改。
- **禁止新增运行时第三方依赖**（eslint/mypy 修复只用现有 devDependencies：eslint@9、@vue/eslint-config-typescript@13、eslint-plugin-vue@9、@eslint/js、prettier@3、ruff、mypy）。

---

## 文件结构（本计划涉及）

**创建**
- `.github/workflows/ci.yml` — CI 流水线（Task 10）
- `frontend/eslint.config.js` — ESLint 9 flat config（Task 9）
- `docs/部署指南.md` — Windows 本地运行 + 端口配置 + E2E 本地说明 + exe 后续（Task 11）

**修改**
- `tests/scrcpy/test_server_manager.py` — 删 1 个陈旧用例（Task 1）
- `tests/scrcpy/test_encoder.py` — 整体重写为 5 个针对现架构的缝测试（Task 2）
- `backend/app/**`（多文件） — ruff 清理 + mypy strict 补注解（Task 3–8）
- `pyproject.toml` — mypy overrides（ignore_missing_imports / 排除 examples）（Task 4）
- `frontend/package.json` — `lint`/`format` 脚本改 flat 调用（Task 9）
- `docker/Dockerfile.backend`、`docker/Dockerfile.frontend`、`docker-compose.yml` — 顶部加「开发参考·未针对本项目验证」caveat（Task 11）
- `README.md`、`Makefile`、`方案/10-集成测试与部署.md`、`方案/进度追踪.md` — 文档回写（Task 11/12）

---

## Phase A — 测试治理（转绿前置）

### Task 1: 删除 server_manager 陈旧验证测试

**Files:**
- Modify: `tests/scrcpy/test_server_manager.py:79-88`

**Interfaces:**
- Produces: `tests/scrcpy/test_server_manager.py` 全套通过（供 Task 10 CI pytest 门禁依赖）

`push_server` 已不再调用 `_server_exists` 做二次验证（见 `server_manager.py:143-145` 注释：Windows 路径转义会误报），`adb push` 返回码 0 即判定成功。故 `test_push_server_verification_failed`（mock `_server_exists=False` 期望 `push_server` 返回 False）编码的是**已删除的行为**，必然失败，应移除。

- [ ] **Step 1: 跑测试确认失败点**

Run: `PYTHONPATH=backend python -m pytest tests/scrcpy/test_server_manager.py::TestPushServer::test_push_server_verification_failed -v`
Expected: FAIL — `assert success is False` → 实际 `True is False`

- [ ] **Step 2: 删除该用例**

删除 `tests/scrcpy/test_server_manager.py` 第 79–88 行整个 `test_push_server_verification_failed` 方法。保留 `test_push_server_success`（仅 mock `_server_exists` 无害，push 不调它仍返回 True）与 `test_push_server_failure`、`test_push_server_jar_not_found`。

- [ ] **Step 3: 跑该文件确认全绿**

Run: `PYTHONPATH=backend python -m pytest tests/scrcpy/test_server_manager.py -v`
Expected: 全 PASS（其余用例不受影响）

- [ ] **Step 4: 提交**

```bash
git add tests/scrcpy/test_server_manager.py
git commit -m "test: 删除 server_manager 陈旧验证测试（push_server 已不做 _server_exists 二次验证）"
```

---

### Task 2: 重写 encoder 测试为针对现架构的缝测试

**Files:**
- Rewrite: `tests/scrcpy/test_encoder.py`（整文件替换）

**Interfaces:**
- Consumes: `ScrcpyEncoder`（`app.infrastructure.stream.scrcpy`）、`ServerManager.push_server`、`domain.ports.EncoderOpts`
- Produces: `tests/scrcpy/test_encoder.py` 全套通过，且**不依赖 `asyncio.create_subprocess_exec`/`open_connection` 的真实行为**（CI ubuntu 可跑）

现有 13 例针对**已废弃的子进程 stdout 读帧架构**（`ensure_server`/`is_running`/`get_pid`/`stdout.read`），当前 `start()` 已改为「TCP socket + 后台 `read_socket` 入队 + 从队列 yield」，帧解析移到 WS 层 `H264Parser`。整体重写为 5 个测**可稳定触及的缝**：push 失败抛错、无 control_sender 时输入回退 adb、短距 swipe 仅 down/up、stop 释放资源。

- [ ] **Step 1: 用下述内容整体替换 `tests/scrcpy/test_encoder.py`**

```python
"""
视频编码器测试（针对现架构：TCP socket + 队列 + 控制发送器）
================================================================

只测可稳定触达的缝，不启动真实 adb 子进程/socket：
    - push_server 失败 → start 抛 RuntimeError
    - 无 control_sender 时 send_input 回退 adb shell input
    - 短距离 swipe 仅发 down+up
    - stop 释放 process/socket
帧读取/NALU 解析属 WS 层 H264Parser 职责，另有覆盖，此处不再测。
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.domain.ports import EncoderOpts
from app.infrastructure.stream.scrcpy import ScrcpyEncoder


@pytest.fixture
def encoder():
    return ScrcpyEncoder()


@pytest.mark.asyncio
async def test_start_raises_when_push_server_fails(encoder, mock_device_id):
    """server 推送失败时 start 立即抛 RuntimeError。"""
    encoder._server_manager.push_server = AsyncMock(return_value=False)
    opts = EncoderOpts(max_size=720, bit_rate="2M", codec="h264", fps=30)

    agen = encoder.start(mock_device_id, opts)
    with pytest.raises(RuntimeError, match="Failed to push scrcpy-server.jar"):
        await agen.__anext__()


@pytest.mark.asyncio
async def test_send_input_falls_back_to_adb_without_control_sender(encoder):
    """无 control_sender/分辨率时 touch 回退到 adb shell input tap。"""
    encoder._device_id = "emulator-5554"
    encoder._control_sender = None
    encoder._resolution = (0, 0)

    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(b"", b""))
    proc.returncode = 0
    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)) as exec_mock:
        await encoder.send_input({"action": "touch", "x": 100, "y": 200})

    args = [str(a) for a in exec_mock.call_args[0]]
    assert "input" in args and "tap" in args and "100" in args and "200" in args


@pytest.mark.asyncio
async def test_send_input_falls_back_on_unknown_action(encoder):
    """未知 action 也走 adb 回退分支（内部仅记 warning，不抛错）。"""
    encoder._device_id = "emulator-5554"
    encoder._control_sender = None
    encoder._resolution = (0, 0)

    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(b"", b""))
    proc.returncode = 0
    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
        await encoder.send_input({"action": "bogus"})  # 不抛异常


@pytest.mark.asyncio
async def test_send_swipe_short_distance_only_down_and_up(encoder):
    """距离<10 的 swipe 只发 ACTION_DOWN + ACTION_UP，无中间 MOVE。"""
    from app.scrcpy.control_sender import ACTION_DOWN, ACTION_UP

    sender = MagicMock()
    sender.touch = AsyncMock()
    encoder._control_sender = sender
    encoder._resolution = (1080, 1920)

    await encoder._send_swipe({"x1": 10, "y1": 10, "x2": 12, "y2": 10, "duration": 50})

    assert sender.touch.await_count == 2
    acts = [c.args[2] for c in sender.touch.await_args_list]
    assert acts == [ACTION_DOWN, ACTION_UP]


@pytest.mark.asyncio
async def test_stop_terminates_process_and_closes_sockets(encoder):
    """stop 终止进程、关闭视频/控制 socket，并复位状态。"""
    proc = MagicMock()
    proc.returncode = None
    proc.terminate = MagicMock()
    proc.wait = AsyncMock()
    proc.kill = MagicMock()
    encoder.process = proc

    writer = MagicMock()
    writer.close = MagicMock()
    writer.wait_closed = AsyncMock()
    encoder._writer = writer

    ctrl = MagicMock()
    ctrl.close = MagicMock()
    ctrl.wait_closed = AsyncMock()
    encoder._control_writer = ctrl
    encoder._device_id = "emulator-5554"

    # 端口清理的 adb 子进程 mock 掉
    cleanup = MagicMock()
    cleanup.communicate = AsyncMock(return_value=(b"", b""))
    cleanup.returncode = 0
    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=cleanup)):
        await encoder.stop()

    proc.terminate.assert_called()
    writer.close.assert_called()
    ctrl.close.assert_called()
    assert encoder.process is None
    assert encoder._device_id == ""
```

- [ ] **Step 2: 跑新测试确认通过**

Run: `PYTHONPATH=backend python -m pytest tests/scrcpy/test_encoder.py -v`
Expected: 5 passed

- [ ] **Step 3: 跑 scrcpy 目录整体确认无残留红**

Run: `PYTHONPATH=backend python -m pytest tests/scrcpy -v`
Expected: 全 PASS

- [ ] **Step 4: 提交**

```bash
git add tests/scrcpy/test_encoder.py
git commit -m "test: 重写 encoder 测试为现架构缝测试（socket+队列）,替换废弃的 stdout 读帧用例"
```

---

## Phase B — 后端静态检查清零

### Task 3: ruff 清零（后端）

**Files:**
- Modify: `backend/app/**`（ruff 报告处，约 21 项，其中 15 可自动修）
- 典型：`backend/app/scrcpy/h264_parser.py:47`（F401 未用导入 `NALU_TYPE_SLICE`）、`backend/app/scrcpy/examples.py:98`（F841 未用局部变量 `stream`）

**Interfaces:**
- Produces: `python -m ruff check backend/app` 退出 0（Task 10 CI 门禁）

- [ ] **Step 1: 自动修复**

Run: `python -m ruff check --fix backend/app`
预期修掉 F401/未用导入等 ~15 项。

- [ ] **Step 2: 手工处理剩余项**

Run: `python -m ruff check backend/app` 看剩余。对 `examples.py:98` F841：`stream = encoder.start(...)` 改为直接迭代 `async for chunk in encoder.start(device_id, opts):`（示例脚本里本就要消费），或加 `_ = stream` 语义不合适 → 选前者的真实改写：删除赋值，保留注释掉的用法示例结构。其它按 `ruff` 提示逐条修，禁止 `# noqa` 掩盖（除非该行确属有意且加行内说明）。

- [ ] **Step 3: 确认清零**

Run: `python -m ruff check backend/app`
Expected: `All checks passed!`

- [ ] **Step 4: 回归测试未破**

Run: `PYTHONPATH=backend python -m pytest tests/ --ignore=tests/integration --ignore=tests/e2e -q`
Expected: 全 PASS（ruff --fix 不应改变行为；如 F841 改动涉及逻辑需复核）

- [ ] **Step 5: 提交**

```bash
git add backend/app
git commit -m "style: ruff check 清零（未用导入/局部变量等）"
```

---

### Task 4: mypy 配置打底（overrides + 排除 examples）

**Files:**
- Modify: `pyproject.toml` `[tool.mypy]` 段

**Interfaces:**
- Produces: `python -m mypy backend/app` 能解析 `config`/第三方无桩库而不报 import 错，且 `app.scrcpy.examples` 不参与 strict。

mypy 当前报 `config/settings.py import-untyped yaml`、`winpty import-untyped`，且 `examples.py` 是演示脚本非发行模块，strict 处理它无收益。

- [ ] **Step 1: 编辑 `pyproject.toml`**

在 `[tool.mypy]` 下补 `mypy_path` 与忽略项，并新增 overrides 段：

```toml
[tool.mypy]
python_version = "3.11"
strict = true
mypy_path = "backend"
explicit_package_bases = true

[[tool.mypy.overrides]]
module = ["yaml", "winpty", "structlog.*", "aiosqlite", "aiofiles"]
ignore_missing_imports = true

[[tool.mypy.overrides]]
module = ["app.scrcpy.examples", "app.scrcpy.constants", "app.scrcpy.h264_parser"]
ignore_errors = true
```

> 说明：`constants.py`/`h264_parser.py` 若后续 strict 已很干净可移除其 ignore；先仅排除确属演示/低风险者，保持最小豁免。executor 在 Task 8 复核能否收窄。

- [ ] **Step 2: 确认 import 错误消除、得到纯类型错误基线**

Run: `python -m mypy backend/app 2>&1 | tail -3`
Expected: 不再有 `import-untyped`/`yaml`/`winpty`；给出剩余（纯注解）错误数（<329）。

- [ ] **Step 3: 提交**

```bash
git add pyproject.toml
git commit -m "chore: mypy 配置打底（mypy_path/ignore_missing_imports/排除 examples 与演示模块）"
```

---

### Task 5: mypy strict 清零 — domain + application 层

**Files:**
- Modify: `backend/app/domain/ports.py`（~19）、`backend/app/application/debug_service.py`（~37）、`backend/app/application/device_service.py`（~13）、`backend/app/application/stream_service.py`、`backend/app/application/bitrate_advisor.py`

**Interfaces:**
- Produces: 上述文件 `mypy` 无 error（Task 8 汇总）

主要错误码为 `no-untyped-def`（补参数/返回注解）、`no-untyped-call`（因被调函数无注解，补全即消）、`type-arg`（`dict`→`dict[str, Any]`、`list`→`list[bytes]` 等补泛型参数）。

- [ ] **Step 1: 看本层错误清单**

Run: `python -m mypy backend/app/domain backend/app/application 2>&1 | tail -40`

- [ ] **Step 2: 逐函数补注解**

模式（按码）：
- `no-untyped-def`：给 `def f(a, b):` 补全 `def f(a: str, b: int) -> None:`；async 生成器写 `-> AsyncIterator[bytes]`（与 `VideoEncoder` Protocol 对齐）。
- `type-arg`：`settings: dict = {}` → `dict[str, object]`/合适值类型；`list`→`list[str]`。
- 第三方返回值无类型时，用显式 `cast` 或就地标注，勿滥用 `Any`。

示例（`domain/ports.py` Protocol 方法签名保持与实现一致，勿改运行时行为）：

```python
async def start(self, device_id: str, opts: EncoderOpts) -> AsyncIterator[bytes]: ...
async def stop(self) -> None: ...
```

- [ ] **Step 3: 确认本层清零**

Run: `python -m mypy backend/app/domain backend/app/application`
Expected: `Success: no issues found`

- [ ] **Step 4: 回归**

Run: `PYTHONPATH=backend python -m pytest tests/application tests/domain -q 2>&1 | tail -5`（无该目录则跑 `tests/ -q --ignore=tests/integration --ignore=tests/e2e`）
Expected: 全 PASS

- [ ] **Step 5: 提交**

```bash
git add backend/app/domain backend/app/application
git commit -m "chore(types): mypy strict 清零 domain+application 层"
```

---

### Task 6: mypy strict 清零 — infrastructure/adb + stream + scrcpy

**Files:**
- Modify: `backend/app/infrastructure/adb/shell.py`（~28）、`cli.py`（~14）、`winpty.py`（~12）、`backend/app/infrastructure/stream/scrcpy.py`（~22）、`backend/app/scrcpy/control_sender.py`（~11）、`server_manager.py`

**Interfaces:**
- Produces: 上述文件 mypy 无 error

`winpty.py` 用 `pywinpty`（已 ignore_missing_imports），其回调/句柄类型补 `Any` 局部别名或精确类型。`scrcpy.py` 的 `read_socket`/`read_server_logs` 内嵌协程补 `-> None`。`send_input(self, data: dict)` → `dict[str, object]` 并在取值处 `cast`/断言窄化（strict 下 `data["x"]` 为 `object`，传给需要 int 的 `touch()` 前需 `int(...)` 或 `cast`）。

- [ ] **Step 1: 看清单**

Run: `python -m mypy backend/app/infrastructure backend/app/scrcpy 2>&1 | tail -40`

- [ ] **Step 2: 补注解**

`send_input` 签名改：`async def send_input(self, data: dict[str, Any]) -> None:`（`Any` 于此处合理：输入 payload 异构；从 `typing import Any` 导入）。内嵌 async 函数与 `-> None`。

- [ ] **Step 3: 确认清零**

Run: `python -m mypy backend/app/infrastructure backend/app/scrcpy`
Expected: `Success`

- [ ] **Step 4: 回归**

Run: `PYTHONPATH=backend python -m pytest tests/infrastructure tests/scrcpy -q 2>&1 | tail -5`
Expected: 全 PASS

- [ ] **Step 5: 提交**

```bash
git add backend/app/infrastructure backend/app/scrcpy
git commit -m "chore(types): mypy strict 清零 infrastructure(adb/stream)+scrcpy"
```

---

### Task 7: mypy strict 清零 — persistence + interfaces 层

**Files:**
- Modify: `backend/app/infrastructure/persistence/sqlite.py`（~27）、`batch_writer.py`（~11）、`backend/app/interfaces/http/devices.py`（~13）、`debug.py`（~9）、`apps.py`（~9）、`backend/app/interfaces/ws/video.py`（~8）、`backend/app/main.py`

**Interfaces:**
- Produces: 上述文件 mypy 无 error；全 `backend/app` strict 通过

- [ ] **Step 1: 看清单**

Run: `python -m mypy backend/app/infrastructure/persistence backend/app/interfaces backend/app/main.py 2>&1 | tail -40`

- [ ] **Step 2: 补注解**：FastAPI 路由函数补参数/返回类型（返回 `dict[str, Any]` 或具体模型）；aiosqlite 行类型用 `Any`/封装；`main.py:113 no-untyped-def` 补 `-> None` 或 `AsyncIterator` 装饰器签名。

- [ ] **Step 3: 全后端清零**

Run: `python -m mypy backend/app`
Expected: `Success: no issues found in <N> source files`

- [ ] **Step 4: 回归全量**

Run: `PYTHONPATH=backend python -m pytest tests/ --ignore=tests/integration --ignore=tests/e2e -q 2>&1 | tail -5`
Expected: 全 PASS

- [ ] **Step 5: 收窄豁免复核**

若 Task 4 里被 `ignore_errors` 的 `constants.py`/`h264_parser.py` 已可清，则从 overrides 移除并重跑 `mypy backend/app` 保持 Success。

- [ ] **Step 6: 提交**

```bash
git add backend/app pyproject.toml
git commit -m "chore(types): mypy strict 清零 persistence+interfaces+main,全后端通过"
```

---

## Phase C — 前端静态检查

### Task 8: ESLint v9 flat-config 迁移并入门禁

**Files:**
- Create: `frontend/eslint.config.js`
- Modify: `frontend/package.json`（`lint`/`format` scripts）
- Modify: `frontend/**`（消除报错，优先 `--fix`）

**Interfaces:**
- Produces: `cd frontend && npm run lint` 退出 0（Task 10 CI 门禁）

现状：装了 `eslint@9` 但**无任何 config**，且 `package.json` 的 `lint` 仍用 v8 的 `eslint . --ext ...`（v9 报迁移错误）。迁到 flat config，只用已安装依赖（不新增）。

- [ ] **Step 1: 新建 `frontend/eslint.config.js`**

```javascript
import js from '@eslint/js'
import pluginVue from 'eslint-plugin-vue'
import { defineConfigWithVueTs, vueTsConfigs } from '@vue/eslint-config-typescript'

export default defineConfigWithVueTs(
  {
    name: 'app/ignores',
    ignores: ['dist/**', 'node_modules/**', '*.d.ts', '.test-output/**', 'coverage/**'],
  },
  {
    name: 'app/setup',
    languageOptions: {
      // 浏览器全局（无 globals 依赖：手动声明常用项）
      globals: {
        window: 'readonly',
        document: 'readonly',
        navigator: 'readonly',
        console: 'readonly',
        setTimeout: 'readonly',
        clearTimeout: 'readonly',
        setInterval: 'readonly',
        clearInterval: 'readonly',
        requestAnimationFrame: 'readonly',
        cancelAnimationFrame: 'readonly',
        URL: 'readonly',
        fetch: 'readonly',
        Blob: 'readonly',
        File: 'readonly',
        FileReader: 'readonly',
        FormData: 'readonly',
        WebSocket: 'readonly',
        Image: 'readonly',
        HTMLElement: 'readonly',
        HTMLCanvasElement: 'readonly',
        EventSource: 'readonly',
        VideoDecoder: 'readonly',
        EncodedVideoChunk: 'readonly',
        __dirname: 'readonly',
        process: 'readonly',
      },
    },
  },
  js.configs.recommended,
  pluginVue.configs['flat/essential'],
  vueTsConfigs.recommended,
  {
    name: 'app/rules',
    rules: {
      // 项目未跑 prettier 强制，关掉与格式冲突/风格类规则，避免大规模无意义改动
      'vue/multi-word-component-names': 'off',
      '@typescript-eslint/no-explicit-any': 'off',
      '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_', varsIgnorePattern: '^_' }],
      'no-undef': 'off',            // 由 TS 负责未定义检查（flat/TS 场景常见做法）
      '@typescript-eslint/no-empty-function': 'off',
    },
  },
)
```

- [ ] **Step 2: 改 `frontend/package.json` scripts**

```json
"lint": "eslint .",
"lint:fix": "eslint . --fix",
```
（移除旧 `--ext` 用法；`format` 保留 prettier。）

- [ ] **Step 3: 自动修 + 看剩余**

Run: `cd frontend && npx eslint . --fix`
然后：`npx eslint . 2>&1 | tail -30`

- [ ] **Step 4: 手工清零剩余**

按剩余报错逐条改（多为真实未用变量/未处理 Promise）。确属有意用行内 `// eslint-disable-next-line <规则>` 并注明原因；不得全局关规则。

- [ ] **Step 5: 确认门禁通过且构建未破**

Run: `cd frontend && npm run lint && npm run build`
Expected: eslint 退出 0；build（vue-tsc + vite）成功

- [ ] **Step 6: vitest 回归**

Run: `cd frontend && npx vitest run`
Expected: 19 passed

- [ ] **Step 7: 提交**

```bash
git add frontend/eslint.config.js frontend/package.json frontend/src frontend/*
git commit -m "chore(lint): ESLint 9 flat-config 迁移并清零,lint 纳入构建流程"
```

---

## Phase D — CI 流水线

### Task 9: 新增 GitHub Actions CI（绿门禁）

**Files:**
- Create: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: Task 1–8 的全部「本地退出 0」命令
- Produces: push/PR 到 master 触发；后端(pytest/ruff/mypy)+前端(build/vitest/eslint) 全绿才通过

- [ ] **Step 1: 写 `.github/workflows/ci.yml`**

```yaml
name: CI

on:
  push:
    branches: [master]
  pull_request:
    branches: [master]

jobs:
  backend:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
          cache: pip
      - name: Install
        run: pip install -e ".[dev]"
      - name: Ruff
        run: python -m ruff check backend/app
      - name: Mypy (strict)
        run: python -m mypy backend/app
      - name: Pytest
        run: PYTHONPATH=backend:. python -m pytest tests/ --ignore=tests/integration --ignore=tests/e2e -q

  frontend:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: frontend
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: '20'
          cache: npm
          cache-dependency-path: frontend/package-lock.json
      - name: Install
        run: npm ci
      - name: Lint
        run: npm run lint
      - name: Test
        run: npx vitest run
      - name: Build
        run: npm run build
```

> E2E 不入 CI（需真机）；注释在文件顶部说明：「Playwright E2E 需物理 Android 设备，仅本地运行，见 docs/部署指南.md」。

- [ ] **Step 2: 本地等价预检（尽量模拟 CI）**

Run: `python -m ruff check backend/app && python -m mypy backend/app && PYTHONPATH=backend:. python -m pytest tests/ --ignore=tests/integration --ignore=tests/e2e -q`
Expected: 三步退出 0

- [ ] **Step 3: 提交**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: 新增 GitHub Actions 门禁(ruff+mypy+pytest / eslint+vitest+build)"
```

> 推送后由用户决定；推 GitHub 后核对 Actions 首次运行为绿（本地已等价验证）。

---

## Phase E — Docker 降级与文档

### Task 10: Docker 降级为开发可选 + 部署文档

**Files:**
- Modify: `docker/Dockerfile.backend`、`docker/Dockerfile.frontend`、`docker-compose.yml`（加 caveat 头注释）
- Modify: `Makefile`（`docker-up`/`docker-down` 标注为可选；新增 `ci` 本地门禁目标）
- Create: `docs/部署指南.md`
- Modify: `README.md`（系统要求/端口/运行方式与现状对齐）

**Interfaces:**
- Produces: README/部署指南描述真实可发布路径（Windows 本地/未来 exe），Docker 明确为未验证的开发参考

- [ ] **Step 1: Docker 文件顶部加 caveat**

在三个 Docker 文件首行注释加：
```
# ⚠️ 开发参考：本项目依赖 Windows 专属组件(ConPTY/pywinpty、scrcpy.exe、ProactorEventLoop)，
# 此 Docker 配置未在 Linux 容器实测，仅作服务器化方向的骨架。当前受支持运行方式为见 docs/部署指南.md。
```

- [ ] **Step 2: 写 `docs/部署指南.md`**，内容覆盖：
  - 开发运行：`make dev` 或后端 `python run_server.py`（默认 8765）+ 前端 `cd frontend && npm run dev`（8080，代理 /api、/ws）
  - 端口配置：`BACKEND_PORT`/`BACKEND_HOST` 环境变量与 `.env`（接 #7）
  - 本地跑 E2E：`E2E_DEVICE=<ip:port> npx playwright test --config=tests/e2e/playwright.config.ts`（自动以 `APP_ENV=e2e` 关自适应；仅 Chrome；需真机）
  - 静态检查/CI：`make lint`（ruff+mypy+eslint）与 CI 说明
  - 已知限制：E2E 不进 CI；mypy strict 覆盖范围；examples 排除
  - 后续：Windows 绿色 exe 打包（指向 `方案/15`）

- [ ] **Step 3: 更新 README 快速开始与系统要求**

修正端口（8765/8080，非 8000/5173）、依赖（Python3.11+/Node20+/ADB/Chrome）、运行命令与 `make` 目标；把「Docker 部署」降级为附注并链到部署指南。

- [ ] **Step 4: Makefile 加本地 CI 门禁目标**

```make
ci:
	python -m ruff check backend/app
	python -m mypy backend/app
	PYTHONPATH=backend:. python -m pytest tests/ --ignore=tests/integration --ignore=tests/e2e -q
	cd frontend && npm run lint && npx vitest run && npm run build
```
并把 `test:` 目标里错误的 `pytest backend/tests` 修为 `PYTHONPATH=backend:. python -m pytest tests/`。

- [ ] **Step 5: 提交**

```bash
git add docker/ Makefile docs/部署指南.md README.md
git commit -m "docs: Docker 降级为开发可选 + 部署指南/README 与现状对齐(make ci 目标)"
```

---

### Task 11: 进度与方案文档回写

**Files:**
- Modify: `方案/进度追踪.md`
- Modify: `方案/10-集成测试与部署.md`

- [ ] **Step 1: 进度追踪 #8 标记完成**

将当前阶段优先级清单第 8 项改为 ✅，附：测试治理(15→绿)/mypy strict 清零/ruff 清零/ESLint9/CI(GitHub Actions)/Docker 降级/部署文档；并**新增第 9 项「Windows 绿色 exe 打包」到待办**（指向 方案/15，标注为下一个独立任务）。记录既有 14 scrcpy 失败已修复的说明（从 ⚠️ 观察转为 ✅ 已清）。

- [ ] **Step 2: 改写 方案/10-集成测试与部署.md 状态与设想**

把「状态」各项更新（E2E→✅ 既有；Docker→降级为开发可选；CI/CD→✅ 本计划；文档→✅）；在文首加「现实校正」小节：交付目标=Windows exe 为主、端口 8765、master 分支、Docker-Linux 未验证、E2E 本地、mypy strict 已清零。保留历史 Docker 片段但标注未采用。

- [ ] **Step 3: 提交**

```bash
git add 方案/进度追踪.md 方案/10-集成测试与部署.md
git commit -m "docs: 回写 #8 完成状态,新增 exe 打包为下一独立任务,校正 方案/10"
```

---

## 验收标准（整体）

- `python -m ruff check backend/app` 退出 0
- `python -m mypy backend/app` → `Success: no issues found`
- `PYTHONPATH=backend:. python -m pytest tests/ --ignore=tests/integration --ignore=tests/e2e -q` 全绿（scrcpy 14 失败清零）
- `cd frontend && npm run lint` 退出 0 且 `npm run build`、`npx vitest run`(19) 通过
- `.github/workflows/ci.yml` 推 master 后 Actions 绿（本地 `make ci` 等价预检通过）
- README/部署指南/进度追踪/方案/10 与现实一致；Docker 明确标注未验证
- E2E 基线不变（本地，`.22`：12 passed + 2 已知 ROM 差异，自适应零触发）
