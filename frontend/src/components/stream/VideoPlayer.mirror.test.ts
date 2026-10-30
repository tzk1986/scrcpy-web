/**
 * VideoPlayer 方案 55 剪贴板单向镜像（设备→PC）接线测试
 * =====================================================
 *
 * 覆盖：
 *   - 开关默认关、ws 未就绪禁用；勾选/取消发送 clipboard_mirror action
 *   - clipboard_push 有焦点 → writeText 直写并提示同步长度
 *   - 失焦 push → 缓存+角标提示，回焦（window focus）补写一次即清
 *   - clipboard_mirror(enabled=false) 后端自停 → 开关复位、后续 push 忽略
 *   - 关闭开关后 push 忽略；clipboard_push 不触碰手动应答通道
 *   - ws 断连（closeHandler）→ 会话级复位
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import { flushPromises, shallowMount, type VueWrapper } from '@vue/test-utils'

const h = vi.hoisted(() => {
  class MockWebSocketService {
    static instances: MockWebSocketService[] = []
    messageHandler: ((data: unknown) => void) | null = null
    closeHandler: (() => void) | null = null
    errorHandler: ((e: unknown) => void) | null = null
    openHandler: (() => void) | null = null
    reconnectHandler: (() => void) | null = null
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
    setOpenHandler(handler: () => void) {
      this.openHandler = handler
    }
    setReconnectHandler(handler: () => void) {
      this.reconnectHandler = handler
    }
  }

  class MockH264Stream {
    static instances: MockH264Stream[] = []
    restartGraceUntil = 0
    suspended = false
    state = 'streaming'
    stats = { fps: 0, frameCount: 0, droppedFrames: 0, lastFrameTime: 0, state: 'streaming', error: null as string | null }
    probeStats = { frameCount: 0, lastFrameTime: 0 }
    start = vi.fn()
    stop = vi.fn()
    suspend = vi.fn()
    resume = vi.fn()
    setStateChangeHandler = vi.fn()
    setStatsUpdateHandler = vi.fn()
    captureFrame = vi.fn(() => Promise.resolve(new Blob(['frame'])))

    constructor() {
      MockH264Stream.instances.push(this)
    }
  }

  class MockInputController {
    static instances: MockInputController[] = []
    setEnabled = vi.fn()
    setInputHandler = vi.fn()
    sendKey = vi.fn()
    sendText = vi.fn()
    handleMouseDown = vi.fn()
    handleMouseMove = vi.fn()
    handleMouseUp = vi.fn()
    handleTouchStart = vi.fn()
    handleTouchMove = vi.fn()
    handleTouchEnd = vi.fn()
    handleKeyDown = vi.fn()
    handleKeyUp = vi.fn()
    releaseAllKeys = vi.fn()
    handleWheel = vi.fn()
    setComposing = vi.fn()
    handleCompositionEnd = vi.fn()
    setPreferText = vi.fn()
    sendClipboardSet = vi.fn()
    sendClipboardMirror = vi.fn()
    requestClipboard = vi.fn()
    handleClipboardMessage = vi.fn()

    constructor() {
      MockInputController.instances.push(this)
    }
  }

  class MockVideoStream {
    static instances: MockVideoStream[] = []
    stats = { fps: 0, frameCount: 0, error: null as string | null }
    setStateChangeHandler = vi.fn()
    setStatsUpdateHandler = vi.fn()
    start = vi.fn()
    stop = vi.fn()
    notifyInput = vi.fn()

    constructor() {
      MockVideoStream.instances.push(this)
    }
  }

  const MockApi = { screenshot: vi.fn() }
  const ElMessage = { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }
  const writeText = vi.fn(() => Promise.resolve())

  return { MockWebSocketService, MockH264Stream, MockInputController, MockVideoStream, MockApi, ElMessage, writeText }
})

vi.mock('@/services/websocket', () => ({ WebSocketService: h.MockWebSocketService }))
vi.mock('@/services/h264VideoStream', () => ({ H264VideoStream: h.MockH264Stream }))
vi.mock('@/services/videoStream', () => ({ VideoStream: h.MockVideoStream }))
vi.mock('@/services/api', () => ({ api: h.MockApi }))
vi.mock('element-plus', () => ({ ElMessage: h.ElMessage }))
vi.mock('@/services/inputController', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/inputController')>()
  return { InputController: h.MockInputController, KeyCode: actual.KeyCode }
})

import VideoPlayer from '@/components/stream/VideoPlayer.vue'

const ElDialogStub = {
  name: 'ElDialog',
  props: ['modelValue'],
  emits: ['update:modelValue'],
  template: '<div v-if="modelValue" class="stub-dialog"><slot /></div>',
}
const ElButtonStub = {
  name: 'ElButton',
  props: ['disabled'],
  template: '<button class="stub-btn" type="button" :disabled="disabled"><slot /></button>',
}

let hiddenFlag = false
let focusFlag = true

async function mountPlayer(): Promise<VueWrapper> {
  const wrapper = shallowMount(VideoPlayer, {
    props: { deviceId: 'dev1', deviceWidth: 640, deviceHeight: 480 },
    global: {
      renderStubDefaultSlot: true,
      stubs: { 'el-button': ElButtonStub, 'el-button-group': true, 'el-dialog': ElDialogStub },
    },
  })
  await flushPromises()
  return wrapper
}

function openWs() {
  h.MockWebSocketService.instances[0].openHandler!()
}

function feedWs(msg: Record<string, unknown>) {
  const wsInst = h.MockWebSocketService.instances[0]
  // 模拟 h264 侧注册（键盘测试同款）：经包装通道安装截流 handler
  if (!wsInst.messageHandler) wsInst.setMessageHandler(vi.fn())
  wsInst.messageHandler!(JSON.stringify(msg))
}

function mirrorCheckbox(wrapper: VueWrapper) {
  return wrapper.find('.mirror-toggle input[type="checkbox"]')
}

async function enableMirror(wrapper: VueWrapper) {
  openWs()
  await nextTick()
  await mirrorCheckbox(wrapper).setValue(true)
  await nextTick()
}

beforeEach(() => {
  h.MockWebSocketService.instances = []
  h.MockH264Stream.instances = []
  h.MockInputController.instances = []
  h.MockVideoStream.instances = []
  h.MockApi.screenshot.mockReset()
  h.writeText.mockReset().mockResolvedValue(undefined)
  localStorage.clear()
  vi.stubGlobal('VideoDecoder', class {})
  vi.stubGlobal('EncodedVideoChunk', class {})
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value: { writeText: h.writeText },
  })
  hiddenFlag = false
  focusFlag = true
  vi.spyOn(document, 'hasFocus').mockImplementation(() => focusFlag)
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => hiddenFlag })
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('方案55 镜像开关与 action', () => {
  it('默认关；ws 未就绪禁用；勾选/取消发送 clipboard_mirror action', async () => {
    const wrapper = await mountPlayer()
    const box = mirrorCheckbox(wrapper)
    expect((box.element as HTMLInputElement).checked).toBe(false)
    expect((box.element as HTMLInputElement).disabled).toBe(true)
    openWs()
    await nextTick()
    expect((mirrorCheckbox(wrapper).element as HTMLInputElement).disabled).toBe(false)
    await box.setValue(true)
    expect(h.MockInputController.instances[0].sendClipboardMirror).toHaveBeenLastCalledWith(true)
    await box.setValue(false)
    expect(h.MockInputController.instances[0].sendClipboardMirror).toHaveBeenLastCalledWith(false)
    wrapper.unmount()
  })

  it('ws 断连会话级复位（镜像中 closeHandler → 开关关）', async () => {
    const wrapper = await mountPlayer()
    await enableMirror(wrapper)
    expect((mirrorCheckbox(wrapper).element as HTMLInputElement).checked).toBe(true)
    h.MockWebSocketService.instances[0].closeHandler!()
    await nextTick()
    expect((mirrorCheckbox(wrapper).element as HTMLInputElement).checked).toBe(false)
    wrapper.unmount()
  })
})

describe('方案55 clipboard_push 焦点策略', () => {
  it('有焦点 → writeText 直写并提示同步长度；不触碰手动应答通道', async () => {
    const wrapper = await mountPlayer()
    await enableMirror(wrapper)
    feedWs({ type: 'clipboard_push', text: '设备复制ABC' })
    await flushPromises()
    expect(h.writeText).toHaveBeenCalledWith('设备复制ABC')
    expect(wrapper.find('.mirror-badge').text()).toContain('7 字符')
    expect(h.MockInputController.instances[0].handleClipboardMessage).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('失焦 → 缓存不写；回焦补写一次即清，同文不再重复', async () => {
    const wrapper = await mountPlayer()
    await enableMirror(wrapper)
    focusFlag = false
    feedWs({ type: 'clipboard_push', text: '后台复制' })
    await flushPromises()
    expect(h.writeText).not.toHaveBeenCalled()
    expect(wrapper.find('.mirror-badge').text()).toContain('回焦自动同步')
    focusFlag = true
    window.dispatchEvent(new Event('focus'))
    await flushPromises()
    expect(h.writeText).toHaveBeenCalledWith('后台复制')
    wrapper.unmount()
  })

  it('开关关闭后 push 忽略（零 writeText）', async () => {
    const wrapper = await mountPlayer()
    await enableMirror(wrapper)
    await mirrorCheckbox(wrapper).setValue(false)
    feedWs({ type: 'clipboard_push', text: '不应出现' })
    await flushPromises()
    expect(h.writeText).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('writeText 被拒（异常）→ 转缓存提示，不抛错不断输入面', async () => {
    h.writeText.mockRejectedValueOnce(new Error('NotAllowedError'))
    const wrapper = await mountPlayer()
    await enableMirror(wrapper)
    feedWs({ type: 'clipboard_push', text: '拒写内容' })
    await flushPromises()
    expect(wrapper.find('.mirror-badge').text()).toContain('写入被拒')
    wrapper.unmount()
  })
})

describe('方案55 后端自停复位', () => {
  it('clipboard_mirror enabled=false → 开关复位，其后 push 忽略', async () => {
    const wrapper = await mountPlayer()
    await enableMirror(wrapper)
    feedWs({ type: 'clipboard_mirror', enabled: false, reason: 'poll_failed' })
    await nextTick()
    expect((mirrorCheckbox(wrapper).element as HTMLInputElement).checked).toBe(false)
    expect(wrapper.find('.mirror-badge').text()).toContain('自动停止')
    feedWs({ type: 'clipboard_push', text: '已停用' })
    await flushPromises()
    expect(h.writeText).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('手动 clipboard 应答帧仍走原通道（回归保护）', async () => {
    const wrapper = await mountPlayer()
    const ic = h.MockInputController.instances[0]
    feedWs({ type: 'clipboard', text: 'manual' })
    expect(ic.handleClipboardMessage).toHaveBeenCalledWith({ type: 'clipboard', text: 'manual' })
    wrapper.unmount()
  })
})
