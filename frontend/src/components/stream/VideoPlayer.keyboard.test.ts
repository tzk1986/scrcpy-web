/**
 * VideoPlayer 方案 54 键盘捕获与剪贴板接线测试
 * =============================================
 *
 * 覆盖：
 *   - D1 点击视频区聚焦隐藏输入（角标出现/失焦补 up/角标点击退出）
 *   - keydown 镜像转发与浏览器保留键放行（Ctrl+R、Meta、F5/F12 等）
 *   - Esc×2（800ms 窗）退出捕获，首 Esc 照常注入
 *   - IME composition 事件与 paste → clipboard_set(paste=true)
 *   - 滚轮 → handleWheel 且拦截页面滚动
 *   - clipboard/clipboard_error JSON 消息截流（其余原样转发）
 *   - 输入文本对话框：D4 禁用、注入/粘贴到设备/取剪贴板回填
 *   - D2 prefer-text checkbox 持久化（localStorage osc.keyboardPreferText）
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

  return { MockWebSocketService, MockH264Stream, MockInputController, MockVideoStream, MockApi, ElMessage }
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

/** 与 ScreenshotPreview.test 同款：v-if 控制 slot 渲染，未打开不产生多余按钮 */
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

async function mountPlayer() {
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

/** 经 KeyboardEvent 构造器设 code/key/修饰位（readonly 属性不能 Object.assign） */
function fireKey(el: Element, type: 'keydown' | 'keyup', init: KeyboardEventInit): KeyboardEvent {
  const e = new KeyboardEvent(type, { bubbles: true, cancelable: true, ...init })
  el.dispatchEvent(e)
  return e
}

/** focus/blur/composition/paste 等替身事件：Event + 附加属性 */
function fire(el: Element, type: string, props: Record<string, unknown> = {}): Event {
  const e = new Event(type, { bubbles: true, cancelable: true })
  Object.assign(e, props)
  el.dispatchEvent(e)
  return e
}

function captureEl(wrapper: VueWrapper): Element {
  return wrapper.find('input.keyboard-capture').element
}

function buttonByText(wrapper: VueWrapper, text: string) {
  const btn = wrapper.findAll('button').find((b) => b.text().includes(text))
  if (!btn) throw new Error(`button not found: ${text}`)
  return btn
}

async function openDialog(wrapper: VueWrapper) {
  h.MockWebSocketService.instances[0].openHandler!()
  await nextTick()
  await buttonByText(wrapper, '文本').trigger('click')
  await nextTick()
}

beforeEach(() => {
  h.MockWebSocketService.instances = []
  h.MockH264Stream.instances = []
  h.MockInputController.instances = []
  h.MockVideoStream.instances = []
  h.MockApi.screenshot.mockReset()
  h.ElMessage.success.mockClear()
  h.ElMessage.error.mockClear()
  h.ElMessage.warning.mockClear()
  h.ElMessage.info.mockClear()
  localStorage.clear()
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

describe('方案54 D1 点击视频区捕获键盘', () => {
  it('canvas mousedown 转发点击并聚焦捕获输入（D1=A；happy-dom focus() 不派发事件，角标见下例）', async () => {
    const wrapper = await mountPlayer()
    const ic = h.MockInputController.instances[0]
    const focusSpy = vi.spyOn(captureEl(wrapper) as HTMLInputElement, 'focus')
    await wrapper.find('canvas').trigger('mousedown')
    expect(ic.handleMouseDown).toHaveBeenCalled()
    expect(focusSpy).toHaveBeenCalled()
    wrapper.unmount()
  })

  it('失焦：角标消失并补发所有在按下键 up', async () => {
    const wrapper = await mountPlayer()
    const ic = h.MockInputController.instances[0]
    fire(captureEl(wrapper), 'focus')
    await nextTick()
    expect(wrapper.find('.keyboard-badge').exists()).toBe(true)
    fire(captureEl(wrapper), 'blur')
    await nextTick()
    expect(wrapper.find('.keyboard-badge').exists()).toBe(false)
    expect(ic.releaseAllKeys).toHaveBeenCalled()
    wrapper.unmount()
  })

  it('点击角标退出捕获', async () => {
    const wrapper = await mountPlayer()
    const ic = h.MockInputController.instances[0]
    fire(captureEl(wrapper), 'focus')
    await nextTick()
    await wrapper.find('.keyboard-badge').trigger('click')
    await nextTick()
    expect(ic.releaseAllKeys).toHaveBeenCalled()
    expect(wrapper.find('.keyboard-badge').exists()).toBe(false)
    wrapper.unmount()
  })
})

describe('方案54 keydown 镜像与浏览器保留键', () => {
  it('普通键 preventDefault 并转发 handleKeyDown', async () => {
    const wrapper = await mountPlayer()
    const ic = h.MockInputController.instances[0]
    const e = fireKey(captureEl(wrapper), 'keydown', { code: 'KeyA', key: 'a' })
    expect(e.defaultPrevented).toBe(true)
    expect(ic.handleKeyDown).toHaveBeenCalledTimes(1)
    expect(ic.handleKeyDown.mock.calls[0][0].code).toBe('KeyA')
    wrapper.unmount()
  })

  it('keyup 转发 handleKeyUp', async () => {
    const wrapper = await mountPlayer()
    const ic = h.MockInputController.instances[0]
    fireKey(captureEl(wrapper), 'keyup', { code: 'KeyA', key: 'a' })
    expect(ic.handleKeyUp).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it.each([
    ['Ctrl+R', { code: 'KeyR', ctrlKey: true }],
    ['Ctrl+W', { code: 'KeyW', ctrlKey: true }],
    ['Ctrl+Shift+I', { code: 'KeyI', ctrlKey: true, shiftKey: true }],
    ['F5', { code: 'F5' }],
    ['F12', { code: 'F12' }],
    ['Meta+S', { code: 'KeyS', metaKey: true }],
  ])('%s 放行浏览器原生行为（不 preventDefault 不注入）', async (_label, init) => {
    const wrapper = await mountPlayer()
    const ic = h.MockInputController.instances[0]
    const e = fireKey(captureEl(wrapper), 'keydown', init)
    expect(e.defaultPrevented).toBe(false)
    expect(ic.handleKeyDown).not.toHaveBeenCalled()
    wrapper.unmount()
  })
})

describe('方案54 Esc 双击退出捕获', () => {
  it('800ms 内连按两次 Esc：首个注入、第二个退出捕获不注入', async () => {
    vi.useFakeTimers()
    const wrapper = await mountPlayer()
    const ic = h.MockInputController.instances[0]
    const capture = captureEl(wrapper)
    fire(capture, 'focus')
    await nextTick()

    fireKey(capture, 'keydown', { code: 'Escape', key: 'Escape' })
    expect(ic.handleKeyDown).toHaveBeenCalledTimes(1)
    fireKey(capture, 'keydown', { code: 'Escape', key: 'Escape' })
    expect(ic.handleKeyDown).toHaveBeenCalledTimes(1)
    expect(ic.releaseAllKeys).toHaveBeenCalled()
    await nextTick()
    expect(wrapper.find('.keyboard-badge').exists()).toBe(false)
    wrapper.unmount()
  })

  it('间隔超过 800ms 的两次 Esc 均注入且不退出', async () => {
    vi.useFakeTimers()
    const wrapper = await mountPlayer()
    const ic = h.MockInputController.instances[0]
    const capture = captureEl(wrapper)
    fire(capture, 'focus')
    await nextTick()

    fireKey(capture, 'keydown', { code: 'Escape', key: 'Escape' })
    vi.advanceTimersByTime(900)
    fireKey(capture, 'keydown', { code: 'Escape', key: 'Escape' })
    expect(ic.handleKeyDown).toHaveBeenCalledTimes(2)
    await nextTick()
    expect(wrapper.find('.keyboard-badge').exists()).toBe(true)
    wrapper.unmount()
  })
})

describe('方案54 IME 与粘贴', () => {
  it('compositionstart/end 转发组合态与整段文本', async () => {
    const wrapper = await mountPlayer()
    const ic = h.MockInputController.instances[0]
    const capture = captureEl(wrapper)
    fire(capture, 'compositionstart')
    expect(ic.setComposing).toHaveBeenCalledWith(true)
    fire(capture, 'compositionend', { data: '你好世界' })
    expect(ic.handleCompositionEnd).toHaveBeenCalledWith('你好世界')
    wrapper.unmount()
  })

  it('paste 整段走 clipboard_set(paste=true) 且不落入捕获输入', async () => {
    const wrapper = await mountPlayer()
    const ic = h.MockInputController.instances[0]
    const e = fire(captureEl(wrapper), 'paste', {
      clipboardData: { getData: () => '粘贴内容' },
    })
    expect(e.defaultPrevented).toBe(true)
    expect(ic.sendClipboardSet).toHaveBeenCalledWith('粘贴内容', true)
    wrapper.unmount()
  })
})

describe('方案54 滚轮', () => {
  it('canvas wheel 转发 handleWheel 且拦截页面滚动', async () => {
    const wrapper = await mountPlayer()
    const ic = h.MockInputController.instances[0]
    const canvas = wrapper.find('canvas').element
    const e = new WheelEvent('wheel', {
      deltaY: 120, clientX: 10, clientY: 10, bubbles: true, cancelable: true,
    })
    canvas.dispatchEvent(e)
    expect(ic.handleWheel).toHaveBeenCalledTimes(1)
    expect(ic.handleWheel.mock.calls[0][0]).toBe(e)
    expect(ic.handleWheel.mock.calls[0][1]).toBe(canvas)
    expect(e.defaultPrevented).toBe(true)
    wrapper.unmount()
  })
})

describe('方案54 clipboard 消息截流', () => {
  it('clipboard/clipboard_error JSON 被截流转 handleClipboardMessage 不再转发', async () => {
    const wrapper = await mountPlayer()
    const wsInst = h.MockWebSocketService.instances[0]
    const ic = h.MockInputController.instances[0]
    const streamSpy = vi.fn()
    wsInst.setMessageHandler(streamSpy) // 模拟 h264 侧注册（经包装通道）

    wsInst.messageHandler!(JSON.stringify({ type: 'clipboard', text: '内容' }))
    expect(ic.handleClipboardMessage).toHaveBeenCalledWith({ type: 'clipboard', text: '内容' })
    expect(streamSpy).not.toHaveBeenCalled()

    wsInst.messageHandler!(JSON.stringify({ type: 'clipboard_error' }))
    expect(ic.handleClipboardMessage).toHaveBeenCalledTimes(2)
    expect(streamSpy).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('普通 JSON、坏 JSON 与二进制帧原样转发不误吞', async () => {
    const wrapper = await mountPlayer()
    const wsInst = h.MockWebSocketService.instances[0]
    const ic = h.MockInputController.instances[0]
    const streamSpy = vi.fn()
    wsInst.setMessageHandler(streamSpy)

    wsInst.messageHandler!(JSON.stringify({ type: 'config' }))
    wsInst.messageHandler!('not-json{{{')
    const buf = new ArrayBuffer(8)
    wsInst.messageHandler!(buf)
    expect(ic.handleClipboardMessage).not.toHaveBeenCalled()
    expect(streamSpy.mock.calls.map(([d]) => d)).toEqual([
      JSON.stringify({ type: 'config' }),
      'not-json{{{',
      buf,
    ])
    wrapper.unmount()
  })
})

describe('方案54 输入文本对话框与 D4 禁用', () => {
  it('ws 未就绪时「文本」按钮禁用（D4），open 后可用，close 恢复禁用', async () => {
    const wrapper = await mountPlayer()
    const wsInst = h.MockWebSocketService.instances[0]
    const btn = buttonByText(wrapper, '文本').element as HTMLButtonElement
    expect(btn.disabled).toBe(true)

    wsInst.openHandler!()
    await nextTick()
    expect(btn.disabled).toBe(false)

    wsInst.closeHandler!()
    await nextTick()
    expect(btn.disabled).toBe(true)
    wrapper.unmount()
  })

  it('点击「文本」打开对话框', async () => {
    const wrapper = await mountPlayer()
    expect(wrapper.find('.stub-dialog').exists()).toBe(false)
    await openDialog(wrapper)
    expect(wrapper.find('.stub-dialog').exists()).toBe(true)
    expect(wrapper.find('textarea').exists()).toBe(true)
    wrapper.unmount()
  })

  it('注入文本 → sendText；粘贴到设备 → sendClipboardSet(text,true)', async () => {
    const wrapper = await mountPlayer()
    await openDialog(wrapper)
    const ic = h.MockInputController.instances[0]
    await wrapper.find('textarea').setValue('要注入的文本')

    await buttonByText(wrapper, '注入文本').trigger('click')
    expect(ic.sendText).toHaveBeenCalledWith('要注入的文本')

    await buttonByText(wrapper, '粘贴到设备').trigger('click')
    expect(ic.sendClipboardSet).toHaveBeenCalledWith('要注入的文本', true)
    wrapper.unmount()
  })

  it('取设备剪贴板回填 textarea；取选中文本带 copyKey=1', async () => {
    const wrapper = await mountPlayer()
    await openDialog(wrapper)
    const ic = h.MockInputController.instances[0]
    ic.requestClipboard.mockResolvedValue('设备内容')

    await buttonByText(wrapper, '取设备剪贴板').trigger('click')
    await flushPromises()
    expect(ic.requestClipboard).toHaveBeenCalledWith(0)
    expect((wrapper.find('textarea').element as HTMLTextAreaElement).value).toBe('设备内容')

    await buttonByText(wrapper, '取设备选中文本').trigger('click')
    await flushPromises()
    expect(ic.requestClipboard).toHaveBeenCalledWith(1)
    wrapper.unmount()
  })

  it('取回 null → warning 提示且不回填', async () => {
    const wrapper = await mountPlayer()
    await openDialog(wrapper)
    const ic = h.MockInputController.instances[0]
    ic.requestClipboard.mockResolvedValue(null)

    await buttonByText(wrapper, '取设备剪贴板').trigger('click')
    await flushPromises()
    expect(h.ElMessage.warning).toHaveBeenCalledWith('设备剪贴板不可用或应答超时')
    expect((wrapper.find('textarea').element as HTMLTextAreaElement).value).toBe('')
    wrapper.unmount()
  })

  it('D2 checkbox 持久化 prefer-text 并同步控制器，重挂载读回', async () => {
    const wrapper = await mountPlayer()
    const ic = h.MockInputController.instances[0]
    expect(ic.setPreferText).toHaveBeenLastCalledWith(false)

    await openDialog(wrapper)
    // 工具栏「剪贴板镜像」checkbox 在 DOM 序前于对话框 prefer-text——须限定对话框内选择
    await wrapper.find('.prefer-text input[type="checkbox"]').setValue(true)
    expect(ic.setPreferText).toHaveBeenLastCalledWith(true)
    expect(localStorage.getItem('osc.keyboardPreferText')).toBe('1')
    wrapper.unmount()

    const wrapper2 = await mountPlayer()
    expect(h.MockInputController.instances[1].setPreferText).toHaveBeenCalledWith(true)
    wrapper2.unmount()
  })
})
