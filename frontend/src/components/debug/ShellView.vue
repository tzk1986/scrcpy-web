<!--
  远程 Shell 终端
  =================

  基于 xterm.js 实现的远程 shell 终端，通过 ADB 在设备上执行命令。

  功能：
    - 命令输入（支持退格删除）
    - 命令执行（回车发送，通过 WebSocket）
    - 流式输出显示（命令输出逐行实时显示）
    - 命令历史记录（上/下箭头键浏览）
    - Ctrl+C 取消当前输入
    - Ctrl+L 清屏

  技术栈：
    - xterm.js：终端模拟器
    - xterm-addon-fit：自适应容器大小

  使用流程：
    1. 用户在终端中输入命令
    2. 按回车后，通过 debugStore.execShellWs(cmd, streamCallback) 发送
    3. Store 通过 WebSocket 发送 exec 操作
    4. 后端通过 ADB 执行命令，逐行流式返回 shell_stream 消息
    5. Store 调用 streamCallback 将每行写入终端
    6. 命令完成后发送 shell_output（done=true），Promise 解析

  消息路由：
    ShellView 通过 debugStore.execShellWs() 使用 store 管理的同一个 WS 连接，
    store 根据消息类型路由：
      - shell_stream → 调用 onStreamLine 回调（实时写入终端）
      - shell_output → 解析 pending Promise

  限制：
    当前是非交互式 shell（每条命令独立执行）。
    未来应实现 PTY（伪终端）以支持交互式命令（如 top, vi）。
-->
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

/** 目标设备 ID（从父组件传入）。 */
defineProps<{ deviceId: string }>()
const debugStore = useDebugStore()

/** 终端容器 DOM 引用。 */
const terminalRef = ref<HTMLElement>()
/** xterm.js 终端实例。 */
let term: Terminal | null = null
/** 终端自适应插件。 */
let fitAddon: FitAddon | null = null

/** 命令历史记录（最近 100 条）。 */
const history: string[] = []
/** 当前在历史记录中的位置（-1 表示不在历史中）。 */
let historyIndex = -1
/** 用户在浏览历史前输入的临时内容。 */
let tempLine = ''

/**
 * 组件挂载时初始化终端：
 *   1. 创建 Terminal 实例（配置光标、字体等）
 *   2. 加载 FitAddon 并适配容器大小
 *   3. 显示欢迎信息
 *   4. 注册键盘输入处理器（包括方向键、Ctrl 组合键）
 */
onMounted(async () => {
  if (!terminalRef.value) return

  term = new Terminal({
    cursorBlink: true,
    fontSize: 14,
    fontFamily: 'Consolas, Monaco, monospace',
  })

  fitAddon = new FitAddon()
  term.loadAddon(fitAddon)
  term.open(terminalRef.value)
  fitAddon.fit()

  term.writeln('OpenScrcpy Shell')
  term.writeln('Type commands and press Enter')
  term.writeln('Up/Down: history  Ctrl+C: cancel  Ctrl+L: clear')
  term.writeln('')

  let currentLine = ''

  /**
   * 清除当前输入行（用于历史导航时刷新显示）。
   * 将光标移回行首，用空格覆盖，再移回行首。
   */
  function clearCurrentLine() {
    if (currentLine.length > 0) {
      term!.write('\r' + ' '.repeat(currentLine.length + 2) + '\r')
    }
  }

  /**
   * 重新绘制提示符和当前输入行。
   */
  function redrawPrompt(line: string) {
    term!.write('$ ' + line)
    currentLine = line
  }

  // 处理键盘输入
  term.onData((data) => {
    // 回车：执行命令
    if (data === '\r') {
      term!.write('\r\n')
      if (currentLine.trim()) {
        // 添加到历史记录（去重）
        if (history.length === 0 || history[history.length - 1] !== currentLine) {
          history.push(currentLine)
          if (history.length > 100) history.shift()
        }
        historyIndex = -1
        tempLine = ''
        executeCommand(currentLine)
      } else {
        term!.write('$ ')
      }
      currentLine = ''
      return
    }

    // 退格：删除最后一个字符
    if (data === '\x7f') {
      if (currentLine.length > 0) {
        currentLine = currentLine.slice(0, -1)
        term!.write('\b \b')
      }
      return
    }

    // Ctrl+C：取消当前输入
    if (data === '\x03') {
      term!.writeln('^C')
      currentLine = ''
      historyIndex = -1
      term!.write('$ ')
      return
    }

    // Ctrl+L：清屏
    if (data === '\x0c') {
      term!.clear()
      term!.write('$ ' + currentLine)
      return
    }

    // 上箭头：浏览历史（前一条）
    if (data === '\x1b[A') {
      if (history.length === 0) return
      if (historyIndex === -1) {
        // 首次按上箭头，保存当前输入
        tempLine = currentLine
        historyIndex = history.length - 1
      } else if (historyIndex > 0) {
        historyIndex--
      }
      clearCurrentLine()
      redrawPrompt(history[historyIndex])
      return
    }

    // 下箭头：浏览历史（后一条）
    if (data === '\x1b[B') {
      if (historyIndex === -1) return
      if (historyIndex < history.length - 1) {
        historyIndex++
        clearCurrentLine()
        redrawPrompt(history[historyIndex])
      } else {
        // 已到历史末尾，恢复临时输入
        historyIndex = -1
        clearCurrentLine()
        redrawPrompt(tempLine)
      }
      return
    }

    // 忽略左/右箭头和其余转义序列
    if (data.startsWith('\x1b')) return

    // 普通可打印字符：追加到当前行
    currentLine += data
    term!.write(data)
  })

  term!.write('$ ')
})

/** 组件卸载时销毁终端实例。 */
onUnmounted(() => {
  term?.dispose()
})

/**
 * 执行 shell 命令并显示结果。
 * 使用流式输出：每行输出到达时立即写入终端。
 */
async function executeCommand(cmd: string) {
  try {
    const result = await debugStore.execShellWs(cmd, (line: string) => {
      // 流式回调：每行输出立即写入终端
      // 确保行以换行符结尾
      if (line.endsWith('\n')) {
        term!.write(line)
      } else {
        term!.write(line + '\r\n')
      }
    })
    // 如果流式回调没有收到任何输出（降级到 HTTP 的情况），显示最终输出
    if (result.output && !result.output.endsWith('\n')) {
      term!.write('\r\n')
    }
    if (!result.success) {
      term!.writeln('[Command failed]')
    }
  } catch (error) {
    term!.writeln(`Error: ${error}`)
  }

  term!.write('$ ')
}
</script>

<style scoped>
.shell-view {
  height: 100%;
  padding: 0.5rem;
}

.terminal {
  height: 100%;
}
</style>
