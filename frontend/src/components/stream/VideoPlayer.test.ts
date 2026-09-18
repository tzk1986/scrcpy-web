/**
 * VideoPlayer Page Visibility 降载测试（方案 17 实施项 4）
 * ==========================================================
 *
 * 覆盖：
 *   - tab 隐藏时停 fps 上报（防后台定时器节流污染自适应码率决策）并停用输入
 *   - 恢复可见后重启上报、重新启用输入
 *   - 卸载后不再响应 visibilitychange
 *   - ?swdecode=1 时向 H264VideoStream 传入 prefer-software
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, shallowMount } from '@vue/test-utils'

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

  class MockH264Stream {
    static instances: MockH264Stream[] = []
    args: unknown[]
    restartGraceUntil = 0
    suspended = false
    stats = {
      frameCount: 0,
      fps: 25,
      lastFrameTime: 0,
      state: 'streaming',
      error: null,
      width: 640,
      height: 480,
      droppedFrames: 0,
    }
    probeStats = { frameCount: 0, lastFrameTime: 0 }
    start = vi.fn()
    stop = vi.fn()
    suspend = vi.fn()
    resume = vi.fn()
    setStateChangeHandler = vi.fn()
    setStatsUpdateHandler = vi.fn()

    constructor(...args: unknown[]) {
      this.args = args
      MockH264Stream.instances.push(this)
    }
  }

  class MockInputController {
    static instances: MockInputController[] = []
    setEnabled = vi.fn()
    sendKey = vi.fn()
    handleMouseDown = vi.fn()
    handleMouseMove = vi.fn()
    handleMouseUp = vi.fn()
    handleTouchStart = vi.fn()
    handleTouchMove = vi.fn()
    handleTouchEnd = vi.fn()

    constructor() {
      MockInputController.instances.push(this)
    }
  }

  class MockVideoStream {
    static instances: MockVideoStream[] = []
    stats = { fps: 0, frameCount: 0 }
    setStateChangeHandler = vi.fn()
    setStatsUpdateHandler = vi.fn()
    start = vi.fn()
    stop = vi.fn()

    constructor() {
      MockVideoStream.instances.push(this)
    }
  }

  return { MockWebSocketService, MockH264Stream, MockInputController, MockVideoStream }
})

vi.mock('@/services/websocket', () => ({ WebSocketService: h.MockWebSocketService }))
vi.mock('@/services/h264VideoStream', () => ({ H264VideoStream: h.MockH264Stream }))
vi.mock('@/services/videoStream', () => ({ VideoStream: h.MockVideoStream }))
vi.mock('@/services/inputController', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/inputController')>()
  return { InputController: h.MockInputController, KeyCode: actual.KeyCode }
})

import VideoPlayer from '@/components/stream/VideoPlayer.vue'

let hiddenFlag = false

function setHidden(value: boolean) {
  hiddenFlag = value
  document.dispatchEvent(new Event('visibilitychange'))
}

function statsReportCount(): number {
  return h.MockWebSocketService.instances[0].send.mock.calls.filter(
    ([m]) => (m as { op?: string }).op === 'stats',
  ).length
}

async function mountPlayer() {
  const wrapper = shallowMount(VideoPlayer, {
    props: { deviceId: 'dev1', deviceWidth: 640, deviceHeight: 480 },
    // 测试不引入 ElementPlus，stub 掉模板中的 el-* 组件消除解析告警
    global: { stubs: { 'el-button': true, 'el-button-group': true } },
  })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  h.MockWebSocketService.instances = []
  h.MockH264Stream.instances = []
  h.MockInputController.instances = []
  h.MockVideoStream.instances = []
  // 走 h264 路径（happy-dom 无 WebCodecs，需 stub 支撑 isWebCodecsSupported）
  vi.stubGlobal('VideoDecoder', class {})
  vi.stubGlobal('EncodedVideoChunk', class {})
  hiddenFlag = false
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => hiddenFlag })
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  history.pushState({}, '', '/')
})

describe('VideoPlayer Page Visibility', () => {
  it('tab 隐藏时停 fps 上报与输入，恢复后重启', async () => {
    vi.useFakeTimers()
    const wrapper = await mountPlayer()
    const ic = h.MockInputController.instances[0]

    await vi.advanceTimersByTimeAsync(2000)
    expect(statsReportCount()).toBe(1)

    setHidden(true)
    await vi.advanceTimersByTimeAsync(2000)
    expect(statsReportCount()).toBe(1)
    expect(ic.setEnabled).toHaveBeenLastCalledWith(false)

    setHidden(false)
    await vi.advanceTimersByTimeAsync(2000)
    expect(statsReportCount()).toBe(2)
    expect(ic.setEnabled).toHaveBeenLastCalledWith(true)

    wrapper.unmount()
  })

  it('卸载后不再响应 visibilitychange', async () => {
    const wrapper = await mountPlayer()
    const ic = h.MockInputController.instances[0]

    wrapper.unmount()
    setHidden(true)

    expect(ic.setEnabled).not.toHaveBeenCalled()
  })

  it('?swdecode=1 时向 H264VideoStream 传入 prefer-software', async () => {
    history.pushState({}, '', '/?swdecode=1')
    const wrapper = await mountPlayer()

    const inst = h.MockH264Stream.instances[0]
    expect(inst.args[2]).toEqual({ hardwareAcceleration: 'prefer-software' })

    wrapper.unmount()
  })
})