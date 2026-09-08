# 07 - 远程 Shell

## 目标

实现基于 xterm.js 的远程 Shell 终端，支持命令历史、自动补全、会话恢复。

## 状态

- ✅ ShellView 组件框架
- ✅ 后端 exec_shell
- ⏳ PTY 伪终端
- ⏳ 流式输出
- ⏳ 命令历史
- ⏳ 自动补全

## 依赖

- [05-调试会话管理.md](./05-调试会话管理.md) ⏳
- xterm.js

## 后端实现

### Step 1: PTY 伪终端 ⏳

**问题**：当前 `exec_shell` 每次执行新命令都启动新进程，无法保持状态

**方案**：使用 PTY

```python
import pty
import os
import asyncio
import select

class PTYShell:
    """PTY-based shell session"""
    
    def __init__(self, device_id: str):
        self.device_id = device_id
        self.master_fd: int | None = None
        self.slave_fd: int | None = None
        self.process: asyncio.subprocess.Process | None = None
    
    async def start(self, cols: int = 80, rows: int = 24):
        """Start PTY shell"""
        # 创建 PTY
        self.master_fd, self.slave_fd = pty.openpty()
        
        # 设置终端大小
        import struct, fcntl, termios
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(self.slave_fd, termios.TIOCSWINSZ, winsize)
        
        # 启动 adb shell
        self.process = await asyncio.create_subprocess_exec(
            "adb", "-s", self.device_id, "shell",
            stdin=self.slave_fd,
            stdout=self.slave_fd,
            stderr=self.slave_fd,
        )
        
        # 关闭 slave fd（父进程不需要）
        os.close(self.slave_fd)
        self.slave_fd = None
    
    async def write(self, data: str):
        """Write input to shell"""
        if self.master_fd:
            os.write(self.master_fd, data.encode())
    
    async def read(self) -> str:
        """Read output from shell"""
        if not self.master_fd:
            return ""
        
        # 非阻塞读取
        r, _, _ = select.select([self.master_fd], [], [], 0.1)
        if r:
            try:
                data = os.read(self.master_fd, 4096)
                return data.decode('utf-8', errors='replace')
            except OSError:
                return ""
        return ""
    
    async def resize(self, cols: int, rows: int):
        """Resize terminal"""
        if self.master_fd:
            import struct, fcntl, termios
            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, winsize)
    
    async def stop(self):
        """Stop shell"""
        if self.process:
            self.process.kill()
            await self.process.wait()
        if self.master_fd:
            os.close(self.master_fd)
```

**注意**：Windows 不支持 PTY，需要使用 `pywinpty` 或改为 WebSocket 中继方案。

### Step 2: Shell 会话管理 ⏳

```python
class DebugService:
    def __init__(self, ...):
        self.shell_sessions: dict[str, PTYShell] = {}
    
    async def open_shell(self, session_id: str, cols: int = 80, rows: int = 24) -> str:
        """Open interactive shell"""
        session = self.sessions.get(session_id)
        if not session:
            raise ValueError(f"Session not found: {session_id}")
        
        shell = PTYShell(session.device_id)
        await shell.start(cols, rows)
        
        self.shell_sessions[session_id] = shell
        return session_id
    
    async def write_shell(self, session_id: str, data: str):
        """Write to shell"""
        shell = self.shell_sessions.get(session_id)
        if not shell:
            raise ValueError(f"Shell not found for session: {session_id}")
        
        await shell.write(data)
    
    async def read_shell(self, session_id: str) -> str:
        """Read from shell"""
        shell = self.shell_sessions.get(session_id)
        if not shell:
            raise ValueError(f"Shell not found for session: {session_id}")
        
        return await shell.read()
    
    async def resize_shell(self, session_id: str, cols: int, rows: int):
        """Resize shell"""
        shell = self.shell_sessions.get(session_id)
        if shell:
            await shell.resize(cols, rows)
    
    async def close_shell(self, session_id: str):
        """Close shell"""
        shell = self.shell_sessions.pop(session_id, None)
        if shell:
            await shell.stop()
```

### Step 3: WebSocket Shell 端点 ⏳

```python
@router.websocket("/ws/shell/{session_id}")
async def shell_stream(websocket: WebSocket, session_id: str):
    """Interactive shell WebSocket"""
    await websocket.accept()
    
    debug_service = get_debug_service()
    
    try:
        # 接收初始 resize
        data = await websocket.receive_json()
        cols = data.get("cols", 80)
        rows = data.get("rows", 24)
        
        # 打开 shell
        await debug_service.open_shell(session_id, cols, rows)
        
        # 双向转发
        async def read_loop():
            while True:
                output = await debug_service.read_shell(session_id)
                if output:
                    await websocket.send_text(output)
                await asyncio.sleep(0.01)
        
        read_task = asyncio.create_task(read_loop())
        
        try:
            while True:
                data = await websocket.receive_json()
                
                if data.get("type") == "input":
                    await debug_service.write_shell(session_id, data["data"])
                elif data.get("type") == "resize":
                    await debug_service.resize_shell(
                        session_id, data["cols"], data["rows"]
                    )
        finally:
            read_task.cancel()
    
    except WebSocketDisconnect:
        await debug_service.close_shell(session_id)
```

## 前端实现

### ShellView 组件

**文件**：`frontend/src/components/debug/ShellView.vue`

```vue
<template>
  <div class="shell-view">
    <div ref="terminalRef" class="terminal"></div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from 'xterm-addon-fit'
import { WebLinksAddon } from 'xterm-addon-web-links'
import '@xterm/xterm/css/xterm.css'
import { useDebugStore } from '@/stores/debug'

const props = defineProps<{ deviceId: string }>()
const debugStore = useDebugStore()

const terminalRef = ref<HTMLElement>()
let term: Terminal | null = null
let fitAddon: FitAddon | null = null
let ws: WebSocket | null = null

onMounted(async () => {
  if (!terminalRef.value) return
  
  // 初始化终端
  term = new Terminal({
    cursorBlink: true,
    fontSize: 14,
    fontFamily: 'Consolas, Monaco, monospace',
    theme: {
      background: '#1e1e1e',
      foreground: '#d4d4d4',
      cursor: '#d4d4d4',
    },
  })
  
  fitAddon = new FitAddon()
  term.loadAddon(fitAddon)
  term.loadAddon(new WebLinksAddon())
  
  term.open(terminalRef.value)
  fitAddon.fit()
  
  // 连接 WebSocket
  const sessionId = debugStore.sessionId
  if (!sessionId) return
  
  ws = new WebSocket(`ws://localhost:8000/ws/shell/${sessionId}`)
  
  ws.onopen = () => {
    // 发送初始 resize
    ws!.send(JSON.stringify({
      cols: term!.cols,
      rows: term!.rows,
    }))
  }
  
  ws.onmessage = (event) => {
    term!.write(event.data)
  }
  
  ws.onclose = () => {
    term!.write('\r\n[Shell disconnected]\r\n')
  }
  
  // 终端输入
  term.onData((data) => {
    ws?.send(JSON.stringify({
      type: 'input',
      data: data,
    }))
  })
  
  // 窗口 resize
  term.onResize(({ cols, rows }) => {
    ws?.send(JSON.stringify({
      type: 'resize',
      cols,
      rows,
    }))
  })
  
  // 监听容器 resize
  window.addEventListener('resize', handleResize)
})

onUnmounted(() => {
  term?.dispose()
  ws?.close()
  window.removeEventListener('resize', handleResize)
})

function handleResize() {
  fitAddon?.fit()
}
</script>

<style scoped>
.shell-view {
  height: 100%;
  padding: 0.5rem;
  background: #1e1e1e;
}

.terminal {
  height: 100%;
}
</style>
```

## 技术细节

### xterm.js 配置

```typescript
const term = new Terminal({
  cursorBlink: true,           // 光标闪烁
  fontSize: 14,
  fontFamily: 'Consolas, Monaco, monospace',
  theme: {
    background: '#1e1e1e',
    foreground: '#d4d4d4',
    cursor: '#d4d4d4',
    selectionBackground: '#264f78',
  },
  scrollback: 10000,           // 回滚行数
  tabStopWidth: 4,
});
```

### ANSI 转义序列

```
# 颜色
\x1b[31m     # 红色前景
\x1b[32m     # 绿色前景
\x1b[0m      # 重置

# 光标移动
\x1b[2J      # 清屏
\x1b[H       # 光标归位
\x1b[<n>A    # 光标上移 n 行

# 示例
echo -e "\x1b[31mRed Text\x1b[0m"
```

## 测试策略

### 单元测试
- PTY 启动/停止
- 输入输出转发

### 集成测试
- WebSocket 连接
- 终端交互

## 验收标准

- ⏳ PTY 伪终端正常工作
- ⏳ 支持彩色输出
- ⏳ 支持终端 resize
- ⏳ 命令历史记录
- ⏳ 断线自动重连

## 交付物

- ⏳ PTYShell 实现（Linux/Mac）
- ⏳ WebSocket shell 端点
- ⏳ ShellView 组件（xterm.js）
- ⏳ 自动重连逻辑

## 风险与应对

| 风险 | 影响 | 应对 |
|------|------|------|
| Windows 不支持 PTY | 高 | 使用 pywinpty 或 WebSocket 中继 |
| 终端状态丢失 | 中 | 会话恢复 |
| 性能问题 | 中 | 限制输出速率 |

## 下一步

1. 实现 PTYShell（Linux/Mac）
2. 实现 WebSocket shell 端点
3. 完善 ShellView 组件
4. 测试验证

## 参考

- [xterm.js](https://xtermjs.org/)
- [PTY 文档](https://man7.org/linux/man-pages/man7/pty.7.html)
