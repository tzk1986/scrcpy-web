<!--
  远程 Shell 终端
  =================

  基于 xterm.js 实现的远程 shell 终端，通过 ADB 在设备上执行命令。

  功能：
    - 命令输入（支持退格删除）
    - 命令执行（回车发送）
    - 输出显示
    - 命令历史记录（未来实现）

  技术栈：
    - xterm.js：终端模拟器
    - xterm-addon-fit：自适应容器大小

  使用流程：
    1. 用户在终端中输入命令
    2. 按回车后，通过 debugStore.execShell() 发送到后端
    3. 后端通过 ADB 执行命令并返回输出
    4. 输出显示在终端中

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

/**
 * 组件挂载时初始化终端：
 *   1. 创建 Terminal 实例（配置光标、字体等）
 *   2. 加载 FitAddon 并适配容器大小
 *   3. 显示欢迎信息
 *   4. 注册键盘输入处理器
 */
onMounted(() => {
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
  term.writeln('')

  let currentLine = ''

  // 处理键盘输入
  term.onData((data) => {
    if (data === '\r') {
      // 回车：执行命令
      term!.write('\r\n')
      executeCommand(currentLine)
      currentLine = ''
    } else if (data === '\x7f') {
      // 退格：删除最后一个字符
      if (currentLine.length > 0) {
        currentLine = currentLine.slice(0, -1)
        term!.write('\b \b')
      }
    } else {
      // 普通字符：追加到当前行
      currentLine += data
      term!.write(data)
    }
  })
})

/** 组件卸载时销毁终端实例。 */
onUnmounted(() => {
  term?.dispose()
})

/**
 * 执行 shell 命令并显示结果。
 * 通过 debugStore.execShell() 发送到后端。
 */
async function executeCommand(cmd: string) {
  if (!cmd.trim()) return

  term!.writeln(`$ ${cmd}`)

  try {
    const result = await debugStore.execShell(cmd)
    term!.write(result.output + '\r\n')
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
