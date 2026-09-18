/**
 * LogcatView 组件测试（方案 17 实施项 5）
 * =========================================
 *
 * 覆盖：
 *   - 挂载时不发起任何网络请求（录制默认暂停，后端 logcat 采集保持关闭）
 *   - 开启录制后才连接 WS（filter 消息 paused=false 触发后端采集）并拉历史
 *   - 暂停录制后断开 WS（彻底省连接与后端采集）
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, shallowMount } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'

const h = vi.hoisted(() => {
  class MockWebSocketService {
    static instances: MockWebSocketService[] = []
    messageHandler: ((data: unknown) => void) | null = null
    closeHandler: (() => void) | null = null
    errorHandler: ((e: unknown) => void) | null = null
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

import LogcatView from './LogcatView.vue'
import { useDebugStore } from '@/stores/debug'

let pinia: Pinia

function mountView() {
  return shallowMount(LogcatView, {
    props: { deviceId: 'dev1' },
    global: { plugins: [pinia] },
  })
}

beforeEach(() => {
  pinia = createPinia()
  setActivePinia(pinia)
  h.MockWebSocketService.instances = []
  mockApi.getLogs.mockClear()
  mockApi.getDebugStats.mockClear()
})

describe('LogcatView', () => {
  it('挂载时不发起任何网络请求', async () => {
    const store = useDebugStore()
    store.sessionId = 'sess-1'
    mountView()
    await flushPromises()

    expect(h.MockWebSocketService.instances.length).toBe(0)
    expect(mockApi.getLogs).not.toHaveBeenCalled()
    expect(mockApi.getDebugStats).not.toHaveBeenCalled()
    expect(store.isRecording).toBe(false)
  })

  it('开启录制后连接 WS（paused=false 触发后端采集）并拉历史', async () => {
    const store = useDebugStore()
    store.sessionId = 'sess-1'
    mountView()

    store.setRecording(true)
    // connectWebSocket 内部用 setInterval 轮询 readyState，需等待真实 timer
    await vi.waitFor(() => {
      expect(h.MockWebSocketService.instances.length).toBe(1)
      const inst = h.MockWebSocketService.instances[0]
      const filterMsgs = inst.send.mock.calls
        .map(([m]) => m as any)
        .filter((m) => m.op === 'filter')
      expect(filterMsgs.at(-1)).toMatchObject({ paused: false })
    })
    await vi.waitFor(() => expect(mockApi.getLogs).toHaveBeenCalled())
    expect(mockApi.getDebugStats).toHaveBeenCalled()
  })

  it('暂停录制后断开 WS', async () => {
    const store = useDebugStore()
    store.sessionId = 'sess-1'
    mountView()

    store.setRecording(true)
    await flushPromises()
    const inst = h.MockWebSocketService.instances[0]

    store.setRecording(false)
    await flushPromises()

    expect(inst.close).toHaveBeenCalled()
    expect(store.wsConnected).toBe(false)
  })
})