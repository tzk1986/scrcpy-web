# 方案 42「录屏流程优化与磁盘清理」实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 消除录屏「白录」数据丢失与 done 态死胡同，自然录满自动收尾拉回，关服/启动清空录像残留，并补齐前端 done 态「重新录制」与文件大小信息行。

**Architecture:** 四层架构不变：cli 侧「终结回调保留 paths 表项 + stop 缺失任务时 `ls -ld` 探测」闭合白录路径；自然结束经依赖倒置通道（`AdbDriver` 端口新增同步注册点）回调进 `RecordingService`，由其守卫后复用 `stop()` 收尾；磁盘清理走 `cleanup_all_recordings()` 双挂钩（lifecycle shutdown + 清理循环首轮即扫）；done 会话文件失效由 GET 端点收敛 404，前端静默回 idle。

**Tech Stack:** Python 3.10 / FastAPI / asyncio / pytest；Vue 3 + vitest + vue-tsc 前端。零新依赖。

**Spec:** `方案/42-录屏流程优化与磁盘清理方案.md`（本计划按 §1-§5 与任务分解 T1-T5 展开；spec 与计划冲突时以 spec 为准并记录 Ruling）

## Global Constraints

- master 直做，不建分支不建 worktree；**禁止 push**（push 需用户逐批显式授权，不顺延）。
- TDD 先红后绿：每段生产代码前必须有失败测试且实际观察其失败；测试全部放 `tests/` 下（镜像分层目录），禁止仓库根/backend 下建测试。
- 后端门禁（每任务收尾必跑）：
  - `python -m ruff check backend/app`（tests/ 有 ~35 条存量告警：只核对新增行不引入新告警）
  - `python -m mypy backend/app`（strict）
  - `PYTHONPATH=backend:. python -m pytest tests/ --ignore=tests/e2e -q --cov=app --cov-fail-under=80`
  - 注：Windows Git Bash 下 `PYTHONPATH=backend:.` 分隔符实际不生效，靠 `tests/conftest.py` sys.path 兜底（E028 实证），写法保持双冒号口径与既有一致。
- 前端门禁（涉及前端任务）：`cd frontend && npx vitest run src/components/stream/RecordingPanel.test.ts`（收尾跑全量 `npx vitest run`）+ `npx vue-tsc -b`。
- 中文提交信息，尾注统一 `（方案 42 TN）`（T 用本计划任务号）；无 Co-Authored-By。
- 零新依赖；严格四层：`cli.py`（infrastructure）不得 import `app.application`——自然结束通知经 `domain/ports.py` 的 `AdbDriver` 协议同步注册点注入。
- 磁盘数据保留策略：录像属暂存——关服清空、强杀残留下次启动即扫为合规行为而非例外（spec §一约束）。
- 真机验证设备：主 .25（192.168.8.25:5555，Android 7.1.2，常态触发 err=-38 降档 256x144）；备 .18/.33。Git Bash 跑 adb 相关命令带 `MSYS_NO_PATHCONV=1`。**不得杀掉用户正在使用的 8765 端口进程**。
- 浏览器仅支持 Chrome（最新两个稳定版）。

## Spec 歧义裁决（Ruling，执行时写入 SDD ledger）

spec §1「自然结束（正常 time-limit 结束；降档重试中的终结不触发）」存在两种读法。本计划采用：**首任务异常终结（其降档重试被另起）不触发监听器；降档重试任务自身正常终结（录满）触发**。理由：spec §一成功标准要求「录满 60s 产物自动拉回不丢失」，而 .25 常态就是降档链路——若降档链终结不触发，白录在主力验证设备上依旧闭合不了。异常终结（含非 -38 失败、被 stop 打断）一律不触发，由 `exc is None` 单条件承载，两读法在该分支上结论一致。

## Review Focus

spec 隐含但单任务测试未必自然覆盖、最可能伤到使用者的输入面（各线在归属任务里以显式测试钉住）：

1. **自然结束时设备已离线（pull 失败）** → 期望：会话收敛 failed、不悬挂、不冒泡崩溃监听器。→ Task 2 `test_natural_end_pull_failure_converges_failed`。
2. **监听器误触时新会话已被覆盖（旧终结延迟到达）** → 期望：守卫判 id 不符 no-op，绝不误停新段。→ Task 2 `test_auto_finalize_guard_noop_on_replaced_session`。
3. **自愈巡检与真实 stop 并发（pull 慢但活着）** → 期望：未超阈值的 stopping/pulling 不被误杀；收敛前复核状态，不把已 done 覆盖成 failed。→ Task 3 `test_self_heal_ignores_fresh_stopping` + 实现内 rm 后复核。
4. **清理时文件被占用/目录不存在（Windows 下载器仍持有 mp4；首次启动无目录）** → 期望：best-effort——单文件失败仅告警、其余照删、不阻塞退出/启动。→ Task 3 `test_cleanup_all_recordings_missing_dir_silent` / `test_cleanup_all_recordings_locked_file_best_effort`。
5. **降档链路自然结束仍须恰好触发一次监听器（.25 主路径回归面）** → 期望：晚失败降档链收敛后 fired==["d"] 恰一次；重复回调不二次触发。→ Task 2 `test_error_exit_does_not_fire_listener_degrade_then_end_fires`。

## 文件结构（改动面）

| 文件 | 职责变化 | 任务 |
|---|---|---|
| `backend/app/infrastructure/adb/cli.py` | 录屏回调保留 paths 表项、stop 探测兜底、自然结束监听器注册点与触发 | T1、T2 |
| `backend/app/domain/ports.py` | `AdbDriver` 协议补 `set_recording_natural_end_listener` 同步方法 | T2 |
| `backend/app/application/recording_service.py` | 监听器接线 + `_auto_finalize`；stopping/pulling 自愈；`cleanup_all_recordings`；循环首轮即扫 | T2、T3 |
| `backend/app/lifecycle.py` | shutdown 清盘挂钩 + 模块 docstring 顺序更新 | T3 |
| `backend/app/interfaces/http/recording.py` | GET done 失效收敛 404；done 响应附 `file_size` | T4 |
| `frontend/src/services/api.ts` | stopRecording/getRecording 响应类型补 `file_size` | T4 |
| `frontend/src/components/stream/RecordingPanel.vue` | done 态「重新录制」+ 大小信息行 + 静默复位注释 | T4 |
| `tests/unit/test_cli_screenrecord.py` | T1 新增 3 测试；T2 新增 3 监听器测试 | T1、T2 |
| `tests/application/test_recording_service.py` | T2 监听器 5 测试；T3 自愈/清理/循环 5 测试 | T2、T3 |
| `tests/unit/test_lifecycle.py`（新建） | lifespan 关服清盘调用链 | T3 |
| `tests/unit/test_recording_api.py` | file_size 与 GET 收敛 3 测试 | T4 |
| `frontend/src/components/stream/RecordingPanel.test.ts` | 既有 2 处更新 + 新增 3 测试 | T4 |
| `tests/manual/spike_42_recording_flow.py`（新建） | 真机五判据 | T5 |
| `docs/用户手册.md`、`方案/42-*.md`、`方案/README.md`、`方案/进度追踪.md`、`方案/后续事项评估与路线图.md`、`docs/经验记录.md` | 行为更新与回写 | T5 |
| 卫生项 6 处（方案 44 终评 Minor 移交，见 T5 Step 7 清单） | 版本自称/守卫/尾字节 | T5 |

---

### Task 1: cli 侧 stop 兜底 pull 路径（表项保留策略 + `ls -ld` 探测）— spec T1

**Files:**
- Modify: `backend/app/infrastructure/adb/cli.py:1179-1262`（`_recording_done_callback`、`stop_recording`）
- Test: `tests/unit/test_cli_screenrecord.py`（文件末尾追加）

**Interfaces:**
- Consumes: 既有 `_store_recording_task`（cli.py:1037）、`_recording_tasks`/`_recording_paths`（:377-378）、`_run_serial`。
- Produces: 新语义——任务终结回调仅弹 `_recording_tasks`（带 `is t` 身份守卫），**保留** `_recording_paths`；`stop_recording` 在任务缺失且路径表项在位时执行 `("shell","ls","-ld",<remote>)` 探测：rc=0 → 返回 remote_path，AdbError → 返回 ""；stop 消费即弹两表。Task 2 的监听器触发依赖本任务的「paths 保留 + 弹 tasks」行为。

- [ ] **Step 1: 写失败测试（3 个）**

追加到 `tests/unit/test_cli_screenrecord.py` 末尾（沿用该文件既有 fake_run/monkeypatch 范式；宽限窗收缩常量 `cli_module._START_GRACE_ITERATIONS/_START_GRACE_INTERVAL` 用法同 :101-102）：

```python
# ---------------------------------------------------------------------------
# 方案 42 §1：自然结束 stop 兜底 pull（表项保留 + ls 探测）
# ---------------------------------------------------------------------------

async def test_done_callback_keeps_path_pops_task(monkeypatch):
    """终结回调仅弹 tasks 表（身份守卫），保留 paths 表项供 stop 消费——白录闭合的前提。"""
    monkeypatch.setattr(cli_module, "_START_GRACE_ITERATIONS", 5)
    monkeypatch.setattr(cli_module, "_START_GRACE_INTERVAL", 0.01)

    async def fake_run(adr, *args, timeout=None):
        return ""  # 秒回 = screenrecord 录满自然正常退出

    monkeypatch.setattr(AdbCliDriver, "_run_serial", fake_run)
    d = AdbCliDriver()
    await d.start_recording("d", "/sdcard/r.mp4", "4M", 60)
    await asyncio.sleep(0.05)  # 冲刷 add_done_callback（对已完成任务经 call_soon 调度）
    assert "d" not in d._recording_tasks
    assert d._recording_paths.get("d") == "/sdcard/r.mp4"


async def test_stop_after_natural_end_probes_and_returns_path(monkeypatch):
    """task 已被回调弹出：stop 经 ls -ld 探测确认产物在位 → 返回 remote_path 供服务端 pull。"""
    monkeypatch.setattr(cli_module, "_START_GRACE_ITERATIONS", 5)
    monkeypatch.setattr(cli_module, "_START_GRACE_INTERVAL", 0.01)
    calls = []

    async def fake_run(adr, *args, timeout=None):
        calls.append(args)
        return ""

    monkeypatch.setattr(AdbCliDriver, "_run_serial", fake_run)
    d = AdbCliDriver()
    await d.start_recording("d", "/sdcard/r.mp4", "4M", 60)
    await asyncio.sleep(0.05)
    assert await d.stop_recording("d") == "/sdcard/r.mp4"
    assert ("d", "shell", "ls", "-ld", "/sdcard/r.mp4") in calls


async def test_stop_after_natural_end_file_missing_returns_empty(monkeypatch):
    """task 已弹出但设备端产物已不在（探测 rc≠0）→ 空串（服务端按无产物收敛 failed）。"""
    monkeypatch.setattr(cli_module, "_START_GRACE_ITERATIONS", 5)
    monkeypatch.setattr(cli_module, "_START_GRACE_INTERVAL", 0.01)
    calls = []

    async def fake_run(adr, *args, timeout=None):
        calls.append(args)
        if "ls" in args:
            raise AdbError("ADB command failed: ls: /sdcard/r.mp4: No such file or directory")
        return ""

    monkeypatch.setattr(AdbCliDriver, "_run_serial", fake_run)
    d = AdbCliDriver()
    await d.start_recording("d", "/sdcard/r.mp4", "4M", 60)
    await asyncio.sleep(0.05)
    assert await d.stop_recording("d") == ""
    assert ("d", "shell", "ls", "-ld", "/sdcard/r.mp4") in calls
```

- [ ] **Step 2: 运行确认三测试红**

Run: `PYTHONPATH=backend:. python -m pytest tests/unit/test_cli_screenrecord.py -q -k "keeps_path or probes or file_missing"`
Expected: 3 FAIL——test1 `KeyError/None != '/sdcard/r.mp4'`（旧回调弹了 paths）；test2 `'' == '/sdcard/r.mp4'` 且无 ls 调用；test3 返回 `''` 虽巧合同值但 `ls` 断言必红（旧代码不探测）。

- [ ] **Step 3: 实现 `_recording_done_callback` 表项策略**

`cli.py` `_cb` 内收尾两行（:1207-1208）替换为：

```python
            # 方案 42 §1：仅弹任务表（带身份守卫——stop 已先行弹出或表项被新任务
            # 替换时不越权）；路径表项保留到 stop 消费或下次 start 覆盖——
            # 「自然结束 → 用户/服务端再来取产物」不再白录。
            if self._recording_tasks.get(device_id) is t:
                self._recording_tasks.pop(device_id, None)
```

同函数 docstring（:1185-1186）中「再自清 _recording_tasks/_recording_paths 表项（防自然结束后的孤儿表项）」改为「再仅弹 _recording_tasks（路径表留待 stop 消费或下次 start 覆盖，方案 42 §1）」；其余降档语义描述不动。

- [ ] **Step 4: 实现 `stop_recording` 头部兜底**

`stop_recording` 开头（:1229-1232）替换为：

```python
        task = self._recording_tasks.pop(device_id, None)
        remote_path = self._recording_paths.pop(device_id, "")
        if task is None:
            if not remote_path:
                return ""
            # 方案 42 §1：任务已自然终结（回调已弹任务表）。设备端产物仍在则
            # 返回路径供服务端 pull 收尾；探测失败/不存在 → 无产物。
            try:
                await self._run_serial(device_id, "shell", "ls", "-ld", remote_path)
            except AdbError:
                logger.debug("recording_product_missing", device=device_id, remote=remote_path)
                return ""
            return remote_path
```

docstring 同步：`返回` 段补「task 缺失但设备端产物在位（自然结束）时也返回 remote_path」；`无活跃录制或无产物时返回空串` 口径不变。函数体其余（pkill/toybox/await task）零改动。

- [ ] **Step 5: 全文件回归绿**

Run: `PYTHONPATH=backend:. python -m pytest tests/unit/test_cli_screenrecord.py -q`
Expected: 全部 PASS——**既有 15 测试必须零修改通过**（既有 stop 测试在 start 同步返回后立即调用 stop：stop 在首个 await 前同步弹表，回调 call_soon 尚未执行，pkill 主路径不受影响；晚失败降档两用例轮询的是 `_recording_tasks`，不受 paths 保留影响）。若出现红，即为实现偏离，回查弹表时机。

- [ ] **Step 6: 定向门禁 + 提交**

```bash
python -m ruff check backend/app/infrastructure/adb/cli.py
python -m mypy backend/app
PYTHONPATH=backend:. python -m pytest tests/unit/test_cli_screenrecord.py -q
git add backend/app/infrastructure/adb/cli.py tests/unit/test_cli_screenrecord.py
git commit -m "feat: 录屏自然结束 stop 兜底 pull——回调仅弹任务表 + ls 探测产物（方案 42 T1）"
```

---

### Task 2: 自然结束监听器注册通道 + RecordingService 自动收尾 — spec T1 后段/§1 服务侧

**Files:**
- Modify: `backend/app/domain/ports.py`（`AdbDriver` 协议，rm_remote 之后）
- Modify: `backend/app/infrastructure/adb/cli.py`（`__init__` :377 附近、新公开方法、`_cb` 触发点）
- Modify: `backend/app/application/recording_service.py`（`__init__`、新方法×2）
- Test: `tests/unit/test_cli_screenrecord.py`（追加 3）、`tests/application/test_recording_service.py`（fixture 补一行 + 追加 5）

**Interfaces:**
- Consumes: T1 的「回调保 paths 弹 tasks」；`_recording_paths` 键值比对的表覆盖检测。
- Produces: `AdbDriver.set_recording_natural_end_listener(listener: Callable[[str], None] | None) -> None`（同步）；`AdbCliDriver._recording_natural_end_listener`；`RecordingService._on_recording_natural_end(device_id: str) -> None`、`RecordingService._auto_finalize(device_id: str, session_id: str) -> None`、`RecordingService._finalize_tasks: set[asyncio.Task[None]]`。Task 3/4 不依赖本任务接口，但真机 spike（T5）依赖整链。

- [ ] **Step 1: ports 协议 + 写失败测试（cli 3 个）**

`backend/app/domain/ports.py`：`AdbDriver` 类体内 `rm_remote` 协议方法之后追加（`Callable` 已在 :27 导入，零新增 import）：

```python
    def set_recording_natural_end_listener(
        self, listener: Callable[[str], None] | None
    ) -> None:
        """
        注册录屏自然结束监听器（方案 42 §1，依赖倒置：cli 不 import application）。

        设备端 screenrecord 后台任务正常终结（--time-limit 录满，降档链路的
        终结同样计入；异常终结不触发）时，以 device_id 为参在事件循环线程内
        同步调用监听器；监听器自行调度收尾。传 None 注销。
        """
        ...
```

`tests/unit/test_cli_screenrecord.py` 追加：

```python
# ---------------------------------------------------------------------------
# 方案 42 §1：自然结束监听器触发（cli 侧）
# ---------------------------------------------------------------------------

async def test_natural_end_fires_registered_listener(monkeypatch):
    """任务正常终结且 paths 表项仍指向本 remote_path → 监听器收到 device_id 恰一次。"""
    monkeypatch.setattr(cli_module, "_START_GRACE_ITERATIONS", 5)
    monkeypatch.setattr(cli_module, "_START_GRACE_INTERVAL", 0.01)

    async def fake_run(adr, *args, timeout=None):
        return ""

    monkeypatch.setattr(AdbCliDriver, "_run_serial", fake_run)
    d = AdbCliDriver()
    fired: list[str] = []
    d.set_recording_natural_end_listener(fired.append)
    await d.start_recording("d", "/sdcard/r.mp4", "4M", 60)
    await asyncio.sleep(0.05)
    assert fired == ["d"]


async def test_error_exit_does_not_fire_listener_degrade_then_end_fires(monkeypatch):
    """Ruling 钉桩：异常终结（降档引发）不触发；降档任务链录满后恰好触发一次。"""
    monkeypatch.setattr(cli_module, "_START_GRACE_ITERATIONS", 5)
    monkeypatch.setattr(cli_module, "_START_GRACE_INTERVAL", 0.01)
    calls = []

    async def fake_run(adr, *args, timeout=None):
        calls.append(args)
        if "--size" not in args:
            await asyncio.sleep(0.3)  # 晚失败：宽限窗外 start 已存表
            raise AdbError("ADB command failed: Unable to get output buffers (err=-38)")
        return ""  # 降档任务正常终结（录满）

    monkeypatch.setattr(AdbCliDriver, "_run_serial", fake_run)
    d = AdbCliDriver()
    fired: list[str] = []
    d.set_recording_natural_end_listener(fired.append)
    await d.start_recording("d", "/sdcard/r.mp4", "4M", 60)
    for _ in range(300):
        if fired:
            break
        await asyncio.sleep(0.01)
    assert fired == ["d"], "降档链自然结束应触发监听器（.25 常态路径不白录）"
    await asyncio.sleep(0.05)
    assert fired == ["d"]  # 首任务异常终结与后续回调均不再二次触发


async def test_listener_suppressed_when_path_overwritten():
    """守卫：任务正常终结但 paths 已被新 start 覆盖（表项指向他路径）→ 不触发。"""
    async def _done() -> str:
        return ""

    d = AdbCliDriver()
    fired: list[str] = []
    d.set_recording_natural_end_listener(fired.append)
    t: asyncio.Task[str] = asyncio.create_task(_done())
    await t
    d._recording_tasks["d"] = t
    d._recording_paths["d"] = "/sdcard/other.mp4"  # 模拟新 start 已覆盖
    cb = d._recording_done_callback("d", "/sdcard/a.mp4", "4M", 60)
    cb(t)
    assert fired == []
```

- [ ] **Step 2: 运行确认 cli 三测试红**

Run: `PYTHONPATH=backend:. python -m pytest tests/unit/test_cli_screenrecord.py -q -k "listener"`
Expected: FAIL——`AttributeError: 'AdbCliDriver' object has no attribute 'set_recording_natural_end_listener'`（test3 为 `fired == []` 恒真但前两测红即达门槛；test3 实现后必须仍绿——它是守卫钉桩）。

- [ ] **Step 3: 实现 cli 侧**

`AdbCliDriver.__init__` 中 `_recording_paths` 定义行之后追加：

```python
        # 录屏自然结束监听器（方案 42 §1，经 AdbDriver 协议由服务侧注册）
        self._recording_natural_end_listener: Callable[[str], None] | None = None
```

新公开方法放在 `_recording_done_callback` 定义之前：

```python
    def set_recording_natural_end_listener(
        self, listener: Callable[[str], None] | None
    ) -> None:
        """注册录屏自然结束监听器（见 AdbDriver 协议 docstring，方案 42 §1）。"""
        self._recording_natural_end_listener = listener
```

`_cb` 在 T1 弹表块之后追加触发块：

```python
            # 方案 42 §1 + Ruling：正常终结（exc=None，含降档链路录满）且 paths
            # 表项仍指向本 remote_path（未被新 start 覆盖、未被 stop 消费）→
            # 通知服务侧自动收尾。异常终结（含引发降档的晚失败）一律不触发。
            if exc is None and self._recording_paths.get(device_id) == remote_path:
                if self._recording_natural_end_listener is not None:
                    self._recording_natural_end_listener(device_id)
```

Run: `PYTHONPATH=backend:. python -m pytest tests/unit/test_cli_screenrecord.py -q` → 全绿后进入 Step 4。

- [ ] **Step 4: 写失败测试（服务侧 5 个 + fixture 补行）**

`tests/application/test_recording_service.py`：`mock_adb` fixture 中 `adb.rm_remote = AsyncMock()` 之后补一行：

```python
    adb.set_recording_natural_end_listener = MagicMock()  # 同步注册点（方案 42 §1）
```

文件头 docstring 的测试内容清单追加「自然结束监听器自动收尾（方案 42 §1）」。文件末尾追加：

```python
# ---------------------------------------------------------------------------
# 自然结束自动收尾（方案 42 §1）
# ---------------------------------------------------------------------------

def _registered_listener(mock_adb):
    """取 RecordingService 构造时经依赖倒置通道注册的自然结束监听器。"""
    mock_adb.set_recording_natural_end_listener.assert_called_once()
    return mock_adb.set_recording_natural_end_listener.call_args[0][0]


async def test_init_registers_natural_end_listener(service, mock_adb):
    listener = _registered_listener(mock_adb)
    assert listener == service._on_recording_natural_end


async def test_natural_end_auto_finalizes(service, mock_adb, tmp_path):
    """监听器 → 自动 stop 收尾（pull→done）——等效系统代为执行了一次停止。"""
    s = await service.start("d1")
    listener = _registered_listener(mock_adb)
    mock_adb.stop_recording.return_value = s.remote_path
    mock_adb.pull_recording.return_value = str(tmp_path / "rec.mp4")
    listener("d1")
    for _ in range(5):
        await asyncio.sleep(0)
    assert s.state == RecordingState.DONE.value
    assert s.local_path == str(tmp_path / "rec.mp4")
    mock_adb.stop_recording.assert_called_once_with("d1")
    mock_adb.pull_recording.assert_called_once_with("d1", s.remote_path, str(tmp_path))
    mock_adb.rm_remote.assert_called_once_with("d1", s.remote_path)


async def test_natural_end_pull_failure_converges_failed(service, mock_adb):
    """Review Focus ①：自然结束时设备离线（pull None）→ 收敛 failed，不悬挂不冒泡。"""
    s = await service.start("d1")
    listener = _registered_listener(mock_adb)
    mock_adb.pull_recording.return_value = None
    listener("d1")
    for _ in range(5):
        await asyncio.sleep(0)
    assert s.state == RecordingState.FAILED.value
    mock_adb.rm_remote.assert_called_once_with("d1", s.remote_path)


async def test_natural_end_guard_noop_when_not_recording(service, mock_adb):
    """守卫一：无会话 / 会话已非 recording（用户已手动停止推进）→ no-op。"""
    listener = _registered_listener(mock_adb)
    listener("ghost")
    s = await service.start("d1")
    s.state = RecordingState.STOPPING.value
    listener("d1")
    for _ in range(5):
        await asyncio.sleep(0)
    mock_adb.stop_recording.assert_not_called()


async def test_auto_finalize_guard_noop_on_replaced_session(service, mock_adb):
    """Review Focus ②：收尾协程 id 失配（会话被新 start 覆盖）→ no-op，不误停新段。"""
    s_a = await service.start("d1")
    await service.stop("d1")
    mock_adb.stop_recording.reset_mock()
    s_b = await service.start("d1")
    await service._auto_finalize("d1", s_a.id)
    for _ in range(5):
        await asyncio.sleep(0)
    assert s_b.state == RecordingState.RECORDING.value
    mock_adb.stop_recording.assert_not_called()
```

需在该文件 import 段补 `import asyncio`（现有 os/time——以文件实际 import 为准，缺则加）。

- [ ] **Step 5: 运行确认服务测试红**

Run: `PYTHONPATH=backend:. python -m pytest tests/application/test_recording_service.py -q`
Expected: 新 5 测试 FAIL——`AttributeError: 'RecordingService' object has no attribute '_on_recording_natural_end'` 或 `set_recording_natural_end_listener` 未被调用断言失败；既有 15 测试保持 PASS。

- [ ] **Step 6: 实现服务侧**

`recording_service.py`：

1. 模块 docstring「职责」清单追加第 6 条：`6. 自然结束自动收尾（方案 42 §1：cli 监听器 → 复用 stop 路径拉回产物）`。
2. `__init__` 替换为：

```python
    def __init__(self, adb: AdbDriver) -> None:
        """
        参数：
            adb: 用于设备通信的 ADB 驱动。
        """
        self.adb = adb
        # 每设备至多一个会话（key: device_id）；终态会话在下一次 start 时被覆盖
        self.sessions: dict[str, RecordingSession] = {}
        # 周期清理任务（lifespan 启动/停止）
        self.cleanup_task: asyncio.Task[None] | None = None
        # 自动收尾协程引用集：防事件循环 GC 中途回收 pending 任务（asyncio 官方告诫）
        self._finalize_tasks: set[asyncio.Task[None]] = set()
        # 自然结束 → 自动收尾监听器（方案 42 §1，依赖倒置注册，协议见 AdbDriver）
        adb.set_recording_natural_end_listener(self._on_recording_natural_end)
```

3. `stop()` 与 `get()` 之间新增一节：

```python
    # -------------------------------------------------------------------
    # 自然结束自动收尾（方案 42 §1）
    # -------------------------------------------------------------------

    def _on_recording_natural_end(self, device_id: str) -> None:
        """
        cli 自然结束监听器（事件循环线程内同步回调）：会话仍 recording 才调度
        收尾协程；非 recording（用户已手动 stop / 无会话）no-op。
        """
        session = self.sessions.get(device_id)
        if session is None or session.state != RecordingState.RECORDING.value:
            return
        task = asyncio.create_task(self._auto_finalize(device_id, session.id))
        self._finalize_tasks.add(task)
        task.add_done_callback(self._finalize_tasks.discard)

    async def _auto_finalize(self, device_id: str, session_id: str) -> None:
        """
        自然结束自动收尾：等效系统代为执行一次 stop（复用五态机 pull→done）。

        执行时二次守卫（会话 id 匹配且仍 recording）——防协程排队期间会话
        被手动 stop 收尾并被新 start 覆盖后误停新段（Review Focus ②）。
        收尾自身异常不冒泡（stopping/pulling 悬挂由方案 42 §2 自愈兜底）。
        """
        session = self.sessions.get(device_id)
        if (
            session is None
            or session.id != session_id
            or session.state != RecordingState.RECORDING.value
        ):
            return
        logger.info("recording_natural_end_finalize", session=session_id, device=device_id)
        try:
            await self.stop(device_id)
        except Exception:  # noqa: BLE001
            logger.exception("recording_auto_finalize_failed", device=device_id)
```

- [ ] **Step 7: 全量绿 + 提交**

```bash
PYTHONPATH=backend:. python -m pytest tests/unit/test_cli_screenrecord.py tests/application/test_recording_service.py -q
python -m ruff check backend/app
python -m mypy backend/app
git add backend/app/domain/ports.py backend/app/infrastructure/adb/cli.py backend/app/application/recording_service.py tests/unit/test_cli_screenrecord.py tests/application/test_recording_service.py
git commit -m "feat: 自然结束监听器与服务侧自动收尾（方案 42 T2）——依赖倒置注册 + 双守卫"
```

Expected: 两文件全绿；ruff/mypy 零告警。

---

### Task 3: stopping/pulling 自愈扩展 + cleanup_all_recordings + 关服/启动清盘挂钩 — spec §2 §3

**Files:**
- Modify: `backend/app/application/recording_service.py`（类常量、`_self_heal`、新方法、`_cleanup_loop`）
- Modify: `backend/app/lifecycle.py`（shutdown 序列 + 模块 docstring）
- Test: `tests/application/test_recording_service.py`（追加 5）、`tests/unit/test_lifecycle.py`（新建）

**Interfaces:**
- Consumes: T2 产出的服务实例（fixture 直接复用）。
- Produces: `RecordingService.STUCK_STATE_TIMEOUT_SECONDS: float = 120.0`；`RecordingService.LOCAL_FILE_GLOB: str = "openscrcpy_rec_*.mp4"`；`RecordingService.cleanup_all_recordings() -> None`（同步、best-effort）；`_cleanup_loop` 首轮即时「扫盘 + cleanup」；lifecycle shutdown 调用序 `stop_cleanup_task → cleanup_all_recordings`。Task 4/5 与 spike 依赖 `cleanup_all_recordings` 语义。

- [ ] **Step 1: 写失败测试（服务 5 个）**

`tests/application/test_recording_service.py` 末尾追加：

```python
# ---------------------------------------------------------------------------
# stopping/pulling 自愈 + 关服/启动清盘（方案 42 §2 §3）
# ---------------------------------------------------------------------------

async def test_self_heal_converges_stuck_pulling(service, mock_adb):
    """§2：stopping/pulling 超 time_limit+120s 无推进 → 收敛 failed + 设备端兜底 rm。"""
    stuck = RecordingSession(
        id="stuck0000000",
        device_id="d1",
        started_at=time.time()
        - (settings().recording.time_limit + RecordingService.STUCK_STATE_TIMEOUT_SECONDS + 1),
        state=RecordingState.PULLING.value,
        remote_path="/sdcard/openscrcpy_rec_stuck0.mp4",
    )
    service.sessions["d1"] = stuck
    await service._self_heal()
    assert stuck.state == RecordingState.FAILED.value
    mock_adb.rm_remote.assert_called_once_with("d1", stuck.remote_path)


async def test_self_heal_ignores_fresh_stopping(service, mock_adb):
    """Review Focus ③：正在收尾（started_at 未超阈）的 stopping 不得被误杀。"""
    fresh = RecordingSession(
        id="fresh0000000",
        device_id="d9",
        state=RecordingState.STOPPING.value,
        remote_path="/sdcard/openscrcpy_rec_fresh0.mp4",
    )
    service.sessions["d9"] = fresh
    await service._self_heal()
    assert fresh.state == RecordingState.STOPPING.value
    mock_adb.rm_remote.assert_not_called()


def test_cleanup_all_recordings_clears_pattern_only(service, tmp_path):
    """§3：仅删 openscrcpy_rec_*.mp4；他名文件与旁路文件不动。"""
    hits = [tmp_path / "openscrcpy_rec_aaa111222333.mp4", tmp_path / "openscrcpy_rec_bbb111222333.mp4"]
    keeps = [tmp_path / "other.mp4", tmp_path / "openscrcpy_rec_c.txt"]
    for p in hits + keeps:
        p.write_bytes(b"x")
    service.cleanup_all_recordings()
    assert not any(p.exists() for p in hits)
    assert all(p.exists() for p in keeps)


def test_cleanup_all_recordings_missing_dir_silent(tmp_path, monkeypatch):
    """Review Focus ④a：目录不存在（首次启动）→ 静默返回不抛。"""
    monkeypatch.setattr(settings().recording, "dir", str(tmp_path / "not_created"))
    RecordingService(MagicMock()).cleanup_all_recordings()  # 不抛即通过


def test_cleanup_all_recordings_locked_file_best_effort(service, tmp_path, monkeypatch):
    """Review Focus ④b：单文件删除失败（Windows 下载器持有）→ 仅告警不抛，其余照删。"""
    (tmp_path / "openscrcpy_rec_aaa111222333.mp4").write_bytes(b"x")
    (tmp_path / "openscrcpy_rec_bbb111222333.mp4").write_bytes(b"x")

    def _boom(self: Path, *args: object, **kwargs: object) -> None:
        if self.name.endswith("aaa111222333.mp4"):
            raise OSError("文件被占用")

    original = Path.unlink

    def fake_unlink(self: Path, *args: object, **kwargs: object) -> None:
        _boom(self, *args, **kwargs)
        original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fake_unlink)
    service.cleanup_all_recordings()  # 不抛即通过
    assert (tmp_path / "openscrcpy_rec_aaa111222333.mp4").exists()  # 被占文件保留
    assert not (tmp_path / "openscrcpy_rec_bbb111222333.mp4").exists()  # 其余已删


async def test_cleanup_loop_sweeps_immediately_at_startup(service, monkeypatch):
    """§3：tick 改「首轮即时」——启动即扫盘 + cleanup，不再先睡 3600s（白盘一小时）。"""
    swept: list[bool] = []
    cleaned: list[bool] = []
    monkeypatch.setattr(RecordingService, "cleanup_all_recordings", lambda self: swept.append(True))

    async def fake_cleanup(self: RecordingService) -> None:
        cleaned.append(True)

    monkeypatch.setattr(RecordingService, "cleanup", fake_cleanup)
    await service.start_cleanup_task()
    await asyncio.sleep(0.05)
    assert swept == [True]
    assert cleaned == [True]
    await service.stop_cleanup_task()
```

（`test_recording_service.py` 现有 import 已含 `MagicMock`/`Path`/`time`/`settings`；补 `RecordingService` 已有；缺 `asyncio` 则与 T2 Step 4 同批已补。）

- [ ] **Step 2: 运行确认红**

Run: `PYTHONPATH=backend:. python -m pytest tests/application/test_recording_service.py -q -k "stuck or fresh_stopping or cleanup_all or sweeps_immediately"`
Expected: FAIL——`STUCK_STATE_TIMEOUT_SECONDS` AttributeError / `cleanup_all_recordings` 不存在 / 循环首轮未即时（swept 空）。

- [ ] **Step 3: 实现服务侧三处**

`recording_service.py` 类常量区（`SELF_HEAL_GRACE_SECONDS` 之后）追加：

```python
    # 滞留态自愈阈值（秒）：stopping/pulling 超过 time_limit + 该值仍无推进
    # → 判悬挂收敛 failed（方案 42 §2；正常 60s 录制 + 收尾远不到此阈值）
    STUCK_STATE_TIMEOUT_SECONDS = 120.0
    # 主机落盘产物文件名模式（与 REMOTE_PREFIX basename 同源，方案 42 §3 清盘依据）
    LOCAL_FILE_GLOB = "openscrcpy_rec_*.mp4"
```

`_self_heal` 整体替换：

```python
    async def _self_heal(self) -> None:
        """
        会话自愈（方案 37 B3 + 方案 42 §2）：

        - recording：started_at 超 time_limit + 宽限仍未收尾 → 自动走 stop；
        - stopping/pulling：超 time_limit + STUCK 仍无推进（stop 中途被非
          AdbError 异常打断的罕见路径）→ 设备端兜底 rm 后收敛 failed，
          消除持续 409 的滞留。rm 等待期间复核状态，防与真实 stop 并发误杀
          已 done 会话（Review Focus ③）。
        """
        cfg = settings().recording
        now = time.time()
        stale_ids = [
            s.device_id
            for s in self.sessions.values()
            if s.state == RecordingState.RECORDING.value
            and s.started_at < now - (cfg.time_limit + self.SELF_HEAL_GRACE_SECONDS)
        ]
        for device_id in stale_ids:
            logger.warning("recording_self_heal", device=device_id)
            await self.stop(device_id)
        stuck_cutoff = now - (cfg.time_limit + self.STUCK_STATE_TIMEOUT_SECONDS)
        for s in list(self.sessions.values()):
            if s.state not in (RecordingState.STOPPING.value, RecordingState.PULLING.value):
                continue
            if s.started_at >= stuck_cutoff:
                continue
            logger.warning("recording_stuck_self_heal", device=s.device_id, state=s.state)
            await self.adb.rm_remote(s.device_id, s.remote_path)
            # rm 等待期间真实 stop 可能已完成收尾：复核后才落 failed
            if s.state in (RecordingState.STOPPING.value, RecordingState.PULLING.value):
                s.state = RecordingState.FAILED.value
```

`cleanup()` 之后新增：

```python
    def cleanup_all_recordings(self) -> None:
        """
        删除主机落盘目录全部录屏产物（方案 42 §3）。

        依据：会话表纯内存——进程退出后 done 会话即无下载入口，残留文件属
        纯垃圾；录像按磁盘保留策略定位为暂存（关服清空合规而非例外）。
        调用点：lifecycle 优雅退出挂钩 + 清理循环首轮（强杀/崩溃兜底）。
        best-effort：单文件失败（Windows 下载器持有等）仅告警，不阻塞退出。
        """
        directory = self.resolve_dir()
        if not directory.is_dir():
            return
        for path in directory.glob(self.LOCAL_FILE_GLOB):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.warning("recording_cleanup_all_failed", path=str(path))
```

`_cleanup_loop` 整体替换：

```python
    async def _cleanup_loop(self) -> None:
        """
        后台清理循环（方案 37 B3 + 42 §3）：首轮**立即**执行——扫盘清上个
        进程遗留（强杀/崩溃无退出钩子，下次启动兜底）+ 常规 cleanup；此后
        每 3600s 一轮 cleanup()；单轮异常记警告续跑。
        """
        first = True
        try:
            while True:
                try:
                    if first:
                        self.cleanup_all_recordings()
                    else:
                        await asyncio.sleep(self.CLEANUP_INTERVAL_SECONDS)
                    await self.cleanup()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("recording_cleanup_iteration_failed", error=str(exc))
                first = False
        except asyncio.CancelledError:
            logger.info("recording_cleanup_loop_cancelled")
```

（sleep 在首轮分支内、且 `except Exception` 不吞 CancelledError——BaseException 直落外层，取消语义保持。）

- [ ] **Step 4: lifecycle 挂钩 + 失败测试（新建文件）**

新建 `tests/unit/test_lifecycle.py`：

```python
"""
应用生命周期钩子测试（方案 42 §3 关服清盘链路）
================================================

monkeypatch 注入全 fake 服务跑 lifespan（不触真实 SQLite/ADB/日志/热重载），
断言：启动拉起录屏周期任务；关闭按序「先停 tick → cleanup_all_recordings
清盘」。对应 lifecycle.py 的 shutdown 顺序第 3 步扩展。
"""

from unittest.mock import AsyncMock, MagicMock

from app import lifecycle
from app.core.config import settings


class FakeRecordingService:
    """录屏服务替身：按调用名记录序列，供顺序断言。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def start_cleanup_task(self) -> None:
        self.calls.append("start")

    async def stop_cleanup_task(self) -> None:
        self.calls.append("stop")

    def cleanup_all_recordings(self) -> None:
        self.calls.append("cleanup_all")


async def test_lifespan_shutdown_clears_all_recordings(monkeypatch) -> None:
    rec = FakeRecordingService()
    monkeypatch.setattr(settings().app, "config_watch", False)
    monkeypatch.setattr(lifecycle, "setup_logging", lambda: None)
    monkeypatch.setattr(lifecycle, "init_db", AsyncMock())

    fake_debug = MagicMock()
    fake_debug.restore_sessions = AsyncMock()
    fake_debug.start_cleanup_task = AsyncMock()
    fake_debug.stop_cleanup_task = AsyncMock()
    fake_debug.writer.flush = AsyncMock()
    monkeypatch.setattr("app.deps.get_recording_service", lambda: rec)
    monkeypatch.setattr("app.deps.get_debug_service", lambda: fake_debug)
    monkeypatch.setattr(
        "app.deps.get_performance_service", lambda: MagicMock(cleanup=AsyncMock())
    )
    monkeypatch.setattr(
        "app.deps.get_network_service", lambda: MagicMock(cleanup=AsyncMock())
    )

    async with lifecycle.lifespan(MagicMock()):
        assert rec.calls == ["start"]

    assert rec.calls == ["start", "stop", "cleanup_all"]
```

Run: `PYTHONPATH=backend:. python -m pytest tests/unit/test_lifecycle.py -q`
Expected: FAIL——末断言 `rec.calls == ["start", "stop"]`（现实现无清盘步）。

- [ ] **Step 5: 实现 lifecycle 挂钩**

`backend/app/lifecycle.py` :93-94 替换：

```python
    # 停止录屏周期清理任务（方案 37 B3）
    recording_service = get_recording_service()
    await recording_service.stop_cleanup_task()

    # 退出清盘：录像属暂存——退出后会话内存态随进程消失，残留文件已无
    # 下载入口（纯垃圾），清空符合磁盘数据保留策略（方案 42 §3）
    recording_service.cleanup_all_recordings()
```

模块 docstring 两处同步：启动顺序第 4 步尾注补「（首轮即含强杀残留扫盘清，方案 42 §3）」；关闭顺序第 3 步改为「stop_cleanup_task() — 停止后台清理任务（recording + debug 日志）；随后 RecordingService.cleanup_all_recordings() 清空录像暂存目录（方案 42 §3）」。

- [ ] **Step 6: 全量绿 + 提交**

```bash
PYTHONPATH=backend:. python -m pytest tests/application/test_recording_service.py tests/unit/test_lifecycle.py tests/unit/test_recording_api.py -q
python -m ruff check backend/app
python -m mypy backend/app
git add backend/app/application/recording_service.py backend/app/lifecycle.py tests/application/test_recording_service.py tests/unit/test_lifecycle.py
git commit -m "feat: stopping/pulling 滞留自愈 + 录屏关服清盘与启动即扫（方案 42 T3）"
```

---

### Task 4: done 态完备化——GET 收敛失效 + file_size + 前端重新录制/信息行 — spec §4

**Files:**
- Modify: `backend/app/interfaces/http/recording.py`（`_session_body`、`get_recording`、文件 docstring）
- Modify: `frontend/src/services/api.ts`（`stopRecording`/`getRecording` 返回类型）
- Modify: `frontend/src/components/stream/RecordingPanel.vue`
- Test: `tests/unit/test_recording_api.py`（追加 3）、`frontend/src/components/stream/RecordingPanel.test.ts`（既有 2 处更新 + 新增 3）

**Interfaces:**
- Consumes: T3 的清理语义（file 失效来源）；既有 `_done_session_with_file` 测试助手。
- Produces: done 会话响应体新增可选键 `file_size: int | null`（recording/failed 响应不带）；`GET /api/recording/{id}` 对「done 但产物缺失」收敛 `session.state → failed` 并 404；前端类名 `.record-again`、`.record-info`（文案 `约 N.N MB`）。

- [ ] **Step 1: 写失败测试（后端 3 个）**

`tests/unit/test_recording_api.py` 末尾追加：

```python
# ---------------------------------------------------------------------------
# done 态完备化（方案 42 §4：file_size 信息 + 失效收敛）
# ---------------------------------------------------------------------------

def test_get_done_returns_file_size(client, fake: FakeRecordingService, tmp_path) -> None:
    """GET done 会话且产物在位 → 200 + file_size（面板信息行数据源）。"""
    mp4 = tmp_path / "rec-d1.mp4"
    mp4.write_bytes(b"12345")
    fake.dir = tmp_path
    _done_session_with_file(fake, mp4)
    app.dependency_overrides[get_recording_service] = lambda: fake
    try:
        response = client.get("/api/recording/d1")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["file_size"] == 5


def test_get_done_missing_file_converges_404(client, fake: FakeRecordingService, tmp_path) -> None:
    """产物已被清理（双限/关服清盘）但会话滞留 done → 收敛 failed 并 404（前端静默 idle）。"""
    fake.dir = tmp_path
    _done_session_with_file(fake, tmp_path / "gone.mp4")
    app.dependency_overrides[get_recording_service] = lambda: fake
    try:
        response = client.get("/api/recording/d1")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NO_RECORDING"
    assert fake.sessions["d1"].state == RecordingState.FAILED.value


def test_stop_done_includes_file_size(client, fake: FakeRecordingService, tmp_path) -> None:
    """stop 返回 done 的响应即带 file_size——前端无需二次请求拿大小。"""
    mp4 = tmp_path / "rec-d1.mp4"
    mp4.write_bytes(b"abcdef")
    fake.sessions["d1"] = RecordingSession(
        id="rec-d1",
        device_id="d1",
        state=RecordingState.RECORDING.value,
        local_path=str(mp4),
    )
    app.dependency_overrides[get_recording_service] = lambda: fake
    try:
        response = client.post("/api/recording/d1/stop")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["session"]["state"] == RecordingState.DONE.value
    assert response.json()["file_size"] == 6
```

Run: `PYTHONPATH=backend:. python -m pytest tests/unit/test_recording_api.py -q -k "file_size or converges"`
Expected: FAIL——KeyError 'file_size' / 404 未发生（旧 GET 对 done 直返 200）。

- [ ] **Step 2: 实现路由侧**

`recording.py` `_session_body` 替换：

```python
def _session_body(session: RecordingSession) -> dict[str, Any]:
    """会话响应体：dataclass → JSON；done 会话附 file_size（方案 42 §4 面板信息行）。"""
    body: dict[str, Any] = {"session": asdict(session)}
    if session.state == RecordingState.DONE.value and session.local_path:
        try:
            body["file_size"] = Path(session.local_path).stat().st_size
        except OSError:
            body["file_size"] = None
    return body
```

`get_recording` 中 `if session is None: raise ...` 之后、return 之前插入：

```python
    if session.state == RecordingState.DONE.value and (
        not session.local_path or not Path(session.local_path).is_file()
    ):
        # 产物已被清理（双限/关服清盘）而会话滞留 done：收敛 failed 并 404——
        # 前端据此静默复位 idle，消除「有面板无文件」假象（方案 42 §4）
        session.state = RecordingState.FAILED.value
        raise RecordingNotFoundError(device_id)
```

文件头 docstring `GET /api/recording/{device_id}` 行尾补「（done 产物已被清理 → 收敛 failed 并 404，方案 42 §4）」。

回归：既有 `test_get_session_passthrough`（recording 态，严格等值 `{"session": ...}`）与 stop 终态测试必须零修改通过——file_size 仅在 done 出现。

- [ ] **Step 3: 写失败测试（前端）**

`RecordingPanel.test.ts`：

1. 既有测试「停止返回 done + local_path → video 预览与下载链接指向 /file 端点」的 stopRecording mock 改为：

```typescript
    mockApi.stopRecording.mockResolvedValue({
      session: sessionWith({ state: 'done', local_path: 'data/recordings/s1.mp4' }),
      file_size: 3145728,
    })
```

并在其断言区末尾追加：

```typescript
    expect(wrapper.find('.record-again').exists()).toBe(true)
    expect(wrapper.find('.record-info').text()).toBe('约 3.0 MB')
```

2. `mountDonePanel` 助手的 getRecording mock 改为：

```typescript
  mockApi.getRecording.mockResolvedValue({
    session: sessionWith({ state: 'done', local_path: 'data/recordings/s1.mp4' }),
    file_size: 1048576,
  })
```

3. `describe('RecordingPanel')` 内新增：

```typescript
  it('done 态「重新录制」：点击 → 调 startRecording 进入录制态并清信息行（方案 42 T4）', async () => {
    const wrapper = await mountDonePanel()
    mockApi.startRecording.mockResolvedValue({ session: sessionWith() })

    await wrapper.find('.record-again').trigger('click')
    await flushPromises()

    expect(mockApi.startRecording).toHaveBeenCalledWith('dev1')
    expect(wrapper.find('.record-timer').exists()).toBe(true)
    expect(wrapper.find('.record-info').exists()).toBe(false)
  })

  it('挂载恢复：GET 会话 404（产物已被清理，后端已收敛）→ 静默回 idle 不弹错误（方案 42 T4）', async () => {
    mockApi.getRecording.mockRejectedValue({ response: { status: 404 } })
    const wrapper = mountPanel()
    await flushPromises()

    expect(wrapper.find('.record-start').exists()).toBe(true)
    expect(h.ElMessage.error).not.toHaveBeenCalled()
  })

  it('挂载恢复：done + file_size → 预览与大小信息行（方案 42 T4）', async () => {
    const wrapper = await mountDonePanel()

    expect(wrapper.find('video').exists()).toBe(true)
    expect(wrapper.find('.record-info').text()).toBe('约 1.0 MB')
  })
```

Run: `cd frontend && npx vitest run src/components/stream/RecordingPanel.test.ts`
Expected: 新断言/新用例 FAIL（`.record-again`/`.record-info` 不存在），既有用例保持 PASS。

- [ ] **Step 4: 实现前端**

`api.ts`：`stopRecording` 与 `getRecording` 返回类型分别改为：

```typescript
    return res.data as { session: RecordingSession; file_size?: number | null }
```

`RecordingPanel.vue`：

1. 头部注释 done 段（:10-13）改为：

```
    done      — <video> 预览 + 下载链接（均指向 GET /api/recording/{id}/file）
              + 「导出 webm」按钮（B6：captureStream + MediaRecorder 抽
              min(时长, 10s) 短片，fps 10 控体积；浏览器不支持时禁用降级）
              + 「重新录制」按钮（方案 42 §4：直接再 start，终态可被新会话
              覆盖、互斥语义不变）+ 产物大小信息行（后端 file_size）
```

2. 模板 done 块 `</el-button>` 之后（`.recording-done` 内末尾）追加：

```html
      <el-button class="record-again" @click="start">重新录制</el-button>
      <div v-if="fileSizeText" class="record-info">{{ fileSizeText }}</div>
```

3. script setup：`const exporting = ref(false)` 之后追加：

```typescript
const fileSize = ref(0)

/** 产物大小信息行文案（方案 42 §4；未知/为 0 时不显示）。 */
const fileSizeText = computed(
  () => (fileSize.value > 0 ? `约 ${(fileSize.value / 1048576).toFixed(1)} MB` : '')
)
```

4. `start()` 成功分支 `sessionId.value = res.session?.id ?? ''` 之后加 `fileSize.value = 0`。
5. `stop()` done 分支 `sessionId.value = session.id ?? ''` 之后加 `fileSize.value = res?.file_size ?? 0`。
6. `onMounted` done 分支同样在 `sessionId.value = session.id ?? ''` 后加 `fileSize.value = res?.file_size ?? 0`。
7. `onMounted` 的 catch 注释改为 `state.value = 'idle' // 无会话 / 产物已被清理（后端收敛 404）：静默复位（方案 42 §4）`。
8. `<style scoped>` 末尾追加：

```css
.record-info {
  font-size: 12px;
  color: #909399;
}
```

- [ ] **Step 5: 前端门禁绿**

```bash
cd frontend && npx vitest run src/components/stream/RecordingPanel.test.ts && npx vitest run && npx vue-tsc -b
```

Expected: 组件测试全绿、前端全量测试零回归、类型检查零错误。

- [ ] **Step 6: 后端门禁 + 提交**

```bash
python -m ruff check backend/app
python -m mypy backend/app
PYTHONPATH=backend:. python -m pytest tests/unit/test_recording_api.py -q
git add backend/app/interfaces/http/recording.py tests/unit/test_recording_api.py frontend/src/services/api.ts frontend/src/components/stream/RecordingPanel.vue frontend/src/components/stream/RecordingPanel.test.ts
git commit -m "feat: done 态重新录制按钮与大小信息行 + GET 失效收敛（方案 42 T4）"
```

---

### Task 5: 门禁全量 + 真机 spike（.25）+ 文档回写 + 方案 44 终评卫生项 — spec §5/T5

**Files:**
- Create: `tests/manual/spike_42_recording_flow.py`
- Modify: `docs/用户手册.md`（§5.4）、`方案/42-录屏流程优化与磁盘清理方案.md`、`方案/README.md`、`方案/进度追踪.md`、`方案/后续事项评估与路线图.md`、`docs/经验记录.md`（E028 步 6 grep 面）
- 卫生项：`tests/scrcpy/test_scrcpy_minimal.sh`、`backend/app/scrcpy/server_manager.py:316`、`backend/app/scrcpy/stream_protocol.py:2`、`backend/app/scrcpy/control_sender.py:13`、`backend/app/scrcpy/constants.py:135`、`backend/app/infrastructure/stream/scrcpy.py:24`、`tests/unit/test_fetch_tools.py`（尾字节）、`backend/app/scrcpy/README.md`（下一步注）

**Interfaces:**
- Consumes: T1-T4 全部产出。
- Produces: 真机验收记录（并入方案 42 文档实施记录节）；文档状态终态。

- [ ] **Step 1: 后端全量门禁**

```bash
python -m ruff check backend/app
python -m mypy backend/app
PYTHONPATH=backend:. python -m pytest tests/ --ignore=tests/e2e -q --cov=app --cov-fail-under=80
```

Expected: 全绿（基线 1131 passed 附近 + 本计划新增 ~19 项）。任何失败先修复再走后续步骤。

- [ ] **Step 2: 写 spike 脚本**

新建 `tests/manual/spike_42_recording_flow.py`（真机判据脚本，不入 CI；仿 spike_37b 范式——直驱 RecordingService + AdbCliDriver，不自起 HTTP）：

```python
#!/usr/bin/env python3
"""方案 42 真机验收 spike：录屏流程优化与磁盘清理（非 pytest，不入 CI）。

判据（默认 .25；--devices 可换 .18/.33）：
    S42-1 连环录制：录 5s → stop done → 同设备立即再 start（done 覆盖态，
          即「重新录制」后端等价路径）→ 再录 5s → done；两段产物均在位
    S42-2 自然录满自动收尾：start 后**不做任何 stop**，等设备端 time_limit
          录满（.25 含降档）→ 服务经监听器自动收敛 done，产物 ftyp + 大小>0，
          设备端无残留，轮询上限 time_limit + 60s
    S42-3 录制中离开页面（直 API 模拟）：与 S42-2 同服务端路径；额外验证
          自动收尾完成后新 start 不被 409（进行态窗口确已消除）
    S42-4 关服清盘：cleanup_all_recordings 只清 openscrcpy_rec_*.mp4，
          其他文件保留（真实目录 + 哨兵文件）
    S42-5 启动即扫（强杀重启模拟）：新实例 start_cleanup_task → 首轮即时
          清掉哨兵残留（无需等 3600s）

用法（仓库根，Git Bash）：
    MSYS_NO_PATHCONV=1 PYTHONPATH=backend python -u tests/manual/spike_42_recording_flow.py
    MSYS_NO_PATHCONV=1 PYTHONPATH=backend python -u tests/manual/spike_42_recording_flow.py \
        --devices 192.168.8.25:5555 --out /tmp/s42.json

退出码：存在 fail → 1；否则 0。spike 自产 mp4 用后即清（数据保留策略）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("MSYS_NO_PATHCONV", "1")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from app.application.recording_service import RecordingService  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.domain.recording import RecordingState  # noqa: E402
from app.infrastructure.adb.cli import AdbCliDriver  # noqa: E402

DEFAULT_DEVICES = ["192.168.8.25:5555"]
REC_GLOB = "/sdcard/openscrcpy_rec_*.mp4"


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def adb_shell(device: str, args: list[str], timeout: float = 30.0) -> tuple[int, str]:
    r = subprocess.run(
        ["adb", "-s", device, "shell", *args], capture_output=True, timeout=timeout
    )
    out = r.stdout.decode("utf-8", errors="replace")
    err = r.stderr.decode("utf-8", errors="replace")
    log(f"adb[{device}] shell {' '.join(args)} -> rc={r.returncode} err={err[:80]!r}")
    return r.returncode, out


def list_remote_recordings(device: str) -> list[str]:
    rc, out = adb_shell(device, ["ls", "-1", REC_GLOB])
    return [ln.strip() for ln in out.splitlines() if ln.strip()] if rc == 0 else []


def clean_remote(device: str) -> None:
    adb_shell(device, ["rm", "-f", REC_GLOB])


def mp4_signature_ok(path: Path) -> bool:
    try:
        head = path.read_bytes()[:12]
    except OSError:
        return False
    return len(head) >= 8 and head[4:8] == b"ftyp"


def rec_dir() -> Path:
    return Path(settings().recording.dir)


async def wait_state(service: RecordingService, device: str, timeout: float) -> str:
    """轮询会话态离 recording（自然收尾路径），上限 timeout 秒。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = service.get(device)
        if s is not None and s.state != RecordingState.RECORDING.value:
            return s.state
        await asyncio.sleep(2)
    return "timeout"


async def drive(device: str) -> list[dict]:
    results: list[dict] = []
    driver = AdbCliDriver()
    service = RecordingService(adb=driver)
    time_limit = settings().recording.time_limit

    clean_remote(device)

    # ---- S42-1 连环录制（done 覆盖 → 再 start 放行）----
    s1 = await service.start(device)
    await asyncio.sleep(5)
    d1 = await service.stop(device)
    s2 = await service.start(device)  # done 终态被新 start 覆盖 = 前端「重新录制」
    await asyncio.sleep(5)
    d2 = await service.stop(device)
    files = [
        Path(x.local_path) for x in (d1, d2) if x and x.local_path
    ]
    ok = (
        d1 is not None and d1.state == RecordingState.DONE.value
        and d2 is not None and d2.state == RecordingState.DONE.value
        and len(files) == 2 and all(mp4_signature_ok(p) for p in files)
    )
    results.append({
        "id": "S42-1", "device": device, "status": "pass" if ok else "fail",
        "detail": (
            f"两段连环录制 state={d1.state if d1 else None}/{d2.state if d2 else None}，"
            f"产物 {len(files)} 个 ftyp 全验；同 session={s1.id != s2.id}"
        ),
    })

    # ---- S42-2/S42-3 自然录满自动收尾（不 stop，纯监听器路径）----
    sn = await service.start(device)
    log(f"已 start（不点停止，等 {time_limit}s 录满自动收尾）session={sn.id}")
    final = await wait_state(service, device, time_limit + 60)
    s_after = service.get(device)
    auto_done = s_after is not None and s_after.state == RecordingState.DONE.value
    local_ok = bool(
        s_after and s_after.local_path and mp4_signature_ok(Path(s_after.local_path))
        and Path(s_after.local_path).stat().st_size > 0
    )
    residue = list_remote_recordings(device)
    if auto_done and local_ok:
        p = Path(s_after.local_path)
        results.append({
            "id": "S42-2", "device": device, "status": "pass",
            "detail": f"录满自动收尾 done；local={p.name}（{p.stat().st_size}B, ftyp✓）；设备残留={residue or '无'}",
        })
    else:
        results.append({
            "id": "S42-2", "device": device, "status": "fail",
            "detail": f"轮询终值={final}；state={getattr(s_after, 'state', None)}；local_ok={local_ok}",
        })
    try:
        third = await service.start(device)  # 收尾后窗口已消：不再 409
        conflict = False
    except Exception as exc:  # noqa: BLE001
        conflict = type(exc).__name__ == "ConflictError"
        third = None
    if conflict or third is None:
        results.append({
            "id": "S42-3", "device": device, "status": "fail",
            "detail": f"自动收尾后新 start 被拒（409 残留窗口未消除）：{conflict}",
        })
    else:
        await service.stop(device)
        results.append({
            "id": "S42-3", "device": device, "status": "pass",
            "detail": "自动收尾完成后新 start 放行（进行态窗口即时消除，等效离开页面语义）",
        })

    # ---- S42-4 关服清盘（真实目录 + 哨兵）----
    rdir = rec_dir().resolve()
    rdir.mkdir(parents=True, exist_ok=True)
    sentinel = rdir / "spike42_keep.mp4"
    sentinel.write_bytes(b"keep")
    fake1 = rdir / "openscrcpy_rec_spike000001.mp4"
    fake1.write_bytes(b"junk")
    service.cleanup_all_recordings()
    ok4 = fake1.exists() is False and sentinel.exists()
    results.append({
        "id": "S42-4", "device": device, "status": "pass" if ok4 else "fail",
        "detail": f"清盘只中模式：fake 删={not fake1.exists()}，哨兵保留={sentinel.exists()}",
    })
    sentinel.unlink(missing_ok=True)

    # ---- S42-5 启动即扫（新实例 tick 首轮即时清哨兵）----
    stale = rdir / "openscrcpy_rec_spike000002.mp4"
    stale.write_bytes(b"junk")
    fresh_service = RecordingService(adb=AdbCliDriver())  # 模拟重启新实例
    await fresh_service.start_cleanup_task()
    await asyncio.sleep(0.2)
    swept = not stale.exists()
    await fresh_service.stop_cleanup_task()
    results.append({
        "id": "S42-5", "device": device, "status": "pass" if swept else "fail",
        "detail": f"启动即扫：tick 首轮（无 3600s 等待）清掉遗留哨兵={swept}",
    })

    # 用后即清：本 spike 产物（数据保留策略）
    for p in files + [Path(s_after.local_path)] if s_after and s_after.local_path else files:
        try:
            Path(p).unlink(missing_ok=True)
        except OSError:
            pass
    return results


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--devices", nargs="*", default=DEFAULT_DEVICES)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    results: list[dict] = []
    for device in args.devices:
        results += await drive(device)

    log("==== spike 结果汇总 ====")
    for r in results:
        print(f"  [{r['status'].upper():7s}] {r['id']:<6s} {r['detail']}", flush=True)
    fails = [r for r in results if r["status"] == "fail"]
    if args.out:
        payload = {
            "scheme": "42", "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "devices": args.devices, "verdicts": results,
        }
        Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
```

注：脚本不 ruff/mypy 门禁（`tests/manual/` 与 spike 先例同口径不入 CI）；实现中 `try/except Exception` 捕 `app.core.exceptions.ConflictError`——运行时按类名比对以保持脚本零 import 精简（如实现者认为直读更清晰可 `from app.core.exceptions import ConflictError` 并 isinstance，二选一并在 spike 记录注明）。

- [ ] **Step 3: 真机跑 spike**

前置：确认 .25 在线（`adb devices`），不动用户 8765 进程。

```bash
MSYS_NO_PATHCONV=1 PYTHONPATH=backend python -u tests/manual/spike_42_recording_flow.py --out /tmp/s42.json
```

Expected: S42-1..5 全 pass、exit 0。**任一 fail → 回 Phase 1 调研根因（systematic-debugging），禁止改判据硬凑。** 若 .25 err=-38 降档导致 S42-2 首启耗时拉长，轮询上限已含 +60s 余量。

- [ ] **Step 4: 用户手册 §5.4 更新**

`docs/用户手册.md`：将「- 点「录屏」开始，设备端即开始录制屏幕（单次最长 60 秒自动停止），按钮处出现红点与计时」行改为：

```markdown
- 点「录屏」开始，设备端即开始录制屏幕（单次最长 60 秒自动停止并自动拉回录像），按钮处出现红点与计时
```

在「- 部分低端设备不支持高分辨率录屏，会自动降为 256x144 分辨率重试一次（画面较模糊属正常现象）」行之后插入：

```markdown
- 录制中离开设备页无需点停止：录满 60 秒后服务端自动收尾拉回，重进页面即可在下载预览里看到该段录像
- 录像预览面板提供「重新录制」按钮，下载后可直接再录，无需刷新页面
- 录像文件属暂存：优雅退出服务（含界面退出）会清空 `data/recordings/` 目录；强杀/崩溃遗留的文件在下次服务启动时自动清扫，下载入口仅在当次服务运行期间有效
```

- [ ] **Step 5: 方案与路线图文档回写**

1. `方案/42-录屏流程优化与磁盘清理方案.md` 头部状态行「立项完成…待用户 review，实施排期另行指示」改为「已实施（2026-10-13，master 提交 <实施区间>；SDD 执行 + .25 真机验收 <结果>）」；文末追加「## 八、实施记录」：任务→提交对照表、门禁数字（pytest 通过数/覆盖率）、spike 五判据结果摘要、Review Focus 落点、Ruling 记录。
2. `方案/README.md` 方案 42 条目状态注记更新为「已实施」。
3. `方案/进度追踪.md` 追加第七十八次更新条目（日期、方案 42 实施完成、spike 结论、门禁全绿数字）。
4. `方案/后续事项评估与路线图.md`：第 42 项状态改「已实施」，更新记录表追加一行。
5. `docs/经验记录.md` E028 第 6 步 grep 口径补扩展面——在该步命令行之后补一行：

```bash
grep -rn --exclude-dir=node_modules --include="*.sh" --include="*.ts" --include="*.vue" --include="*.py" "5\.0\.1" .
```

并注明（方案 44 终评 Minor-1：升级核对面须含脚本与前端，不只 *.md/*.py）。

提交：

```bash
git add tests/manual/spike_42_recording_flow.py docs/用户手册.md 方案/42-录屏流程优化与磁盘清理方案.md 方案/README.md 方案/进度追踪.md 方案/后续事项评估与路线图.md docs/经验记录.md
git commit -m "test+docs: 方案 42 真机 spike 通过与手册/方案/路线图回写（方案 42 T5）"
```

- [ ] **Step 6: 方案 44 终评卫生项（Minor 1-6 收编，独立小提交）**

逐处按现状文本替换（行号以执行时 Read 复核为准）：

1. `tests/scrcpy/test_scrcpy_minimal.sh`——`exec app_process / com.genymobile.scrcpy.Server "$SCRCPY_VERSION"` 行之前插入：

```sh
: "${SCRCPY_VERSION:?SCRCPY_VERSION must be injected by host}"
```

2. `backend/app/scrcpy/server_manager.py:316` docstring：`版本号字符串（如 "2.4"），如果无法获取返回 None。` → `唯一分发点 scrcpy_version.py 中的版本号字符串，无法获取时返回 None。`
3. `backend/app/scrcpy/stream_protocol.py:2`：`scrcpy-server v4.1 流协议解析（12 字节包头）` → `scrcpy-server 流协议解析（12 字节包头；v4.1~v5.0.1 协议逐字段一致）`
4. `backend/app/scrcpy/control_sender.py:13`：`与 scrcpy-server v4.1 控制协议兼容。` → `与 scrcpy-server 控制协议兼容（v4.1~v5.0.1 一致）。`
5. `backend/app/infrastructure/stream/scrcpy.py:24`：`视频流协议（scrcpy-server v4.1，官方源码 fa57d7c6 逐字段核对）：` → `视频流协议（scrcpy-server v4.1~v5.0.1 协议一致，官方源码 fa57d7c6 逐字段核对）：`
6. `backend/app/scrcpy/constants.py:135` 注释：`（v4.1 使用 scid 格式化）` → `（v4.1 起使用 scid 格式化）`
7. `tests/unit/test_fetch_tools.py` 末尾补换行（Edit 无法表达 EOF 换行，用字节级追加）：

```bash
printf '\n' >> tests/unit/test_fetch_tools.py
```

8. `backend/app/scrcpy/README.md`「## 下一步」标题下一行插入：

```markdown
> 注：本清单为早期历史存档——除「性能优化和延迟测试」（持续进行）外各项均已完成，当前进度以 `方案/进度追踪.md` 为准。
```

验证：

```bash
python -m ruff check backend/app
python -m mypy backend/app
PYTHONPATH=backend:. python -m pytest tests/ --ignore=tests/e2e -q --cov=app --cov-fail-under=80
cd frontend && npx vitest run && npx vue-tsc -b
```

```bash
git add tests/scrcpy/test_scrcpy_minimal.sh backend/app/scrcpy tests/unit/test_fetch_tools.py backend/app/infrastructure/stream/scrcpy.py backend/app/scrcpy/README.md
git commit -m "chore: 方案 44 终评卫生项收尾（版本自称中立化 + 脚本守卫 + 历史清单注记；方案 42 T5）"
```

- [ ] **Step 7: 收口报告**

汇总：四任务提交区间、门禁数字、spike 五判据结果、Ruling 清单。**明确本批全部提交未 push——push 需用户显式授权（不顺延既有授权）**；方案 43 待用户排期指示后另行写计划。
