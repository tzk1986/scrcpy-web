<!--
  远程 Shell 终端（PTY 模式）
  ==========================

  基于 xterm.js 实现的远程 shell 终端，通过 PTY 模式与设备交互。

  功能：
    - 混合模式：xterm.js 完整终端 + 底部命令输入框
    - xterm.js 支持：Tab 补全、交互式命令（top/vi）、复制
    - 输入框支持：快捷命令发送、命令历史（↑↓）
    - 窗口大小固定 80x24（与设备 PTY 对齐）

  技术栈：
    - xterm.js：终端模拟器（固定 80x24）
    - xterm-addon-fit：自适应容器大小

  数据流：
    输入框 → WebSocket (input) → 后端 → adb stdin → 设备 PTY
    终端按键 → WebSocket (input) → 后端 → adb stdin → 设备 PTY
    用户看到 ← xterm.js ← WebSocket (shell_stream) ← 后端 ← adb stdout ← 设备 PTY
-->
<template>
  <div class="shell-view">
    <!-- 终端区域 -->
    <div ref="terminalRef" class="terminal"></div>

    <!-- 输入框区域 -->
    <div class="input-bar">
      <input
        ref="inputRef"
        v-model="command"
        @keydown.enter="sendCommand"
        @keydown.up.prevent="historyUp"
        @keydown.down.prevent="historyDown"
        @keydown.tab.prevent="sendTab"
        placeholder="输入命令，Enter 发送，↑↓ 切换历史，Tab 补全..."
        class="command-input"
      />
      <button @click="sendCommand" class="send-btn">发送</button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from 'xterm-addon-fit'
import '@xterm/xterm/css/xterm.css'
import { useDebugStore } from '@/stores/debug'

/** 目标设备 ID（从父组件传入）。 */
defineProps<{ deviceId: string }>()
const debugStore = useDebugStore()

/** 终端容器 DOM 引用。 */
const terminalRef = ref<HTMLElement>()
/** xterm.js 终端实例。 */
let term: Terminal | null = null
/** 终端自适应插件。 */
let fitAddon: FitAddon | null = null

/** 命令输入框引用。 */
const inputRef = ref<HTMLInputElement>()
/** 当前输入的命令。 */
const command = ref('')
/** 命令历史（最多 50 条）。 */
const history = ref<string[]>([])
/** 历史索引（-1 表示不在历史中）。 */
const historyIndex = ref(-1)

/**
 * 发送命令到 shell。
 * 在命令末尾追加 \n 确保 shell 识别行结束符。
 */
function sendCommand() {
  const cmd = command.value.trim()
  if (!cmd) return

  // 添加到历史
  history.value.push(cmd)
  if (history.value.length > 50) {
    history.value.shift()
  }
  historyIndex.value = -1

  // 发送命令（追加 \n）
  debugStore.sendInput(cmd + '\n')
  command.value = ''

  // 重新聚焦输入框
  inputRef.value?.focus()
}

/**
 * 切换到上一条历史命令。
 */
function historyUp() {
  if (historyIndex.value < history.value.length - 1) {
    historyIndex.value++
    command.value = history.value[history.value.length - 1 - historyIndex.value]
  }
}

/**
 * 切换到下一条历史命令。
 */
function historyDown() {
  if (historyIndex.value > 0) {
    historyIndex.value--
    command.value = history.value[history.value.length - 1 - historyIndex.value]
  } else {
    historyIndex.value = -1
    command.value = ''
  }
}

/**
 * 输入框 Tab 键：发送 Tab 到设备进行补全
 */
function sendTab() {
  debugStore.sendInput('\t')
}

/**
 * 组件挂载时初始化终端：
 *   1. 创建 Terminal 实例（固定 80x24）
 *   2. 加载 FitAddon 并适配容器大小
 *   3. 显示欢迎信息
 *   4. 连接 WebSocket
 *   5. 注册键盘输入处理器（完全透传模式）
 *   6. 拦截 Tab 键（防止浏览器焦点切换）
 *   7. 设置 shell 输出处理器（接收设备输出）
 *   8. 聚焦输入框
 */
onMounted(async () => {
  if (!terminalRef.value) return

  // 创建 xterm.js 实例（固定 80x24，与设备 PTY 对齐）
  term = new Terminal({
    cols: 80,
    rows: 24,
    cursorBlink: true,
    fontSize: 14,
    fontFamily: 'Consolas, Monaco, monospace',
    theme: {
      background: '#1a1a1a',
      foreground: '#ffffff',
      cursor: '#ffffff',
    },
  })

  fitAddon = new FitAddon()
  term.loadAddon(fitAddon)
  term.open(terminalRef.value)
  fitAddon.fit()

  // 显示欢迎信息
  term.writeln('OpenScrcpy Shell (PTY Mode)')
  term.writeln('Terminal: 80x24')
  term.writeln('Features: Tab completion, interactive commands, copy/paste')
  term.writeln('Use input box below for quick commands, or type directly in terminal')
  term.writeln('')

  // 先设置输出处理器（在 connectWebSocket 之前，否则初始 prompt 丢失）
  debugStore.setShellOutputHandler((output: string) => {
    term?.write(output)
  })

  // 连接 WebSocket（subscribe 会触发后端发送初始 prompt）
  await debugStore.connectWebSocket()

  // Tab 键：onKey 拦截阻止 xterm.js 本地渲染，直接发送到设备
  term.onKey((e) => {
    if (e.key === '\t') {
      e.domEvent.preventDefault()
      e.domEvent.stopPropagation()
      debugStore.sendInput('\t')
    }
  })

  // 其他按键透传到设备
  term.onData((data) => {
    if (data !== '\t') {
      debugStore.sendInput(data)
      command.value = ''
      historyIndex.value = -1
    }
  })

  // 包装 term.write，过滤掉 xterm.js 本地渲染的 Tab 字符
  const nativeWrite = term.write.bind(term)
  term.write = (data: string | Uint8Array, callback?: () => void) => {
    if (typeof data === 'string') {
      data = data.replace(/\t/g, '')
    }
    nativeWrite(data, callback)
  }

  // 聚焦输入框
  inputRef.value?.focus()
})

/** 组件卸载时销毁终端实例。 */
onUnmounted(() => {
  term?.dispose()
})
</script>

<style scoped>
.shell-view {
  height: 100%;
  display: flex;
  flex-direction: column;
  background: #1a1a1a;
}

.terminal {
  flex: 1;
  padding: 0.5rem;
  overflow: hidden;
}

.input-bar {
  display: flex;
  gap: 0.5rem;
  padding: 0.5rem;
  background: #2a2a2a;
  border-top: 1px solid #444;
}

.command-input {
  flex: 1;
  padding: 0.5rem 0.75rem;
  background: #1a1a1a;
  border: 1px solid #555;
  border-radius: 4px;
  color: #fff;
  font-family: 'Consolas, Monaco, monospace';
  font-size: 14px;
  outline: none;
}

.command-input:focus {
  border-color: #0078d4;
}

.command-input::placeholder {
  color: #888;
}

.send-btn {
  padding: 0.5rem 1rem;
  background: #0078d4;
  border: none;
  border-radius: 4px;
  color: #fff;
  font-size: 14px;
  cursor: pointer;
  white-space: nowrap;
}

.send-btn:hover {
  background: #106ebe;
}

.send-btn:active {
  background: #005a9e;
}
</style>
