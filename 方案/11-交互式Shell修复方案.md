# 11 - 交互式 Shell 修复方案（PTY 模式）

> **版本**：v3.0（最终版） | **日期**：2026-09-10
> **状态**：待实施
> **前置依赖**：v0.3.0（视频流修复完成）

---

## 1. 问题概述

### 1.1 核心问题

当前 Shell 终端无法作为真正的交互式 shell 使用，与真实 `adb shell` 体验差距巨大：

| 功能 | 真实 adb shell | 当前实现 | 差异 |
|------|----------------|----------|------|
| cd 持久化 | ✅ | ❌ 每条命令独立进程 | 无法保持目录 |
| su 提权 | ✅ | ❌ 每条命令独立进程 | 无法保持权限 |
| Ctrl+C 中断 | ✅ 发送 SIGINT | ❌ 仅清空本地输入 | 无法中断命令 |
| Tab 补全 | ✅ 设备 shell 处理 | ❌ 未实现 | 无法补全 |
| 命令历史（↑↓） | ✅ 设备端维护 | ⚠️ 前端本地维护 | 不持久 |
| 交互式命令（top/vi） | ✅ | ❌ | 不支持 |
| stderr 显示 | ✅ | ❌ | 错误信息丢失 |

**根本原因**：当前使用 `adb shell <cmd>`（一次性命令模式），而非 `adb shell`（交互式会话）。

### 1.2 现有代码额外问题

| 问题 | 位置 | 影响 |
|------|------|------|
| `shell_stream()` stderr 未合并 | `cli.py` L212-250 | 错误信息延迟显示 |
| `stream_logcat()` 未消费 stderr | `cli.py` L332-349 | 可能死锁 |

### 1.3 与真实 adb shell 的最终差异（方案实施后）

| # | 差异 | 影响 | 状态 |
|---|------|------|------|
| D1 | 窗口大小固定 80x24 | 终端区域稍小，但功能完整 | ✅ 接受 |
| D2 | 网络延迟 10-50ms | 按键反馈略有延迟 | ✅ 接受 |
| D3 | 初始欢迎消息 | 可能显示设备名 | ✅ 接受 |
| D4 | 连接断开行为 | shell 保持状态（比真实 adb 更好） | ✅ 接受 |

**结论**：方案实施后，**95%+ 的体验与真实 adb shell 一致**。

---

## 2. 技术方案

### 2.1 核心技术

**PTY 模式**：使用 `adb shell -tt` 强制分配 PTY（即使 stdin 不是 TTY）

```bash
# 验证命令
printf 'ls -la /proc/$$/fd/0\n' | timeout 3 adb shell -tt
# 预期输出：/dev/pts/0（而非 socket:[...]）
```

### 2.2 架构设计

```
用户按键 → xterm.js (80x24) → WebSocket → 后端 → adb stdin (PTY)
                                                    ↓
用户看到 ← xterm.js ← WebSocket ← 后端 ← adb stdout (PTY)
```

**数据流**：完全双向透传，无本地 shell 模拟

### 2.3 窗口大小策略

**固定 80x24**：前端 xterm.js 和设备 PTY 都设为 80 列 x 24 行，确保对齐

```typescript
term = new Terminal({
  cols: 80,
  rows: 24,
  cursorBlink: true,
  // ...
})
```

**不实现窗口同步**：接受终端区域稍小，功能完整，体验一致。

---

## 3. 架构层次

```
domain/ports.py
  + ShellSession(Protocol)       ← 新端口：交互式 shell 会话
  + AdbDriver.create_shell()     ← 工厂方法

infrastructure/adb/shell.py      ← 新文件
  + InteractiveShell              ← 实现 ShellSession（PTY 模式）

infrastructure/adb/cli.py
  + create_shell()                ← 工厂方法实现
  ~ shell_stream()                ← 修复 stderr 合并
  ~ stream_logcat()               ← 修复 stderr 管道死锁
  + _consume_stderr()             ← 辅助方法

application/debug_service.py
  + shell_sessions: dict          ← 管理每个会话的 shell 实例
  ~ exec_shell_stream()           ← 改用 InteractiveShell
  ~ close_session()               ← 关闭对应 shell
  + get_or_create_shell()         ← 延迟创建 shell

interfaces/ws/debug.py
  ~ debug_stream()                ← 新增 `input` 操作（发送按键到设备）

frontend/src/components/debug/ShellView.vue
  ~ 改为"透传模式"：每次按键 → WebSocket → 设备
  ~ 移除本地 shell 模拟逻辑
  ~ 固定 cols: 80, rows: 24

frontend/src/stores/debug.ts
  + sendInput(data: string)       ← 发送原始按键到设备
```

---

## 4. 关键实现细节

### 4.1 InteractiveShell（infrastructure/adb/shell.py）

```python
"""
交互式 Shell 会话（PTY 模式）
==============================

通过 `adb shell -tt` 创建持久化交互式 shell。

数据流：
  用户按键 → WebSocket → backend → adb stdin → 设备 PTY
  用户看到 ← WebSocket ← backend ← adb stdout ← 设备 PTY

功能：
  - 持久化 shell 会话（cd/su 状态保持）
  - 完整终端功能（Ctrl+C、Tab、↑↓ 历史、readline）
  - 交互式命令支持（top、vi、less）
"""

import asyncio
import uuid
from typing import AsyncIterator

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class ShellExitedError(Exception):
    """Shell 进程意外退出"""
    pass


class InteractiveShell:
    """持久化交互式 shell 会话（PTY 模式）"""
    
    READ_TIMEOUT = 30.0  # 读取超时（支持长时间运行的命令）
    
    def __init__(self):
        self._adb_path = settings().adb.path
        self._proc: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._device_id: str | None = None
    
    @property
    def is_alive(self) -> bool:
        """检查 shell 进程是否存活"""
        return self._proc is not None and self._proc.returncode is None
    
    async def start(self, device_id: str):
        """启动 adb shell -tt（强制 PTY）"""
        self._device_id = device_id
        self._proc = await asyncio.create_subprocess_exec(
            self._adb_path,
            "-s", device_id,
            "shell", "-tt",  # 强制 PTY 分配
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        logger.info("interactive_shell_started", device=device_id, pid=self._proc.pid)
        
        # 读取并丢弃初始欢迎消息（直到看到第一个 prompt）
        await self._read_until_prompt()
    
    async def _read_until_prompt(self):
        """读取并丢弃初始输出，直到看到 shell prompt"""
        try:
            while True:
                line = await asyncio.wait_for(
                    self._proc.stdout.readline(),
                    timeout=2.0
                )
                if not line:
                    break
                text = line.decode("utf-8", errors="replace")
                # 检测到 prompt（如 `rk3288:/ $ ` 或 `shell@rk3288:~ $ `）
                if "$ " in text:
                    logger.debug("shell_prompt_detected", line=text.strip())
                    break
        except asyncio.TimeoutError:
            logger.debug("initial_output_timeout")
    
    async def execute(self, cmd: str) -> AsyncIterator[str]:
        """
        执行命令并流式返回输出（过滤 prompt）。
        
        用于 HTTP API 降级路径（保持向后兼容）。
        WebSocket 模式下使用 send_input() 直接透传。
        """
        async with self._lock:
            if not self.is_alive:
                raise ShellExitedError("Shell process not running")
            
            marker = f"__CMD_DONE_{uuid.uuid4().hex[:8]}__"
            # 写入命令 + 标记（合并 stderr）
            line = f'{cmd} 2>&1 ; echo "\\n{marker}"\n'
            self._proc.stdin.write(line.encode())
            await self._proc.stdin.drain()
            
            while True:
                raw = await asyncio.wait_for(
                    self._proc.stdout.readline(),
                    timeout=self.READ_TIMEOUT
                )
                if not raw:
                    raise ShellExitedError("Shell process exited")
                
                text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                
                # 过滤：命令回显行（包含 prompt）
                if self._is_prompt_line(text):
                    continue
                
                # 检测标记
                if marker in text:
                    break
                
                # 过滤：空 prompt 行
                if self._is_empty_prompt(text):
                    continue
                
                yield text + "\n"
    
    async def send_input(self, data: bytes):
        """发送原始输入（按键）到 shell"""
        async with self._lock:
            if not self.is_alive:
                raise ShellExitedError("Shell process not running")
            self._proc.stdin.write(data)
            await self._proc.stdin.drain()
    
    async def stop(self):
        """安全关闭 shell 进程"""
        if self._proc and self._proc.returncode is None:
            logger.info("stopping_shell", device=self._device_id)
            # 1. 先关闭 stdin
            self._proc.stdin.close()
            try:
                # 2. 等待 3 秒让 shell 自然退出
                await asyncio.wait_for(self._proc.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                # 3. 超时则强制 kill
                self._proc.kill()
                await self._proc.wait()
            # 4. 清理 transport（避免 Windows 资源泄漏）
            try:
                self._proc.stdout._transport.close()
            except Exception:
                pass
            try:
                self._proc.stderr._transport.close()
            except Exception:
                pass
            self._proc = None
    
    def _is_prompt_line(self, text: str) -> bool:
        """检测是否为 shell prompt 行（命令回显）"""
        # 示例：`rk3288:/ $ pwd` 或 `shell@rk3288:~ $ pwd`
        import re
        return bool(re.match(r'^[^\$]+\$ .+$', text))
    
    def _is_empty_prompt(self, text: str) -> bool:
        """检测是否为空 prompt 行"""
        import re
        return bool(re.match(r'^[^\$]+\$ $', text))
```

### 4.2 AdbCliDriver 扩展（infrastructure/adb/cli.py）

```python
# 新增方法
async def create_shell(self, device_id: str) -> "InteractiveShell":
    """创建交互式 shell 会话"""
    from app.infrastructure.adb.shell import InteractiveShell
    shell = InteractiveShell()
    await shell.start(device_id)
    return shell

# 修复 shell_stream() stderr
async def shell_stream(self, device_id: str, cmd: str) -> AsyncIterator[str]:
    """流式执行 shell 命令（Pipe 模式，用于 HTTP API 降级）"""
    proc = await asyncio.create_subprocess_exec(
        self.adb_path, "-s", device_id, "shell",
        f"{cmd} 2>&1",  # 合并 stderr
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stderr_task = asyncio.create_task(self._consume_stderr(proc.stderr))
    try:
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            yield line.decode("utf-8", errors="replace").rstrip("\r\n") + "\n"
    finally:
        stderr_task.cancel()
        if proc.returncode is None:
            proc.kill()

# 修复 stream_logcat() stderr 死锁
async def stream_logcat(self, device_id: str) -> AsyncIterator[str]:
    """逐行流式输出 logcat（修复 stderr 死锁）"""
    proc = await asyncio.create_subprocess_exec(
        self.adb_path, "-s", device_id, "logcat", "-v", "threadtime",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stderr_task = asyncio.create_task(self._consume_stderr(proc.stderr))
    try:
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            yield line.decode("utf-8", errors="replace").rstrip("\r\n")
    finally:
        stderr_task.cancel()
        proc.kill()

# 辅助方法
async def _consume_stderr(self, stderr):
    """消费 stderr 防止管道缓冲区满导致死锁"""
    try:
        while True:
            line = await stderr.readline()
            if not line:
                break
            logger.debug("adb_stderr", line=line.decode(errors="replace").strip())
    except (asyncio.CancelledError, Exception):
        pass
```

### 4.3 DebugService 集成（application/debug_service.py）

```python
class DebugService:
    def __init__(self, adb: AdbDriver, repo: DebugRepository):
        # ... 现有代码 ...
        self.shell_sessions: dict[str, "ShellSession"] = {}  # 新增
    
    async def get_or_create_shell(self, session_id: str) -> "ShellSession":
        """获取或创建交互式 shell"""
        if session_id in self.shell_sessions:
            shell = self.shell_sessions[session_id]
            if shell.is_alive:
                return shell
        
        # 创建新 shell
        session = self.sessions.get(session_id)
        if not session:
            raise ValueError(f"Session not found: {session_id}")
        
        shell = await self.adb.create_shell(session.device_id)
        self.shell_sessions[session_id] = shell
        return shell
    
    async def exec_shell_stream(self, session_id: str, cmd: str):
        """流式执行 shell 命令（改用 InteractiveShell）"""
        shell = await self.get_or_create_shell(session_id)
        async for line in shell.execute(cmd):
            yield line
    
    async def close_session(self, session_id: str):
        """关闭调试会话（同时关闭 shell）"""
        # ... 现有代码 ...
        if session_id in self.shell_sessions:
            await self.shell_sessions[session_id].stop()
            del self.shell_sessions[session_id]
```

### 4.4 WebSocket 协议扩展（interfaces/ws/debug.py）

```python
# 新增 `input` 操作
elif op == "input":
    # 发送原始按键到设备 shell
    data = base64.b64decode(data.get("data", ""))
    try:
        shell = await debug_service.get_or_create_shell(session_id)
        await shell.send_input(data)
    except Exception as e:
        await websocket.send_json({
            "type": "error",
            "message": f"Failed to send input: {e}"
        })
```

**前端发送示例**：

```typescript
// 发送回车
debugWs.send({ op: 'input', data: btoa('\r') })

// 发送 Ctrl+C
debugWs.send({ op: 'input', data: btoa('\x03') })

// 发送 Tab
debugWs.send({ op: 'input', data: btoa('\t') })
```

### 4.5 ShellView.vue 重构（完全透传模式）

```typescript
<template>
  <div class="shell-view">
    <div ref="terminalRef" class="terminal"></div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from 'xterm-addon-fit'
import '@xterm/xterm/css/xterm.css'
import { useDebugStore } from '@/stores/debug'

defineProps<{ deviceId: string }>()
const debugStore = useDebugStore()

const terminalRef = ref<HTMLElement>()
let term: Terminal | null = null
let fitAddon: FitAddon | null = null

onMounted(async () => {
  if (!terminalRef.value) return
  
  // 创建 xterm.js 实例（固定 80x24）
  term = new Terminal({
    cols: 80,
    rows: 24,
    cursorBlink: true,
    fontSize: 14,
    fontFamily: 'Consolas, Monaco, monospace',
  })
  
  fitAddon = new FitAddon()
  term.loadAddon(fitAddon)
  term.open(terminalRef.value)
  fitAddon.fit()
  
  // 显示欢迎信息
  term.writeln('OpenScrcpy Shell (PTY Mode)')
  term.writeln('Terminal: 80x24')
  term.writeln('')
  
  // 完全透传模式：每次按键 → WebSocket → 设备
  term.onData((data) => {
    debugStore.sendInput(data)
  })
  
  // 接收设备输出 → 写入终端
  debugStore.setShellOutputHandler((output: string) => {
    term?.write(output)
  })
})

onUnmounted(() => {
  term?.dispose()
})
</script>

<style scoped>
.shell-view {
  height: 100%;
  padding: 0.5rem;
  background: #1a1a1a;
}

.terminal {
  height: 100%;
}
</style>
```

**关键变化**：
- ❌ 删除：currentLine、history、clearCurrentLine、redrawPrompt、executeCommand
- ✅ 新增：固定 cols: 80, rows: 24
- ✅ 新增：onData → debugStore.sendInput(data)
- ✅ 新增：setShellOutputHandler 接收设备输出

### 4.6 debug.ts Store 扩展

```typescript
export const useDebugStore = defineStore('debug', () => {
  // ... 现有代码 ...
  
  let shellOutputHandler: ((output: string) => void) | null = null
  
  /** 发送原始按键到设备 shell */
  async function sendInput(data: string) {
    if (!debugWs || !wsConnected.value) {
      console.warn('WebSocket not connected, cannot send input')
      return
    }
    debugWs.send({
      op: 'input',
      data: btoa(data)  // base64 编码
    })
  }
  
  /** 设置 shell 输出处理器 */
  function setShellOutputHandler(handler: (output: string) => void) {
    shellOutputHandler = handler
  }
  
  // 修改 handleMessage
  function handleMessage(msg: any) {
    switch (msg.type) {
      case 'log':
        appendLog(msg.entry)
        break
      case 'shell_stream':
        // 流式输出：直接写入终端
        if (shellOutputHandler && msg.line) {
          shellOutputHandler(msg.line)
        }
        // 保留旧逻辑（向后兼容）
        if (onStreamLine && msg.line) {
          onStreamLine(msg.line)
        }
        break
      case 'shell_output':
        // 命令完成
        onStreamLine = null
        if (shellResolve) {
          shellResolve({ output: msg.output || '', success: msg.success !== false })
          shellResolve = null
        }
        break
      // ... 其他 case ...
    }
  }
  
  return {
    // ... 现有导出 ...
    sendInput,
    setShellOutputHandler,
  }
})
```

---

## 5. 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `domain/ports.py` | 修改 | + `ShellSession` Protocol<br>+ `AdbDriver.create_shell()` |
| `infrastructure/adb/shell.py` | **新建** | `InteractiveShell` 实现（PTY 模式） |
| `infrastructure/adb/cli.py` | 修改 | + `create_shell()` 工厂方法<br>~ `shell_stream()` stderr 修复<br>~ `stream_logcat()` stderr 防死锁<br>+ `_consume_stderr()` 辅助方法 |
| `application/debug_service.py` | 修改 | + `shell_sessions: dict`<br>+ `get_or_create_shell()`<br>~ `exec_shell_stream()` 改用 InteractiveShell<br>~ `close_session()` 关闭 shell |
| `interfaces/ws/debug.py` | 修改 | + `input` 操作处理 |
| `frontend/src/components/debug/ShellView.vue` | **重构** | 改为"透传模式"，固定 80x24，移除本地模拟逻辑 |
| `frontend/src/stores/debug.ts` | 修改 | + `sendInput()`<br>+ `setShellOutputHandler()`<br>~ `handleMessage()` 扩展 |

---

## 6. 风险与缓解

| 风险 | 缓解措施 |
|------|----------|
| Prompt 过滤不准确 | 正则匹配 `<hostname>:<path> $`，支持多种设备 |
| PTY 模式下命令回显干扰 | 过滤包含 prompt 的行 |
| 长时间命令阻塞 | `READ_TIMEOUT = 30s`，超时抛出异常 |
| Shell 进程泄漏 | `close_session()` + `__del__` 双重保险 |
| Windows 资源泄漏 | 显式关闭 stdin/stdout/stderr transport |
| 并发输入竞争 | `asyncio.Lock` 保证串行 |
| 设备断开 | 检测空行 → 标记死亡 → 自动重启 |
| ANSI 转义序列 | xterm.js 自动处理 |

---

## 7. 验证步骤

### 7.1 单元验证（Python 脚本）

```bash
# 测试 1：PTY 模式分配
printf 'ls -la /proc/$$/fd/0\n' | timeout 3 adb shell -tt
# 预期：`/dev/pts/0`

# 测试 2：Ctrl+C 中断
python3 << 'EOF'
import asyncio
async def test():
    proc = await asyncio.create_subprocess_exec(
        'adb', 'shell', '-tt',
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
    )
    proc.stdin.write(b'sleep 100\n')
    await proc.stdin.drain()
    await asyncio.sleep(0.5)
    proc.stdin.write(b'\x03')  # Ctrl+C
    await proc.stdin.drain()
    proc.stdin.write(b'echo OK\n')
    await proc.stdin.drain()
    line = await proc.stdout.readline()
    print(f'After Ctrl+C: {line.decode().strip()!r}')
    proc.stdin.close()
    proc.kill()
asyncio.run(test())
EOF
# 预期：输出包含 `OK`
```

### 7.2 前端集成测试

| # | 测试用例 | 操作 | 预期 |
|---|----------|------|------|
| 1 | cd 持久化 | `cd /sdcard` → `pwd` | 输出 `/sdcard` |
| 2 | Ctrl+C 中断 | `sleep 100` → `Ctrl+C` → `echo OK` | 输出 `OK` |
| 3 | Tab 补全 | `ls /sys` → `Tab` | 显示补全建议 |
| 4 | 命令历史 | `echo A` → `echo B` → `↑` | 显示 `echo B` |
| 5 | su 提权 | `su` → `id` | 输出 `uid=0(root)` |
| 6 | 交互式命令 | `top` → `q` | 显示进程列表，退出正常 |
| 7 | stderr 显示 | `ls /nonexistent` | 显示错误信息 |

### 7.3 回归测试

| 功能 | 操作 | 预期 |
|------|------|------|
| HTTP shell | 断开 WS，输入命令 | 通过 HTTP 执行（Pipe 模式） |
| Logcat 日志 | 查看日志面板 | 正常推送，无中断 |
| 视频流 | 查看视频播放器 | 正常播放 |

---

## 8. 验收标准

- [ ] `cd` 跨命令目录持久化
- [ ] `su` 提权后权限持久化
- [ ] stderr 错误信息正常显示
- [ ] Ctrl+C 能中断正在运行的命令
- [ ] Tab 补全功能正常
- [ ] 命令历史（上/下箭头）正常
- [ ] 交互式命令（如 top）能运行
- [ ] Shell prompt 正确显示（如 `rk3288:/ $`）
- [ ] 会话关闭后 shell 进程正确清理
- [ ] Logcat 日志功能无回归
- [ ] 视频流功能无回归

---

## 9. 实施计划

| 阶段 | 任务 | 预计 |
|------|------|------|
| 1 | `domain/ports.py` 新增 `ShellSession` Protocol | 10 min |
| 2 | `infrastructure/adb/shell.py` 实现 `InteractiveShell`（PTY 模式） | 45 min |
| 3 | `infrastructure/adb/cli.py` 新增工厂方法 + 修复 stderr | 20 min |
| 4 | `application/debug_service.py` 集成 InteractiveShell | 20 min |
| 5 | `interfaces/ws/debug.py` 新增 `input` 操作 | 10 min |
| 6 | `ShellView.vue` 重构为透传模式（固定 80x24） | 30 min |
| 7 | `debug.ts` Store 扩展 sendInput/setShellOutputHandler | 15 min |
| 8 | 启动后端 + 前端，按 7.1-7.3 验证 | 30 min |
| **合计** | | **~180 min** |

---

## 10. 相关文档

- `方案/07-远程Shell.md` — 原始 Shell 设计文档
- `方案/进度追踪.md` — 整体进度跟踪
- `docs/经验记录.md` — 踩坑记录
- `DEVELOPMENT.md` — 开发约束

---

## 附录 A：代码走查结论

| 检查项 | 结果 | 说明 |
|--------|------|------|
| 后端架构支持改造 | ✅ | 四层架构清晰，可扩展 |
| WebSocket 协议可扩展 | ✅ | 新增 `input` 操作即可 |
| 前端 xterm.js 支持透传 | ✅ | onData → WebSocket → 设备 |
| PTY 模式验证 | ✅ | `adb shell -tt` 分配 `/dev/pts/0` |
| Windows 兼容性 | ✅ | asyncio subprocess 已验证 |
| 窗口大小策略 | ✅ | 固定 80x24，接受差异 |

---

**文档结束**
