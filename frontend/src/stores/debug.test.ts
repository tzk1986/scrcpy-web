/**
 * debug store 测试（方案 17 实施项 5）
 * ======================================
 *
 * 覆盖：
 *   - setRecording 通过 WS filter 消息同步 paused 状态（后端据此启停采集）
 *   - 意外断线自动重连仅在录制开启时生效（未录制不重连、不重启采集）
 *   - 主动断开（disconnectWebSocket/closeSession）永不重连
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useDebugStore } from '@/stores/debug'

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

beforeEach(() => {
  setActivePinia(createPinia())
  h.MockWebSocketService.instances = []
  mockApi.getLogs.mockClear()
  mockApi.getDebugStats.mockClear()
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
})