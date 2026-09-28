# 绿色版退出机制与单开模式（方案 23）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为绿色版补齐用户可达的退出通道（UI 按钮 + stop.bat 兜底）并修复端口探测缺陷 D1（通配绑定共存误判）。

**Architecture:** 停机动作留在启动器层：`app/core/launcher.py` 提供进程级回调注册表，run_server.py（dev 与 frozen 两分支统一）持 uvicorn `Server` 实例并注册 `request_shutdown`；接口层 `POST /api/system/shutdown` 只做薄适配调用注册表。前端顶栏新增「退出服务」按钮（二次确认 + accepted 判定 + health 轮询退出指引）。D1 修复在 `find_available_port` 前加连接预检（`connect_ex`），任何监听者（含 0.0.0.0 通配绑定）都触发端口回退。

**Tech Stack:** Python 3.10 / FastAPI / uvicorn（Server.should_exit 闭包——045ae04 修订，原文误写 request_shutdown）/ Vue 3 + Element Plus / vitest；PyInstaller 6.22.3（随包 stop.bat 经构建脚本复制注入，不碰 spec）。

**Spec:** `方案/23-绿色版退出与单开模式方案.md`（§6 决策已落定：退出机制 A+C、D1 立即排期、仅二次确认）。打包背景见 `方案/15-Windows绿色exe打包方案.md` §9。

## Global Constraints

- 零新依赖：不新增任何 Python/npm 包；发布产物体积变化仅 stop.bat（纯文本，约 200B）。
- 测试文件只放 `tests/`；前端测试与组件同目录（既有 `*.test.ts` 模式，不放入 tests/）。
- 提交信息中文，格式 `feat:/fix:/docs: 中文描述`，并以 `Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>` 结尾；每任务至少一个提交。
- 后端门禁（每个后端测试循环后必跑）：`PYTHONPATH=backend python -m pytest tests/ -v`、`ruff check backend/app`、`python -m mypy backend/app`、`python -m mypy backend/app --platform linux`（Ubuntu CI 交叉检查，方案 15 任务 6 确立的口径）。
- 前端门禁：`cd frontend && npm run test`、`npm run lint`。
- 不改坏 dev 运行流程：run_server.py 非 frozen 分支仅是 `uvicorn.run(...)` → Server 对象 + `run()` 的等价变换（reload=False 语义不变）；**不得重启或干扰当前运行中的 dev 后端**（用户环境 8765 已被其实例占用）。
- 严格四层架构：停机动作经 launcher 注册表，端点不直接触碰 uvicorn/线程。
- 退出端点不加网络鉴权（方案 23 §5.2-T2 评审口径）：绿色版默认绑 127.0.0.1 本机语义；显式 BACKEND_HOST 放开者自担远程退出风险，与方案 §2.1 M2 模式一致。
- 禁止 push（需用户显式授权）。

## Review Focus

1. **无回调注册场景**（docker 直跑 `uvicorn app.main:app`，不走 run_server）：POST `/api/system/shutdown` 必须返回 200 `accepted=false` 而非假成功或 500。→ Task 2 测试 `test_shutdown_refused_without_handler`。
2. **重复退出请求**（用户连点按钮 / 浏览器重试）：第二次 POST 仍 200 `accepted=true`，注册表无状态残留、回调再次触发。→ Task 2 测试 `test_shutdown_idempotent_double_request`。
3. **accepted 响应必须先送达浏览器**（服务须在响应帧写完后才停，前端才能展示「正在退出」而非无从判断的网络错误）：实现依赖 uvicorn `should_exit` 标志语义（本轮请求处理完才收循环）；单测无法模拟完整时序。→ Task 4 复测 R2 验证。
4. **通配绑定共存**（缺陷 D1）：存在 `0.0.0.0:port` 监听者时，试绑 `127.0.0.1:port` 成功也不能判可用，必须回退。→ Task 1 测试 `test_find_available_port_detects_wildcard_listener`。
5. **stop.bat 误击**（服务未运行时双击）：应输出友好提示「未发现运行中的 OpenScrcpy.exe」且退出码为 0，不弹系统错误。→ Task 3 步骤 2 人工验证 + Task 4 R4 复测。

---

### Task 1: 端口探测预检修复缺陷 D1

**Files:**
- Modify: `backend/app/core/launcher.py`（`find_available_port`，当前 52-70 行；新增 `_port_has_listener`）
- Test: `tests/unit/test_launcher.py`（追加 1 个回归用例）

**Interfaces:**
- Consumes: 无（独立任务）。
- Produces: `_port_has_listener(port: int, timeout: float = 0.5) -> bool`；`find_available_port(host: str, start_port: int, attempts: int = 5) -> int` 语义强化：占用判定包含通配绑定监听者（T2 不依赖本函数，T4 复测依赖该语义）。

- [ ] **Step 1: 写失败测试（D1 回归用例）**

在 `tests/unit/test_launcher.py` 的 `test_find_available_port_exhausted_raises` 之后追加：

```python
def test_find_available_port_detects_wildcard_listener():
    """D1 回归（方案 23 §1.2/§3.3）：0.0.0.0 通配绑定占用时，试绑 127.0.0.1
    会成功误判可用；必须连接预检感知通配监听者并回退。"""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("0.0.0.0", 0))
        probe.listen(1)
        occupied = probe.getsockname()[1]
        assert launcher.find_available_port("127.0.0.1", occupied, attempts=2) == occupied + 1
    finally:
        probe.close()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=backend python -m pytest tests/unit/test_launcher.py::test_find_available_port_detects_wildcard_listener -v`
Expected: FAIL——修复前 `find_available_port` 对 `127.0.0.1:occupied` 试绑成功（Windows 允许通配与具体地址共存），返回 `occupied` 而非 `occupied + 1`，`assert` 失败。

- [ ] **Step 3: 实现连接预检**

修改 `backend/app/core/launcher.py`，在 `find_available_port` 之前新增 `_port_has_listener`：

```python
def _port_has_listener(port: int, timeout: float = 0.5) -> bool:
    """连接预检：目标端口存在任何监听者（含 0.0.0.0 通配绑定）即视为占用。

    Windows 允许 0.0.0.0:port 与具体地址:port 共存且连接路由具体地址优先，
    仅试绑具体地址会误判空闲（方案 23 缺陷 D1，§1.2/§3.3）——先连接预检
    再试绑：通配绑定同样接受回环连接，正确触发回退。
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(timeout)
        return probe.connect_ex(("127.0.0.1", port)) == 0
```

同时更新 `find_available_port` 的 docstring 与循环体：

```python
def find_available_port(host: str, start_port: int, attempts: int = 5) -> int:
    """从 start_port 起逐个探测，返回第一个可用端口。

    探测 = 连接预检（感知任何监听者，含通配绑定，§23-D1）+ 试绑复核。
    Windows 下 SO_REUSEADDR 不阻止重复绑定，改用 SO_EXCLUSIVEADDRUSE
    （8.4-6 端口冲突回退的探测正确性依赖此语义）。
    """
    for offset in range(attempts):
        port = start_port + offset
        if _port_has_listener(port):
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.setsockopt(socket.SOL_SOCKET, _SO_EXCLUSIVEADDRUSE, 1)
            except (AttributeError, OSError):
                pass  # 非 Windows 无此选项，默认语义已足够
            try:
                sock.bind((host, port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"no available port from {start_port} to {start_port + attempts - 1}")
```

注意保留 `_SO_EXCLUSIVEADDRUSE` 的 getattr 兜底写法与「非 Windows 值为 0、setsockopt 抛 OSError 被吞」的注释（Ubuntu CI mypy 依赖该模式，方案 15 b5c6194 确立）。

- [ ] **Step 4: 跑测试确认通过**

Run: `PYTHONPATH=backend python -m pytest tests/unit/test_launcher.py -v`
Expected: PASS——新用例通过，且既有 `test_find_available_port_skips_occupied`（127.0.0.1 绑定占用）与 `test_find_available_port_exhausted_raises` 不受影响（预检感知监听者后走回退或耗尽路径，行为不变）。

- [ ] **Step 5: 门禁**

Run:
`PYTHONPATH=backend python -m pytest tests/ -v`、`ruff check backend/app`、`python -m mypy backend/app`、`python -m mypy backend/app --platform linux`
Expected: 全通过（`--platform linux` 验证 `_port_has_listener` 无 Windows 专属门控常量直引）。

- [ ] **Step 6: 提交**

```bash
git add backend/app/core/launcher.py tests/unit/test_launcher.py
git commit -m "$(cat <<'EOF'
fix: 端口探测增加连接预检，修复通配绑定共存误判（方案 23 T1）

Windows 允许 0.0.0.0:8765 与 127.0.0.1:8765 同时 LISTENING（连接路由
具体地址优先），仅试绑具体地址会误判可用，导致 exe 与 dev 同端口
共存、回退失效（缺陷 D1）。探测改为先 connect_ex 预检再试绑，任何
监听者（含通配绑定）都触发回退；回归用例锁定 0.0.0.0 占位场景。

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: 优雅退出全链路（后端端点 + 启动器接入 + 前端按钮）

**Files:**
- Modify: `backend/app/core/launcher.py`（新增 `register_shutdown_handler` / `request_shutdown`；模块 docstring 微调）
- Modify: `backend/app/interfaces/http/system.py`（新增 `POST /shutdown` 端点）
- Modify: `run_server.py`（dev 与 frozen 统一 Server 实例持有 + 注册回调）
- Test: `tests/unit/test_http_system.py`（新建）、`tests/unit/test_launcher.py`（追加 2 用例）
- Modify: `frontend/src/services/api.ts`（新增 `shutdownSystem` / `getHealth`）
- Test: `frontend/src/services/api.test.ts`（追加 2 用例）
- Modify: `frontend/src/App.vue`（顶栏退出按钮 + 二次确认 + 退出指引）
- Test: `frontend/src/App.test.ts`（新建）

**Interfaces:**
- Consumes: Task 1 的 `launcher` 模块（同文件追加函数，无函数级依赖）；后端端点测试模式 `tests/unit/http_testkit.py`（`make_client()` 返回 `TestClient(app, raise_server_exceptions=False)`）；前端测试沿用 vitest + `vi.hoisted` mock 模式（见 `tests/` 外 `frontend/src/**/*.test.ts` 既有风格）。
- Produces: `launcher.register_shutdown_handler(handler: Callable[[], None] | None) -> None`（None 表示反注册）；`launcher.request_shutdown() -> bool`；`POST /api/system/shutdown` 响应 `{"accepted": bool, "message": str}`；`api.shutdownSystem(): Promise<{accepted: boolean; message?: string}>`；`api.getHealth(): Promise<{status: string}>`。

#### 后端部分（提交 1）

- [ ] **Step 1: 写失败测试**

新建 `tests/unit/test_http_system.py`：

```python
"""
系统 HTTP 端点测试（方案 23 T2）
================================

覆盖 POST /api/system/shutdown（优雅停机）：
    - 已注册回调：accepted=true 且回调被触发（should_exit 标志语义，
      响应先于停机送达）
    - 无注册回调（docker 直跑 uvicorn 场景）：accepted=false 不假成功
    - 重复请求幂等：第二次仍 accepted=true 且回调再次触发、无状态残留

回调注册经 launcher 公开 API 往返（不用 monkeypatch 私有变量），
注册表是进程级全局，每用例 try/finally 反注册复位。
"""

from app.core import launcher

from .http_testkit import make_client


def test_shutdown_accepted_when_handler_registered():
    calls: list[bool] = []
    launcher.register_shutdown_handler(lambda: calls.append(True))
    try:
        resp = make_client().post("/api/system/shutdown")
        assert resp.status_code == 200
        assert resp.json() == {"accepted": True, "message": "服务正在退出"}
        assert calls == [True]
    finally:
        launcher.register_shutdown_handler(None)


def test_shutdown_refused_without_handler():
    launcher.register_shutdown_handler(None)
    resp = make_client().post("/api/system/shutdown")
    assert resp.status_code == 200
    assert resp.json() == {
        "accepted": False,
        "message": "当前运行方式未接入在线退出，请手动停止服务",
    }


def test_shutdown_idempotent_double_request():
    calls: list[bool] = []
    launcher.register_shutdown_handler(lambda: calls.append(True))
    try:
        client = make_client()
        for _ in range(2):
            resp = client.post("/api/system/shutdown")
            assert resp.status_code == 200
            assert resp.json()["accepted"] is True
        assert calls == [True, True]
    finally:
        launcher.register_shutdown_handler(None)
```

在 `tests/unit/test_launcher.py` 末尾追加：

```python
def test_shutdown_handler_register_and_request_roundtrip():
    calls: list[bool] = []
    launcher.register_shutdown_handler(lambda: calls.append(True))
    try:
        assert launcher.request_shutdown() is True
        assert launcher.request_shutdown() is True  # 幂等：可重复触发
        assert calls == [True, True]
    finally:
        launcher.register_shutdown_handler(None)


def test_request_shutdown_without_handler_returns_false():
    launcher.register_shutdown_handler(None)
    assert launcher.request_shutdown() is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `PYTHONPATH=backend python -m pytest tests/unit/test_http_system.py tests/unit/test_launcher.py::test_shutdown_handler_register_and_request_roundtrip -v`
Expected: FAIL——`AttributeError: module 'app.core.launcher' has no attribute 'register_shutdown_handler'`；端点 404。

- [ ] **Step 3: 实现 launcher 回调注册表**

修改 `backend/app/core/launcher.py`：

1. 顶部 import 区块新增（`import sys` 之后、`from pathlib import Path` 之前）：

```python
from collections.abc import Callable
```

2. 模块 docstring 的「源码运行（dev）不经过本模块」一句更新为：

```
源码运行（dev）仅使用本模块的退出回调注册表（§23-T2），其余行为不经过
本模块、保持不变。
```

3. 在 `_SO_EXCLUSIVEADDRUSE` 常量之后追加：

```python
_shutdown_handler: Callable[[], None] | None = None


def register_shutdown_handler(handler: Callable[[], None] | None) -> None:
    """登记优雅停机回调（run_server 启动时注册置 uvicorn Server.should_exit 的闭包）。

    进程内单例注册表：重复注册以最后一次为准（dev/frozen 各注册一次，
    互不叠加）；传 None 反注册（测试与 docker 直跑场景的诚实语义依赖此）。
    """
    global _shutdown_handler
    _shutdown_handler = handler


def request_shutdown() -> bool:
    """触发优雅停机；无回调注册（如 docker 直跑 uvicorn）时返回 False。"""
    handler = _shutdown_handler
    if handler is None:
        return False
    handler()
    return True
```

- [ ] **Step 4: 实现端点**

修改 `backend/app/interfaces/http/system.py`，import 区块加 `from app.core.launcher import request_shutdown`，并在文件末尾追加：

```python
@router.post("/shutdown")
async def shutdown_endpoint() -> dict[str, Any]:
    """
    请求优雅停机（方案 23 T2）。

    停机动作经启动器注册表触发（run_server 注册的回调仅置 uvicorn
    Server.should_exit 标志——当前响应完整送达后才停止，浏览器可先拿到
    accepted 再进入退出指引）。

    docker 直跑 uvicorn（无注册回调）时返回 accepted=false 不假成功。

    返回：
        {"accepted": true, "message": "服务正在退出"}  已接入在线退出
        {"accepted": false, "message": "..."}          未接入，需手动停止
    """
    accepted = request_shutdown()
    return {
        "accepted": accepted,
        "message": "服务正在退出" if accepted else "当前运行方式未接入在线退出，请手动停止服务",
    }
```

- [ ] **Step 5: 改 run_server.py 统一持有 Server 实例**

修改 `run_server.py`（注意：**不在模块顶层新增 import**——frozen 下必须先接管标准流再加载任何 app 子模块，该顺序是既有 log 重定向正确性的根基，`_serve` 采用函数内局部 import 保持）：

1. `main()` 之前新增模块级 `_serve` 函数：

```python
def _serve(server_app, host: str, port: int) -> None:
    """以可被在线端点优雅停止的方式跑 uvicorn（dev 与 frozen 共用）。

    uvicorn.run 内部即 Config + Server.run，此处显式持有 Server 实例并
    注册停机回调（闭包置 should_exit 标志，当前响应写完后停机，
    同时触发 FastAPI lifespan 关闭清理）。导入放函数内：frozen 下必须先
    接管标准流再加载任何 app 子模块（见 main 开头注释），不破坏该顺序。
    """
    import uvicorn

    from app.core import launcher

    server = uvicorn.Server(uvicorn.Config(server_app, host=host, port=port, reload=False))

    def _trigger_shutdown() -> None:
        # uvicorn 无 request_* 公开停机方法；should_exit 是主循环轮询的优雅停机
        # 标志（信号处理 handle_exit 亦置此属性，方案 23 §5.2-T2 原文语义）。
        server.should_exit = True

    launcher.register_shutdown_handler(_trigger_shutdown)
    server.run()
```

2. `main()` 中非 frozen 分支（当前 47-50 行）替换为：

```python
    if not frozen:
        # 开发模式：行为与原版一致（reload=False），仅改为显式持有
        # Server 实例并注册退出回调，支持 UI 退出按钮优雅停机（方案 23 T2）
        _serve(app, srv.host, srv.port)
        return 0
```

3. frozen 分支的线程 target（当前 70-74 行）替换为：

```python
    server_thread = threading.Thread(
        target=_serve, args=(app, host, port),
        daemon=True,
    )
```

- [ ] **Step 6: 跑测试确认通过**

Run: `PYTHONPATH=backend python -m pytest tests/unit/test_http_system.py tests/unit/test_launcher.py -v`
Expected: PASS——全部 5 个新用例通过；既有 launcher 用例不受影响。

- [ ] **Step 7: 后端门禁**

Run:
`PYTHONPATH=backend python -m pytest tests/ -v`、`ruff check backend/app`、`python -m mypy backend/app`、`python -m mypy backend/app --platform linux`
Expected: 全通过（`Callable | None` 与 `global` 声明模式是严格模式安全写法；端点返回 `dict[str, Any]` 与既有 `reload_config_endpoint` 风格一致）。

- [ ] **Step 8: 提交后端部分**

```bash
git add backend/app/core/launcher.py backend/app/interfaces/http/system.py run_server.py tests/unit/test_http_system.py tests/unit/test_launcher.py
git commit -m "$(cat <<'EOF'
feat: 新增 /api/system/shutdown 优雅停机端点（方案 23 T2 后端）

停机动作留在启动器层：launcher 提供进程级回调注册表，
run_server（dev 与 frozen 统一）持有 uvicorn Server 实例并注册
停机闭包（should_exit 标志——响应完整送达后停机，触发
lifespan 清理）；接口层仅薄适配。docker 直跑 uvicorn 无注册回调时
返回 accepted=false 不假成功；重复请求幂等。

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

#### 前端部分（提交 2）

- [ ] **Step 9: 写失败测试**

新建 `frontend/src/App.test.ts`：

```ts
/**
 * App 根组件测试（方案 23 T2）
 * ============================
 *
 * 覆盖顶栏「退出服务」按钮：
 *   - 渲染存在
 *   - 确认后 accepted=true → 「服务正在退出」提示并开始 health 轮询
 *   - 确认后 accepted=false → 警告提示，不进入轮询
 *   - 取消确认框 → 不调用 api
 *   - 轮询中 health 失败 → 「服务已退出」提示且定时器停止
 *
 * element-plus 采用 partial mock：组件（el-button 等）与插件保持真实
 * （mount 时 global.plugins 注册），仅函数式 API（ElMessage/ElMessageBox）
 * 替换为 spy。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import App from './App.vue'

const h = vi.hoisted(() => ({
  ElMessage: { info: vi.fn(), success: vi.fn(), warning: vi.fn(), error: vi.fn() },
  ElMessageBox: { confirm: vi.fn() },
}))

vi.mock('element-plus', async (importOriginal) => {
  const actual = await importOriginal<typeof import('element-plus')>()
  return { ...actual, ElMessage: h.ElMessage, ElMessageBox: h.ElMessageBox }
})

const mockApi = vi.hoisted(() => ({
  shutdownSystem: vi.fn(),
  getHealth: vi.fn(),
}))
vi.mock('@/services/api', () => ({ api: mockApi }))

function mountApp() {
  return mount(App, {
    global: {
      plugins: [ElementPlus],
      stubs: {
        'router-view': { template: '<div class="view-stub" />' },
        CommandPalette: { template: '<div class="palette-stub" />' },
      },
    },
  })
}

afterEach(() => {
  vi.useRealTimers()
})

describe('App 退出服务按钮', () => {
  it('顶栏渲染退出按钮', () => {
    vi.clearAllMocks()
    const wrapper = mountApp()
    expect(wrapper.find('[data-test="exit-btn"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('确认后 accepted=true：显示退出中提示并轮询 health，按钮进入 loading', async () => {
    vi.clearAllMocks()
    h.ElMessageBox.confirm.mockResolvedValue('confirm')
    mockApi.shutdownSystem.mockResolvedValue({ accepted: true, message: '服务正在退出' })
    mockApi.getHealth.mockResolvedValue({ status: 'ok' })

    const wrapper = mountApp()
    await wrapper.find('[data-test="exit-btn"]').trigger('click')

    expect(h.ElMessageBox.confirm).toHaveBeenCalled()
    expect(mockApi.shutdownSystem).toHaveBeenCalledOnce()
    expect(h.ElMessage.info).toHaveBeenCalledWith('服务正在退出…')
    expect(mockApi.getHealth).toHaveBeenCalled()
    wrapper.unmount()
  })

  it('accepted=false：警告提示且不进入轮询', async () => {
    vi.clearAllMocks()
    h.ElMessageBox.confirm.mockResolvedValue('confirm')
    mockApi.shutdownSystem.mockResolvedValue({ accepted: false, message: '未接入' })

    const wrapper = mountApp()
    await wrapper.find('[data-test="exit-btn"]').trigger('click')

    expect(h.ElMessage.warning).toHaveBeenCalledWith(
      '当前运行方式不支持在线退出，请手动停止服务'
    )
    expect(mockApi.getHealth).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('取消确认框不调用 api', async () => {
    vi.clearAllMocks()
    h.ElMessageBox.confirm.mockRejectedValue('cancel')

    const wrapper = mountApp()
    await wrapper.find('[data-test="exit-btn"]').trigger('click')

    expect(mockApi.shutdownSystem).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('health 轮询失败后提示已退出并停止轮询', async () => {
    vi.clearAllMocks()
    vi.useFakeTimers()
    h.ElMessageBox.confirm.mockResolvedValue('confirm')
    mockApi.shutdownSystem.mockResolvedValue({ accepted: true })
    mockApi.getHealth.mockRejectedValue(new Error('conn refused'))

    const wrapper = mountApp()
    await wrapper.find('[data-test="exit-btn"]').trigger('click')
    await vi.advanceTimersByTimeAsync(500)

    expect(h.ElMessage.success).toHaveBeenCalledWith('服务已退出，请关闭此页面')
    const calls = mockApi.getHealth.mock.calls.length
    await vi.advanceTimersByTimeAsync(3000)
    expect(mockApi.getHealth.mock.calls.length).toBe(calls)  // 定时器已清除
    wrapper.unmount()
  })
})
```

在 `frontend/src/services/api.test.ts` 末尾追加（复用文件头部的 `mockClient` 结构）：

```ts
describe('系统 API（方案 23 T2）', () => {
  it('shutdownSystem 请求 POST /system/shutdown 并透传结果', async () => {
    mockClient.post.mockResolvedValue({ data: { accepted: true, message: '服务正在退出' } })
    const result = await api.shutdownSystem()
    expect(mockClient.post).toHaveBeenCalledWith('/system/shutdown')
    expect(result.accepted).toBe(true)
  })

  it('getHealth 以空 baseURL 越过 /api 前缀请求 /health', async () => {
    mockClient.get.mockResolvedValue({ data: { status: 'ok' } })
    const result = await api.getHealth()
    expect(mockClient.get).toHaveBeenCalledWith('/health', { baseURL: '', timeout: 2000 })
    expect(result.status).toBe('ok')
  })
})
```

- [ ] **Step 10: 跑测试确认失败**

Run: `cd frontend && npx vitest run src/App.test.ts src/services/api.test.ts`
Expected: FAIL——`App.test.ts` 找不到退出按钮（`[data-test="exit-btn"]` 不存在）；`api.test.ts` 报 `api.shutdownSystem is not a function`。

- [ ] **Step 11: 实现 api.ts 新方法**

修改 `frontend/src/services/api.ts`，在设备 API 分组之后追加：

```ts
  // ==================== 系统 API ====================

  /**
   * 请求服务优雅退出。
   * 对应：POST /api/system/shutdown
   */
  async shutdownSystem() {
    const res = await client.post('/system/shutdown')
    return res.data as { accepted: boolean; message?: string }
  },

  /**
   * 服务健康检查（退出确认轮询用）。
   * 对应：GET /health（位于 /api 前缀之外，需空 baseURL 越过）
   */
  async getHealth() {
    const res = await client.get('/health', { baseURL: '', timeout: 2000 })
    return res.data as { status: string }
  },
```

注意插入位置在对象字面量内部（`client` 实例定义之后、`export const api` 内），保持与既有方法同级的逗号与缩进风格。

- [ ] **Step 12: 实现 App.vue 退出按钮与退出指引**

修改 `frontend/src/App.vue`：

1. template（`el-menu` 之后、`header-content` 收尾之前插入按钮）：

```vue
          <el-button
            text
            class="exit-btn"
            data-test="exit-btn"
            :loading="exiting"
            @click="onExitClick"
          >
            退出服务
          </el-button>
```

2. script 替换为：

```ts
import { onBeforeUnmount, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import CommandPalette from '@/components/ui/CommandPalette.vue'
import { api } from '@/services/api'

const exiting = ref(false)
let exitPollTimer: ReturnType<typeof setInterval> | null = null
const EXIT_POLL_LIMIT = 60 // 500ms × 60 = 30s 上限

async function onExitClick() {
  try {
    await ElMessageBox.confirm(
      '确定要退出 OpenScrcpy 服务吗？退出后将释放端口，需重新启动程序才能继续使用。',
      '退出服务',
      { confirmButtonText: '退出', cancelButtonText: '取消', type: 'warning' }
    )
  } catch {
    return // 用户取消
  }
  try {
    const result = await api.shutdownSystem()
    if (!result.accepted) {
      ElMessage.warning('当前运行方式不支持在线退出，请手动停止服务')
      return
    }
    exiting.value = true
    ElMessage.info('服务正在退出…')
    startExitPolling()
  } catch {
    // 请求未送达即已断连：服务先于响应退出（如 stop.bat 抢先），视为已退出
    ElMessage.success('服务已退出，请关闭此页面')
  }
}

function startExitPolling() {
  let ticks = 0
  exitPollTimer = setInterval(async () => {
    ticks += 1
    try {
      await api.getHealth()
    } catch {
      stopExitPolling()
      ElMessage.success('服务已退出，请关闭此页面')
      return
    }
    if (ticks >= EXIT_POLL_LIMIT) {
      stopExitPolling()
      ElMessage.warning('服务仍在运行，请稍后再试或使用任务管理器结束 OpenScrcpy.exe')
    }
  }, 500)
}

function stopExitPolling() {
  if (exitPollTimer) {
    clearInterval(exitPollTimer)
    exitPollTimer = null
  }
}

onBeforeUnmount(stopExitPolling)
```

3. style 补一行（按钮靠右）：

```css
.exit-btn {
  margin-left: auto;
}
```

- [ ] **Step 13: 跑测试确认通过**

Run: `cd frontend && npx vitest run src/App.test.ts src/services/api.test.ts`
Expected: PASS——App 5 用例与 api 2 用例全过；若个别用例因 el-button 事件为异步链需 `await flushPromises`，仅允许在测试内补 `await new Promise((r) => setTimeout(r))` 式等待（组件实现不得为此改动）。

- [ ] **Step 14: 前端门禁**

Run: `cd frontend && npm run test`、`npm run lint`
Expected: 全通过（无既有用例回归；App.vue 总行数 < 300，不触发拆分约束；eslint 无新告警）。

- [ ] **Step 15: 提交前端部分**

```bash
git add frontend/src/App.vue frontend/src/App.test.ts frontend/src/services/api.ts frontend/src/services/api.test.ts
git commit -m "$(cat <<'EOF'
feat: 顶栏新增退出服务按钮与退出指引（方案 23 T2 前端）

二次确认后 POST /api/system/shutdown；accepted=true 进入退出中
提示并健康轮询直至确认退出，accepted=false（docker 直跑）提示手动
停止。关闭浏览器不等于关闭服务——退出通道由本按钮、stop.bat 与
任务管理器三路径构成（发布文档见 T3）。

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: stop.bat 随包注入与发布文档「退出服务」章节

**Files:**
- Create: `scripts/stop.bat`
- Modify: `scripts/build_exe.sh`（[5/6] 步骤注入 stop.bat 并转 CRLF）
- Modify: `docs/Windows-exe-发布说明.md`（新增「退出服务」章节）
- （条件）Modify: `方案/15-Windows绿色exe打包方案.md`（§8.6 观察项同步，若命中）

**Interfaces:**
- Consumes: 无（独立任务；产物名 `OpenScrcpy.exe` 与既有 spec 的 EXE name 一致）。
- Produces: `dist/OpenScrcpy/stop.bat`（构建时注入、与 exe 同级）；发布说明「退出服务」章节（T4 复测对照）。

> 实施口径说明：方案 23 T3 原文「spec datas 注入 exe 同级」改为**构建脚本 [5/6] 复制注入**——PyInstaller 6 onedir 的 datas 目标路径相对 `_internal`，无法落到 exe 同级；而 [5/6] 已是 zip 打包关口，复制在此最可靠且不触碰 spec 体积逻辑。偏离原因与结论随提交信息可见。

- [ ] **Step 1: 创建 scripts/stop.bat**

```bat
@echo off
taskkill /IM OpenScrcpy.exe /F >nul 2>&1
if %errorlevel%==0 (
    echo OpenScrcpy 服务已停止
) else (
    echo 未发现运行中的 OpenScrcpy.exe
)
pause
```

说明：`/F` 强制终止——窗口消息（WM_CLOSE）对 `--noconsole` 无窗口进程无效，是唯一可靠方式；`taskkill` 只命中 `OpenScrcpy.exe` 不影响无关进程；`pause` 面向双击用户，自动化验证时用管道喂回车。

- [ ] **Step 2: 人工验证 bat 两个分支**

服务未运行场景（当前环境 dev 后端是 python.exe，非 OpenScrcpy.exe，安全）：
Run: `printf '\r\n' | cmd //c "scripts\\stop.bat"`
Expected: 输出「未发现运行中的 OpenScrcpy.exe」，退出码 0（Review Focus #5）。

- [ ] **Step 3: 修改构建脚本注入 stop.bat**

修改 `scripts/build_exe.sh` 的 [5/6] 段落（当前 33-35 行）：

```bash
echo "==> [5/6] 清理运行时数据、注入 stop.bat 并打包 zip（§9.5：zip 不得携带 data/）"
rm -rf dist/OpenScrcpy/data
cp scripts/stop.bat dist/OpenScrcpy/stop.bat
unix2dos dist/OpenScrcpy/stop.bat  # bat 需 CRLF 行尾（LF 下多行块解析有兼容坑）
python -m zipfile -c "dist/OpenScrcpy-win64-$VERSION.zip" dist/OpenScrcpy
```

- [ ] **Step 4: 发布文档新增「退出服务」章节**

修改 `docs/Windows-exe-发布说明.md`，在「端口与单实例」章节之后插入：

```markdown
## 退出服务

**关闭浏览器不等于关闭服务**：服务继续在后台占用端口并轮询设备。退出方式三选一：

1. **界面按钮（推荐）**：右上角「退出服务」→ 确认 → 服务优雅停止（日志落盘、
   端口释放）。未接入在线退出的运行方式（如 docker 直接部署）会明确提示，请改用方式 2/3
2. **stop.bat**：双击 exe 同级的 `stop.bat` 结束服务进程
3. **任务管理器**：结束 `OpenScrcpy.exe` 进程（兜底，不做优雅清理）

修改 port.txt 等运行痕迹不影响下次启动：再次双击 `OpenScrcpy.exe` 即可。
```

- [ ] **Step 5: 方案 15 §8.6 观察项同步**

Run: `grep -n "退出\|托盘\|taskkill\|任务管理器" "方案/15-Windows绿色exe打包方案.md" | head -20`
- 若命中退出机制相关观察项：将对应条目更新为「已由方案 23 覆盖（本提交）」。
- 若无命中：跳过修改，在提交信息中不提及。

- [ ] **Step 6: 提交**

```bash
git add scripts/stop.bat scripts/build_exe.sh docs/Windows-exe-发布说明.md
git commit -m "$(cat <<'EOF'
feat: 随包注入 stop.bat 并补充发布说明退出服务章节（方案 23 T3）

stop.bat 经构建脚本复制到 exe 同级（CRLF 转行尾），覆盖 UI 按钮
之外的兜底退出路径；发布说明新增「退出服务」章节（界面按钮 /
stop.bat / 任务管理器三路径 + 关闭浏览器不等同退出的说明）。

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

（若 Step 5 同步了方案 15，把 `方案/15-Windows绿色exe打包方案.md` 一并加入 `git add`。）

---

### Task 4: 打包重建与干净环境复测（D1 双跑 / 退出释放 / 二启 / stop.bat）

**Files:**
- Modify: `方案/23-绿色版退出与单开模式方案.md`（状态行回写复测结论）
- （条件）Modify: `方案/15-Windows绿色exe打包方案.md`（§9.6 复测结论，若 Task 3 已同步观察项）

**Interfaces:**
- Consumes: Task 1 的端口预检语义、Task 2 的退出端点与前端按钮、Task 3 的 stop.bat 注入（全部经 `scripts/build_exe.sh` 产物体现）。
- Produces: 复测结论（回写方案 23 状态行）；无代码接口变更。

复测矩阵（方案 23 §5.2-T4）：R1 D1 双跑回退、R2 UI 退出端口释放、R3 二启单开不回退、R4 stop.bat、R5 日志审计。所有命令在仓库根（Git Bash）执行；启动 exe 用 `./dist/OpenScrcpy/OpenScrcpy.exe &` 后台化，就绪判定轮询 `/health`。

- [ ] **Step 1: 完整构建**

Run: `bash scripts/build_exe.sh`
Expected: 构建成功，输出 `构建完成：dist/OpenScrcpy-win64-0.1.0.zip`（耗时数分钟，含 npm ci/build 与干净 venv；如超时按 10 分钟上限重跑一次）。记录 SHA256。

- [ ] **Step 2: 产物静态断言**

Run:
```bash
python - <<'EOF'
import zipfile
names = zipfile.ZipFile("dist/OpenScrcpy-win64-0.1.0.zip").namelist()
assert "OpenScrcpy/stop.bat" in names, "zip 缺 stop.bat"
assert not any("data/" in n for n in names), "zip 携带 data/"
assert any(n == "OpenScrcpy/OpenScrcpy.exe" for n in names)
print("zip OK:", f"{len(names)} files, stop.bat 就位")
EOF
sha256sum dist/OpenScrcpy-win64-0.1.0.zip
```
Expected: `zip OK`；SHA256 与 Step 1 日志一致。

- [ ] **Step 3: 复测前置**

Run:
```bash
netstat -ano | grep -E ":(8765|8766)\b.*LISTENING" || echo "8765/8766 空闲"
rm -rf dist/OpenScrcpy/data dist/OpenScrcpy/logs
```
Expected: 若 8765 已有通配监听（如用户 dev 后端在跑）记录 PID 留作 R1 占用者；若空闲则 R1 需自起 mock 监听。

- [ ] **Step 4: R1 D1 双跑回退**

若 Step 3 显示 8765 空闲，先起通配占位（模拟 dev 服务）：

```bash
python -c "import socket,time; s=socket.socket(); s.bind(('0.0.0.0',8765)); s.listen(1); print('mock listener up', flush=True); time.sleep(600)" &
MOCK_PID=$!
sleep 1
```

然后启动 exe 并等待就绪：

```bash
./dist/OpenScrcpy/OpenScrcpy.exe &
sleep 8
for i in $(seq 1 30); do
  curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8766/health | grep -q 200 && break
  sleep 0.5
done
cat dist/OpenScrcpy/data/port.txt
netstat -ano | grep ":876" | grep LISTENING
```

Expected（D1 修复生效）：`port.txt` = `8766`（8765 被通配监听占用，exe 必须回退）；netstat 同时可见 8765（mock/dev）与 8766（exe）两个不同端口的 LISTENING，**不再是同端口共存**。浏览器会弹开指向 8766（复测环境接受）。

- [ ] **Step 5: R2 UI 退出端口释放**

Run:
```bash
echo "--- 请求退出 ---"
curl -s -X POST http://127.0.0.1:8766/api/system/shutdown
echo
echo "--- 等待停机 ---"
for i in $(seq 1 20); do
  if ! curl -s -o /dev/null http://127.0.0.1:8766/health; then echo "health 已断，服务已退出"; break; fi
  sleep 0.5
done
tasklist //FI "IMAGENAME eq OpenScrcpy.exe" | grep -i openscrcpy || echo "无 OpenScrcpy 进程"
netstat -ano | grep ":8766" | grep LISTENING || echo "8766 已释放"
```

Expected（Review Focus #3）：shutdown 响应体 `{"accepted":true,...}`（浏览器能先拿到响应再断连）；随后 health 不可达、进程消失、8766 无 LISTENING。

- [ ] **Step 6: R3 二启单开**

Run:
```bash
./dist/OpenScrcpy/OpenScrcpy.exe &
sleep 8
PORT=$(cat dist/OpenScrcpy/data/port.txt)
echo "实际端口: $PORT"
for i in $(seq 1 30); do
  curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:$PORT/health" | grep -q 200 && break
  sleep 0.5
done
echo "--- 第二次启动（应秒退） ---"
./dist/OpenScrcpy/OpenScrcpy.exe
sleep 3
echo "--- 进程数断言 ---"
tasklist //FI "IMAGENAME eq OpenScrcpy.exe" | grep -ci openscrcpy
cat dist/OpenScrcpy/data/port.txt
```

Expected: 首次启动的实际端口以 `port.txt` 为准（若 8765 仍被别的监听者占用——包括用户 dev 后端——则回退到 8766，二启单开断言**不依赖具体端口号**）；二次启动进程自动退出（`exit 0`）、仅开浏览器指向已运行实例；进程计数 = 1；`port.txt` 与首启一致（未回退重写）。

- [ ] **Step 7: R4 stop.bat**

Run:
```bash
printf '\r\n' | cmd //c "dist\\OpenScrcpy\\stop.bat"
sleep 2
tasklist //FI "IMAGENAME eq OpenScrcpy.exe" | grep -i openscrcpy || echo "无 OpenScrcpy 进程"
netstat -ano | grep ":8765" | grep LISTENING || echo "8765 已释放"
```

Expected: 输出「OpenScrcpy 服务已停止”；进程消失、8765 释放（Review Focus #5 的反向分支——服务在跑时正确命中）。

- [ ] **Step 8: R5 日志与残留审计**

Run:
```bash
grep -icE "error|traceback|exception" dist/OpenScrcpy/logs/openscrcpy.log || echo "0 error"
grep -c "Application shutdown\|application_stopped" dist/OpenScrcpy/logs/openscrcpy.log || true
rm -rf dist/OpenScrcpy/data dist/OpenScrcpy/logs
```

Expected: 错误计数 0（`server_ready_timeout` 等既有 warning 不算 error，允许出现一行 `Error while listing devices`？不允许——若出现需停下排查，不得吞掉）；lifespan 关闭日志存在（优雅停机路径走通）。

- [ ] **Step 9: 回写方案 23 与提交**

修改 `方案/23-绿色版退出与单开模式方案.md` 状态行，在既有内容后追加：

```markdown
> **状态（更新 2026-09-28）**：T1~T4 实施完成并打包复测通过——D1 双跑回退
> 8766 实证（通配监听共存场景）、UI 退出响应先达且端口释放、二启单开
> 进程数=1、stop.bat 命中与未命中双分支、R5 日志零 error；发布说明已含
> 「退出服务」章节。待用户真机手感确认（退出按钮交互）。
```

然后：

```bash
git add 方案/23-绿色版退出与单开模式方案.md
git commit -m "$(cat <<'EOF'
docs: 回写方案 23 实施复测结论（T4 打包回归通过）

D1 双跑回退 / UI 退出释放 / 二启单开 / stop.bat 双分支 / 日志零
error 五项复测全部通过；发布卷 zip 静态断言 stop.bat 就位且不含
data/。

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 10: 现场清理**

Run:
```bash
kill $MOCK_PID 2>/dev/null || true
tasklist //FI "IMAGENAME eq OpenScrcpy.exe" | grep -i openscrcpy || echo "现场无残留进程"
```
Expected: mock 监听（若 Step 4 起过）已杀；无 OpenScrcpy.exe 残留。用户 dev 后端（8765 通配监听）全程未触碰。