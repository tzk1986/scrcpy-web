/**
 * debug store 测试
 * ==================
 *
 * 覆盖：
 *   - setRecording 通过 WS filter 消息同步 paused 状态（后端据此启停采集，方案 17 实施项 5）
 *   - 意外断线自动重连仅在录制开启时生效（未录制不重连、不重启采集）
 *   - 主动断开（disconnectWebSocket/closeSession）永不重连
 *   - 会话生命周期：createSession / fetchLogs / execShell / closeSession 及状态重置
 *   - appendLog 缓冲上限（FIFO）与 closeSession 的 lastSeq 重置
 *   - WS 消息处理：log / log_batch（断线补发去重）/ shell_stream / shell_output /
 *     error / session_closed / subscribed / 未知类型 / 非法 JSON
 *   - execShellWs 的 WS 路径与 HTTP 降级、sendInput base64 编码与回车转换、setFilter 同步
 *   - 连接超时（readyState 非 OPEN 时不再订阅并重置 debugWs）
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useDebugStore, type LogEntry } from '@/stores/debug'

const h = vi.hoisted(() => {
  class MockWebSocketService {
    static instances: MockWebSocketService[] = []
    messageHandler: ((data: unknown) => void) | null = null
    closeHandler: (() => void) | null = null
    errorHandler: ((e: unknown) => void) | null = null
    // 模拟原生 WebSocket：readyState=1 即 OPEN，connectWebSocket 的连接等待会立即通过
    ws = { readyState: 1 }
    send = vi.fn()
    connect = vi.fn()
    close = vi.fn()
    url: string

    constructor(url: string) {
      this.url = url
      MockWebSocketService.instances.push(this)
    }
    setMessageHandler(handler: (data: unknown) => void) {
      this.messageHandler = handler
    }
    setCloseHandler(handler: () => void) {
      this.closeHandler = handler
    }
    setErrorHandler(handler: (e: unknown) => void) {
      this.errorHandler = handler
    }
  }
  MockWebSocketService.instances = []
  return { MockWebSocketService }
})

vi.mock('@/services/websocket', () => ({
  WebSocketService: h.MockWebSocketService,
}))

const mockApi = vi.hoisted(() => ({
  getLogs: vi.fn().mockResolvedValue({ logs: [] }),
  getDebugStats: vi.fn().mockResolvedValue({ db_size_mb: 0 }),
  createDebugSession: vi.fn().mockResolvedValue({ session_id: 's1' }),
  closeDebugSession: vi.fn().mockResolvedValue(undefined),
  execShell: vi.fn().mockResolvedValue({ output: '', success: true }),
}))
vi.mock('@/services/api', () => ({ api: mockApi }))

function lastInstance() {
  return h.MockWebSocketService.instances.at(-1)!
}

/** 最近一个 mock WS 实例收到的全部消息。 */
function sentOps() {
  return lastInstance().send.mock.calls.map(([m]) => m)
}

/** 通过 mock WS 的 messageHandler 投递一条消息（模拟服务端推送）。 */
function emit(payload: unknown) {
  lastInstance().messageHandler!(payload)
}

function entry(seq: number, message = `log-${seq}`): LogEntry {
  return { ts: seq, level: 'I', pid: 1, tid: 1, tag: 't', message, seq }
}

beforeEach(() => {
  setActivePinia(createPinia())
  h.MockWebSocketService.instances = []
  mockApi.getLogs.mockClear()
  mockApi.getDebugStats.mockClear()
  mockApi.createDebugSession.mockClear()
  mockApi.closeDebugSession.mockClear()
  mockApi.execShell.mockClear()
  // 静默 store 内部日志（产生大量噪音），同时便于断言错误分支
  vi.spyOn(console, 'log').mockImplementation(() => {})
  vi.spyOn(console, 'error').mockImplementation(() => {})
  vi.spyOn(console, 'warn').mockImplementation(() => {})
})

afterEach(() => {
  vi.mocked(console.log).mockRestore()
  vi.mocked(console.error).mockRestore()
  vi.mocked(console.warn).mockRestore()
})

async function connectStore(recording: boolean) {
  const store = useDebugStore()
  store.sessionId = 'sess-1'
  store.setRecording(recording)
  await store.connectWebSocket()
  return store
}

describe('debug store', () => {
  it('连接时按录制状态发送 paused，setRecording 同步 paused 消息', async () => {
    const store = await connectStore(false)
    const filterMsgs = lastInstance()
      .send.mock.calls.map(([m]) => m)
      .filter((m: any) => m.op === 'filter')
    // 默认暂停录制：paused=true，后端不启动采集
    expect(filterMsgs.at(-1)).toMatchObject({ paused: true })

    store.setRecording(true)
    const afterOn = lastInstance()
      .send.mock.calls.map(([m]) => m)
      .filter((m: any) => m.op === 'filter')
    expect(afterOn.at(-1)).toMatchObject({ paused: false })

    store.setRecording(false)
    const afterOff = lastInstance()
      .send.mock.calls.map(([m]) => m)
      .filter((m: any) => m.op === 'filter')
    expect(afterOff.at(-1)).toMatchObject({ paused: true })
  })

  it('录制开启时意外断线自动重连', async () => {
    const store = await connectStore(true)
    vi.useFakeTimers()
    try {
      lastInstance().closeHandler!()
      expect(store.wsConnected).toBe(false)
      const before = h.MockWebSocketService.instances.length
      vi.advanceTimersByTime(2000)
      expect(h.MockWebSocketService.instances.length).toBe(before + 1)
    } finally {
      vi.useRealTimers()
    }
  })

  it('未录制时意外断线不重连（不重启后端采集）', async () => {
    const store = await connectStore(false)
    vi.useFakeTimers()
    try {
      lastInstance().closeHandler!()
      expect(store.wsConnected).toBe(false)
      const before = h.MockWebSocketService.instances.length
      vi.advanceTimersByTime(5000)
      expect(h.MockWebSocketService.instances.length).toBe(before)
    } finally {
      vi.useRealTimers()
    }
  })

  it('主动断开后永不重连', async () => {
    const store = await connectStore(true)
    vi.useFakeTimers()
    try {
      await store.disconnectWebSocket()
      lastInstance().closeHandler!()
      const before = h.MockWebSocketService.instances.length
      vi.advanceTimersByTime(5000)
      expect(h.MockWebSocketService.instances.length).toBe(before)
    } finally {
      vi.useRealTimers()
    }
  })

  it('录制中但会话已清空时意外断线不重连', async () => {
    const store = await connectStore(true)
    store.sessionId = null
    vi.useFakeTimers()
    try {
      lastInstance().closeHandler!()
      const before = h.MockWebSocketService.instances.length
      vi.advanceTimersByTime(5000)
      expect(h.MockWebSocketService.instances.length).toBe(before)
    } finally {
      vi.useRealTimers()
    }
  })
})

describe('debug store - 会话生命周期', () => {
  it('createSession 设置 sessionId/connected 并返回后端结果', async () => {
    const store = useDebugStore()
    const res = await store.createSession('dev-1', 'user-1')
    expect(mockApi.createDebugSession).toHaveBeenCalledWith('dev-1', 'user-1')
    expect(store.sessionId).toBe('s1')
    expect(store.connected).toBe(true)
    expect(res).toEqual({ session_id: 's1' })
  })

  it('fetchLogs 无会话时早退（不调用 API）', async () => {
    const store = useDebugStore()
    await store.fetchLogs()
    expect(mockApi.getLogs).not.toHaveBeenCalled()
  })

  it('fetchLogs 携带过滤条件与 limit，覆盖日志缓冲', async () => {
    const store = useDebugStore()
    store.sessionId = 'sess-1'
    store.setFilter('E', 'Tag1')
    mockApi.getLogs.mockResolvedValueOnce({ logs: [entry(1)] })
    await store.fetchLogs(200)
    expect(mockApi.getLogs).toHaveBeenCalledWith('sess-1', 'E', 'Tag1', 200)
    expect(store.logs.map((l) => l.seq)).toEqual([1])
  })

  it('execShell 无会话时早退，有会话时转发命令并返回结果', async () => {
    const store = useDebugStore()
    expect(await store.execShell('ls')).toBeUndefined()
    expect(mockApi.execShell).not.toHaveBeenCalled()

    store.sessionId = 'sess-1'
    mockApi.execShell.mockResolvedValueOnce({ output: 'ok', success: true })
    expect(await store.execShell('ls -l')).toEqual({ output: 'ok', success: true })
    expect(mockApi.execShell).toHaveBeenCalledWith('sess-1', 'ls -l')
  })

  it('appendLog 超过 50000 条时 FIFO 淘汰最旧记录', () => {
    const store = useDebugStore()
    for (let i = 0; i <= 50000; i++) {
      store.appendLog(entry(i))
    }
    expect(store.logs).toHaveLength(50000)
    expect(store.logs[0].seq).toBe(1)
    expect(store.logs.at(-1)!.seq).toBe(50000)
  })

  it('closeSession 断开 WS、关闭后端会话并重置全部状态', async () => {
    const store = await connectStore(true)
    store.connected = true
    store.appendLog(entry(10))
    await store.closeSession()

    expect(lastInstance().close).toHaveBeenCalled()
    expect(mockApi.closeDebugSession).toHaveBeenCalledWith('sess-1')
    expect(store.sessionId).toBeNull()
    expect(store.logs).toEqual([])
    expect(store.connected).toBe(false)
    expect(store.wsConnected).toBe(false)
  })

  it('closeSession 重置 lastSeq：新会话订阅不带 from_seq', async () => {
    const store = await connectStore(true)
    emit(JSON.stringify({ type: 'log', entry: entry(7) }))
    await store.closeSession()

    store.sessionId = 'sess-2'
    await store.connectWebSocket()
    const sub = sentOps().find((m: any) => m.op === 'subscribe')
    expect(sub).toEqual({ op: 'subscribe' })
  })

  it('closeSession 无会话时不调用关闭 API', async () => {
    const store = useDebugStore()
    await store.closeSession()
    expect(mockApi.closeDebugSession).not.toHaveBeenCalled()
  })
})

describe('debug store - WebSocket 连接', () => {
  it('无 sessionId 不建连；已连接时重复调用直接返回', async () => {
    const store = useDebugStore()
    await store.connectWebSocket()
    expect(h.MockWebSocketService.instances).toHaveLength(0)

    store.sessionId = 'sess-1'
    await store.connectWebSocket()
    expect(store.wsConnected).toBe(true)
    await store.connectWebSocket()
    expect(h.MockWebSocketService.instances).toHaveLength(1)
  })

  it('断线续传：重连订阅携带 from_seq = lastSeq + 1', async () => {
    const store = await connectStore(true)
    emit(JSON.stringify({ type: 'log', entry: entry(3) }))
    emit(JSON.stringify({ type: 'log', entry: entry(1) })) // 乱序（旧 seq 不回退）

    await store.disconnectWebSocket()
    await store.connectWebSocket()
    const sub = sentOps().find((m: any) => m.op === 'subscribe')
    expect(sub).toEqual({ op: 'subscribe', from_seq: 4 })
  })

  it('https 页面使用 wss 协议与当前 host', async () => {
    const original = window.location
    Object.defineProperty(window, 'location', {
      value: { protocol: 'https:', host: 'example.com' },
      configurable: true,
    })
    try {
      const store = useDebugStore()
      store.sessionId = 'sess-1'
      await store.connectWebSocket()
      expect(lastInstance().url).toBe('wss://example.com/ws/debug/sess-1')
    } finally {
      Object.defineProperty(window, 'location', { value: original, configurable: true })
    }
  })

  it('WS error 回调透传错误日志', async () => {
    await connectStore(false)
    const err = new Error('socket boom')
    lastInstance().errorHandler!(err)
    expect(console.error).toHaveBeenCalledWith('[DebugStore] WebSocket error:', err)
  })

  it('连接超时（readyState 非 OPEN）不订阅并重置 debugWs', async () => {
    vi.useFakeTimers()
    try {
      const store = useDebugStore()
      store.sessionId = 'sess-1'
      const p = store.connectWebSocket()
      // 连接阶段保持 CONNECTING，轮询始终失败，等待 10s 超时保护
      lastInstance().ws.readyState = 0
      await vi.advanceTimersByTimeAsync(10100)
      await p

      expect(store.wsConnected).toBe(false)
      expect(lastInstance().send).not.toHaveBeenCalled()
      expect(console.error).toHaveBeenCalledWith('[DebugStore] WebSocket connection timeout')

      // debugWs 已重置：恢复连接能力后重新连接会新建实例
      lastInstance().ws.readyState = 1
      const p2 = store.connectWebSocket()
      await vi.advanceTimersByTimeAsync(200)
      await p2
      expect(h.MockWebSocketService.instances).toHaveLength(2)
      expect(store.wsConnected).toBe(true)
    } finally {
      vi.useRealTimers()
    }
  })
})

describe('debug store - 消息处理', () => {
  it('log 消息追加日志并更新 lastSeq', async () => {
    const store = await connectStore(true)
    emit(JSON.stringify({ type: 'log', entry: entry(1) }))
    emit(JSON.stringify({ type: 'log', entry: { ...entry(2), seq: undefined } }))
    expect(store.logs).toHaveLength(2)
    expect(store.logs[0].seq).toBe(1)
  })

  it('log_batch 按 seq 去重合并并告警不可恢复日志数', async () => {
    const store = await connectStore(true)
    emit(JSON.stringify({ type: 'log', entry: entry(5) }))

    const noSeq: LogEntry = { ts: 9, level: 'I', pid: 1, tid: 1, tag: 't', message: 'no-seq' }
    emit(
      JSON.stringify({
        type: 'log_batch',
        logs: [entry(5), entry(6), entry(6), noSeq],
        missing: 2,
      })
    )

    // 5 已存在被去重；6 只追加一次；无 seq 的条目不去重直接追加
    expect(store.logs.map((l) => l.seq ?? 'no-seq')).toEqual([5, 6, 'no-seq'])
    expect(console.warn).toHaveBeenCalledWith('[DebugStore] 断线期间 2 条日志已不可恢复')
  })

  it('log_batch 缺省 logs/missing 字段时安全处理', async () => {
    const store = await connectStore(true)
    emit(JSON.stringify({ type: 'log_batch' }))
    emit(JSON.stringify({ type: 'log_batch', logs: [], missing: 0 }))
    expect(store.logs).toEqual([])
    expect(console.warn).not.toHaveBeenCalled()
  })

  it('shell_stream / shell_output 驱动 execShellWs 流式输出', async () => {
    const store = await connectStore(true)
    const lines: string[] = []
    const terminal: string[] = []
    store.setShellOutputHandler((l) => terminal.push(l))

    const p = store.execShellWs('ls -l', (l) => lines.push(l))
    expect(sentOps().at(-1)).toEqual({ op: 'exec', command: 'ls -l' })

    emit(JSON.stringify({ type: 'shell_stream', line: 'a.txt' }))
    expect(lines).toEqual(['a.txt'])
    expect(terminal).toEqual(['a.txt'])

    emit(JSON.stringify({ type: 'shell_output', output: 'a.txt\n', success: true }))
    await expect(p).resolves.toEqual({ output: 'a.txt\n', success: true })
  })

  it('shell_output 缺省 output 为空串，并透传 success=false', async () => {
    const store = await connectStore(true)
    const p = store.execShellWs('false')
    emit(JSON.stringify({ type: 'shell_output', success: false }))
    await expect(p).resolves.toEqual({ output: '', success: false })
  })

  it('shell_stream 无任何 handler 时不报错', async () => {
    await connectStore(true)
    emit(JSON.stringify({ type: 'shell_stream', line: 'orphan' }))
    expect(console.error).not.toHaveBeenCalled()
  })

  it('error 消息写入终端（有/无 handler 均安全）', async () => {
    const store = await connectStore(true)
    emit(JSON.stringify({ type: 'error', message: 'before-handler' }))
    expect(console.error).toHaveBeenCalledWith('[DebugStore] Error:', 'before-handler')

    const terminal: string[] = []
    store.setShellOutputHandler((l) => terminal.push(l))
    emit(JSON.stringify({ type: 'error', message: 'boom' }))
    expect(terminal).toEqual(['\r\n[Error] boom\r\n'])
  })

  it('session_closed 重置连接状态并拒绝 pending 的 shell 命令', async () => {
    const store = await connectStore(true)
    const p = store.execShellWs('sleep 100')
    emit(JSON.stringify({ type: 'session_closed' }))
    await expect(p).resolves.toEqual({ output: 'Session closed', success: false })
    expect(store.connected).toBe(false)
    expect(store.wsConnected).toBe(false)
  })

  it('session_closed 无 pending 命令时不报错', async () => {
    const store = await connectStore(true)
    emit(JSON.stringify({ type: 'session_closed' }))
    expect(store.wsConnected).toBe(false)
  })

  it('subscribed 与未知类型消息只记录日志', async () => {
    await connectStore(true)
    emit(JSON.stringify({ type: 'subscribed', session_id: 'sess-1' }))
    emit(JSON.stringify({ type: 'who-knows' }))
    expect(console.log).toHaveBeenCalledWith('Subscribed to session:', 'sess-1')
    expect(console.log).toHaveBeenCalledWith('Unknown message type:', 'who-knows')
  })

  it('非法 JSON 与非字符串消息被安全忽略', async () => {
    await connectStore(true)
    emit('{not-json')
    expect(console.error).toHaveBeenCalledWith(
      'Failed to parse WebSocket message:',
      expect.anything()
    )
    emit(new ArrayBuffer(4))
    emit(JSON.stringify({ type: 'log' })) // entry 缺失：appendLog(undefined) 前不校验，此处仅确认不抛
    expect(console.error).toHaveBeenCalledTimes(1)
  })
})

describe('debug store - 命令与过滤', () => {
  it('execShellWs 未连接时降级 HTTP：成功与失败两分支', async () => {
    const store = useDebugStore()
    store.sessionId = 'sess-1'

    mockApi.execShell.mockResolvedValueOnce({ output: 'http-out', success: true })
    await expect(store.execShellWs('ls')).resolves.toEqual({ output: 'http-out', success: true })

    mockApi.execShell.mockResolvedValueOnce(null)
    await expect(store.execShellWs('ls')).resolves.toEqual({
      output: 'Command failed',
      success: false,
    })
  })

  it('sendInput 未连接时告警不发送；已连接时 base64 编码', async () => {
    const store = useDebugStore()
    store.sendInput('ignored')
    expect(console.warn).toHaveBeenCalledWith(
      '[DebugStore] WebSocket not connected, cannot send input'
    )
    expect(h.MockWebSocketService.instances).toHaveLength(0)

    const connected = await connectStore(false)
    connected.sendInput('ls')
    expect(sentOps().at(-1)).toEqual({ op: 'input', data: btoa('ls') })

    connected.sendInput('\r') // 回车转换为换行（设备 PTY 未设 ICRNL）
    expect(sentOps().at(-1)).toEqual({ op: 'input', data: btoa('\n') })
  })

  it('setFilter 未连接仅更新本地状态，已连接同步 filter 消息', async () => {
    const store = useDebugStore()
    store.setFilter('W', 'T')
    expect(store.filter).toEqual({ level: 'W', tag: 'T' })

    const connected = await connectStore(false)
    connected.setFilter('E', 'Tag1')
    expect(connected.filter).toEqual({ level: 'E', tag: 'Tag1' })
    expect(sentOps().at(-1)).toEqual({ op: 'filter', level: 'E', tag: 'Tag1', paused: true })
  })
})