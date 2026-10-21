/**
 * 调试状态管理（Pinia Store）
 * ==============================
 *
 * 管理调试会话的全局状态：
 *   - sessionId: 当前活跃的调试会话 ID
 *   - logs: 日志条目列表（内存缓冲区，最多 50000 条）
 *   - filter: 日志过滤条件（level, tag）
 *   - connected: 是否已连接到调试会话
 *   - wsConnected: WebSocket 是否已连接并订阅日志
 *
 * 提供的方法：
 *   - createSession(): 创建新的调试会话
 *   - fetchLogs(): 从后端获取日志
 *   - closeSession(): 关闭调试会话
 *   - appendLog(): 向日志缓冲区追加一条记录
 *   - connectWebSocket(): 建立 WebSocket 连接并订阅日志
 *   - disconnectWebSocket(): 断开 WebSocket 连接
 *
 * 使用方式（在 Vue 组件中）：
 *   import { useDebugStore } from '@/stores/debug'
 *   const debugStore = useDebugStore()
 *   await debugStore.createSession(deviceId, userId)
 *   await debugStore.connectWebSocket()
 */

import { defineStore } from 'pinia'
import { ref } from 'vue'
import { api } from '@/services/api'
import { WebSocketService } from '@/services/websocket'

/**
 * 日志条目接口。
 * 与后端 LogEntry 数据类对应。
 */
export interface LogEntry {
  ts: number      // Unix 时间戳（秒）
  level: string   // 日志级别：V/D/I/W/E/F
  pid: number     // 进程 ID
  tid: number     // 线程 ID
  tag: string     // 日志标签
  message: string // 日志消息
  raw?: string    // 原始日志行
  seq?: number    // 会话内单调递增序列号（断线续传游标）
}

/**
 * fetchLogs 历史与现有缓冲的合并纯函数（方案 51 F2-04）。
 * 按 seq 去重、升序归位——整体替换会把请求在途期间 WS 订阅窗
 * 推达的增量日志覆盖丢失。重叠 seq 时 existing 实例优先；无 seq
 * 条目（旧协议/测试形态）按内容签名去重、保原序附尾。
 */
export function mergeLogs(existing: LogEntry[], incoming: LogEntry[]): LogEntry[] {
  const bySeq = new Map<number, LogEntry>()
  for (const e of existing) {
    if (typeof e.seq === 'number') bySeq.set(e.seq, e)
  }
  for (const e of incoming) {
    if (typeof e.seq === 'number' && !bySeq.has(e.seq)) bySeq.set(e.seq, e)
  }
  const sorted = [...bySeq.entries()]
    .sort(([a], [b]) => a - b)
    .map(([, e]) => e)

  const seenSig = new Set<string>()
  const tail: LogEntry[] = []
  for (const e of [...existing, ...incoming]) {
    if (typeof e.seq === 'number') continue
    const sig = `${e.ts}|${e.level}|${e.pid}|${e.tid}|${e.tag}|${e.message}`
    if (seenSig.has(sig)) continue
    seenSig.add(sig)
    tail.push(e)
  }
  return [...sorted, ...tail]
}

/**
 * 调试 Store。
 * 管理调试会话的生命周期和日志数据。
 */
export const useDebugStore = defineStore('debug', () => {
  const sessionId = ref<string | null>(null)
  const logs = ref<LogEntry[]>([])
  const filter = ref({
    level: null as string | null,
    tag: null as string | null,
  })
  const connected = ref(false)
  const wsConnected = ref(false)
  /** 是否正在录制（接收）新日志。默认暂停：日志页打开不采集，
   *  用户点「开始」才连 WS、启动后端 logcat（方案 17 实施项 5）。 */
  const isRecording = ref(false)

  let debugWs: WebSocketService | null = null
  /** 已收到的最大日志 seq（-1 表示尚无带 seq 的日志；重连时据此增量补发）。 */
  const lastSeq = ref(-1)
  /** 主动断开标记：区分 closeSession/disconnectWebSocket 与意外断线。 */
  let manualClose = false

  /** Shell 输出处理器：PTY 模式下接收设备输出并写入终端。 */
  let shellOutputHandler: ((output: string) => void) | null = null

  /**
   * 创建调试会话。
   * 设置 sessionId 和 connected 状态。
   */
  async function createSession(deviceId: string, userId: string) {
    const res = await api.createDebugSession(deviceId, userId)
    sessionId.value = res.session_id
    connected.value = true
    return res
  }

  /**
   * 从后端获取日志。
   * 应用当前的过滤条件（level, tag）。
   * 方案 51 F2-04：与现有缓冲按 seq 去重合并保序（原整体替换会覆盖
   * 丢失请求在途期间 WS 订阅窗推达的增量日志）。
   */
  async function fetchLogs(limit = 1000) {
    if (!sessionId.value) return
    const res = await api.getLogs(
      sessionId.value,
      filter.value.level,
      filter.value.tag,
      limit
    )
    logs.value = mergeLogs(logs.value, res.logs)
  }

  /**
   * 关闭调试会话。
   * 清理状态（sessionId, logs, connected）。
   */
  async function closeSession() {
    // 先断开 WebSocket
    await disconnectWebSocket()

    if (!sessionId.value) return
    await api.closeDebugSession(sessionId.value)
    sessionId.value = null
    logs.value = []
    connected.value = false
    lastSeq.value = -1
  }

  /**
   * 向日志缓冲区追加一条记录。
   * 超过 50000 条时，移除最旧的记录（FIFO）。
   */
  function appendLog(entry: LogEntry) {
    logs.value.push(entry)
    if (logs.value.length > 50000) {
      logs.value.shift()
    }
  }

  /**
   * 建立 WebSocket 连接并订阅实时日志推送。
   */
  async function connectWebSocket() {
    if (!sessionId.value) return
    if (debugWs) return  // 已连接

    // 使用 window.location.host 支持开发和生产环境
    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const wsUrl = `${wsProtocol}//${window.location.host}/ws/debug/${sessionId.value}`

    debugWs = new WebSocketService(wsUrl)

    let opened = false  // 局部连接标志——避免与外层 connected ref 同名遮蔽（方案 51 F2-19）

    debugWs.setMessageHandler((data) => {
      if (typeof data === 'string') {
        try {
          const msg = JSON.parse(data)
          // 不逐条记日志（方案 51 F2-12）
          handleMessage(msg)
        } catch (e) {
          console.error('Failed to parse WebSocket message:', e)
        }
      }
    })

    debugWs.setErrorHandler((error) => {
      console.error('[DebugStore] WebSocket error:', error)
    })

    debugWs.setCloseHandler(() => {
      wsConnected.value = false
      // 方案 51 F2-01：丢弃旧实例前先 close()——WebSocketService.onclose 内含
      // 自动重拨（!manualClose 时 scheduleReconnect），不关会与下方 store 级
      // 重连并存成双连接。close 后 store 定时器是单一路径。
      const dying = debugWs
      debugWs = null
      dying?.close()
      // 意外断线（非主动断开）且录制开启时才自动重连，按 from_seq 补发缺口日志；
      // 未录制时无需重连（也不再重启后端采集，实施项 5）
      if (!manualClose && isRecording.value && sessionId.value) {
        setTimeout(() => {
          if (!debugWs && isRecording.value && sessionId.value) connectWebSocket()
        }, 2000)
      }
    })

    manualClose = false
    debugWs.connect()

    // 等待连接建立后发送订阅请求
    await new Promise<void>((resolve) => {
      const checkInterval = setInterval(() => {
        if (debugWs && (debugWs as any).ws?.readyState === WebSocket.OPEN) {
          opened = true
          clearInterval(checkInterval)
          resolve()
        }
      }, 100)

      // 超时保护（10秒）
      setTimeout(() => {
        clearInterval(checkInterval)
        if (!opened) {
          console.error('[DebugStore] WebSocket connection timeout')
        }
        resolve()
      }, 10000)
    })

    // 只有在连接成功时才发送订阅请求并设置状态
    if (opened && debugWs) {
      // 带 lastSeq 时请求从 seq+1 补发（断线续传），首次订阅行为与旧协议一致
      debugWs.send({
        op: 'subscribe',
        ...(lastSeq.value >= 0 ? { from_seq: lastSeq.value + 1 } : {}),
      })
      // 同步当前的录制状态到后端
      debugWs.send({ op: 'filter', level: filter.value.level, tag: filter.value.tag, paused: !isRecording.value })
      wsConnected.value = true
    } else {
      console.error('[DebugStore] Failed to connect WebSocket')
      // 方案 51 F2-18：先 close 再丢弃，消除 CONNECTING 僵尸 socket
      // （超时后仍可能完成握手，留下无人管理的连接）
      debugWs?.close()
      debugWs = null
    }
  }

  /**
   * 断开 WebSocket 连接。
   */
  async function disconnectWebSocket() {
    if (debugWs) {
      manualClose = true
      debugWs.close()
      debugWs = null
    }
    wsConnected.value = false
  }

  /**
   * 设置服务端日志过滤条件。
   * 通过 WebSocket 发送 filter 操作，只有匹配的日志才会推送。
   * 这减少了网络流量和客户端处理开销。
   */
  function setFilter(level: string | null, tag: string | null) {
    filter.value.level = level
    filter.value.tag = tag
    if (debugWs && wsConnected.value) {
      debugWs.send({ op: 'filter', level, tag, paused: !isRecording.value })
    }
  }

  /**
   * 设置录制状态（开启/暂停接收新日志）。
   * 暂停时后端不再推送新日志，但缓冲区中的日志保留可搜索。
   */
  function setRecording(recording: boolean) {
    isRecording.value = recording
    if (debugWs && wsConnected.value) {
      debugWs.send({ op: 'filter', level: filter.value.level, tag: filter.value.tag, paused: !recording })
    }
  }

  /**
   * 发送原始按键到设备 shell（PTY 模式）。
   *
   * 用于完全透传的交互式终端，支持 Ctrl+C、Tab、↑↓ 等。
   * 通过 WebSocket 发送 input 操作，数据使用 base64 编码。
   *
   * 参数：
   *   data: 原始按键数据（字符串）。
   */
  function sendInput(data: string) {
    if (!debugWs || !wsConnected.value) {
      console.warn('[DebugStore] WebSocket not connected, cannot send input')
      return
    }
    // 修复：设备 PTY 可能未设置 ICRNL 标志，\r 不会被转换为 \n
    // 将 \r 替换为 \n 确保 shell 的 readline() 能识别行结束符
    if (data === '\r') {
      data = '\n'
    }
    // base64 编码
    const encoded = btoa(unescape(encodeURIComponent(data)))
    debugWs.send({ op: 'input', data: encoded })
  }

  /**
   * 发送终端 resize 操作（PTY 模式）。
   *
   * 终端尺寸变化（fit 触发 onResize）时同步到后端，后端调整设备 PTY 窗口。
   * 未连接时静默忽略（resize 是高频 UI 事件，不告警刷屏）。
   *
   * 参数：
   *   cols: 列数（正整数）。
   *   rows: 行数（正整数）。
   */
  function sendResize(cols: number, rows: number) {
    if (!debugWs || !wsConnected.value) return
    debugWs.send({ op: 'resize', cols, rows })
  }

  /**
   * 设置 shell 输出处理器（PTY 模式）。
   *
   * 当收到 shell_stream 消息时，调用此处理器将输出写入终端。
   *
   * 参数：
   *   handler: 输出处理函数；传 null 摘除（方案 51 F2-05：组件卸载须
   *   注销，否则闭包滞留引用已销毁终端，延迟消息写入失效实例）。
   */
  function setShellOutputHandler(handler: ((output: string) => void) | null) {
    shellOutputHandler = handler
  }

  /**
   * 处理 WebSocket 消息。
   * 根据消息类型分发到不同的处理器：
   *   - log: 追加到日志缓冲区
   *   - shell_stream: 流式 shell 输出一行（写入 shellOutputHandler 终端）
   *   - error: 错误消息（显示在终端）
   *   - session_closed: 更新连接状态
   */
  function handleMessage(msg: any) {
    switch (msg.type) {
      case 'log':
        // 后端已经根据 paused 状态控制推送，这里直接追加
        if (typeof msg.entry?.seq === 'number') {
          lastSeq.value = Math.max(lastSeq.value, msg.entry.seq)
        }
        appendLog(msg.entry)
        break
      case 'log_batch': {
        // 断线重连补发：按 seq 去重合并，保留已加载历史
        const seen = new Set(
          logs.value.map((l) => l.seq).filter((s) => s != null)
        )
        for (const e of msg.logs || []) {
          if (typeof e.seq === 'number') {
            lastSeq.value = Math.max(lastSeq.value, e.seq)
            if (seen.has(e.seq)) continue
            seen.add(e.seq)
          }
          appendLog(e)
        }
        if (msg.missing > 0) {
          console.warn(`[DebugStore] 断线期间 ${msg.missing} 条日志已不可恢复`)
        }
        break
      }
      case 'shell_stream':
        // PTY 模式：每行立即写入终端（原 execShellWs 的 onStreamLine/
        // shellResolve 死链已随方案 51 F2-10 删除）
        if (shellOutputHandler && msg.line) {
          shellOutputHandler(msg.line)
        }
        break
      case 'error':
        // 错误消息：写入终端
        if (shellOutputHandler && msg.message) {
          shellOutputHandler(`\r\n[Error] ${msg.message}\r\n`)
        }
        console.error('[DebugStore] Error:', msg.message)
        break
      case 'session_closed':
        connected.value = false
        wsConnected.value = false
        break
      case 'subscribed':
        // 已知类型：确认订阅成功，无副作用（不落入 default 告警）
        break
      default:
        console.warn('[DebugStore] Unknown message type:', msg.type)
    }
  }

  return {
    sessionId,
    logs,
    filter,
    connected,
    wsConnected,
    isRecording,
    createSession,
    fetchLogs,
    closeSession,
    appendLog,
    connectWebSocket,
    disconnectWebSocket,
    setFilter,
    setRecording,
    sendInput,
    sendResize,
    setShellOutputHandler,
  }
})
