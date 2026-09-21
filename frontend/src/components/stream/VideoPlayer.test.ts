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
import { nextTick } from 'vue'
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
      error: null as string | null,
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
    stats = { fps: 0, frameCount: 0, error: null as string | null }
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
import { KeyCode } from '@/services/inputController'

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
    // 测试不引入 ElementPlus，stub 掉模板中的 el-* 组件消除解析告警；
    // renderStubDefaultSlot 让 el-button-group stub 透出内部导航键按钮
    global: { renderStubDefaultSlot: true, stubs: { 'el-button': true, 'el-button-group': true } },
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

  it('挂载时 tab 已隐藏 → 不启动 fps 上报且输入停用', async () => {
    vi.useFakeTimers()
    // 不派发 visibilitychange，仅让 document.hidden 在挂载前已为 true
    hiddenFlag = true
    const wrapper = await mountPlayer()
    const ic = h.MockInputController.instances[0]

    await vi.advanceTimersByTimeAsync(2000)
    expect(statsReportCount()).toBe(0)
    expect(ic.setEnabled).toHaveBeenLastCalledWith(false)

    // 恢复可见后按常规路径重启上报与输入
    setHidden(false)
    await vi.advanceTimersByTimeAsync(2000)
    expect(statsReportCount()).toBe(1)
    expect(ic.setEnabled).toHaveBeenLastCalledWith(true)

    wrapper.unmount()
  })

  it('?swdecode=1 时向 H264VideoStream 传入 prefer-software', async () => {
    history.pushState({}, '', '/?swdecode=1')
    const wrapper = await mountPlayer()

    const inst = h.MockH264Stream.instances[0]
    expect(inst.args[2]).toEqual({ hardwareAcceleration: 'prefer-software' })

    wrapper.unmount()
  })
})

describe('VideoPlayer 初始化与输入转发', () => {
  it('初始化画布尺寸、WebSocket、H264 流与输入控制器', async () => {
    const wrapper = await mountPlayer()
    const canvas = wrapper.find('canvas').element as HTMLCanvasElement

    // 画布初始尺寸取设备分辨率（默认值 1080x1920 见截图模式用例）
    expect(canvas.width).toBe(640)
    expect(canvas.height).toBe(480)

    const wsInst = h.MockWebSocketService.instances[0]
    expect(wsInst.url).toMatch(/^ws:\/\/.*\/ws\/video\/dev1$/)
    expect(wsInst.connect).toHaveBeenCalled()

    const h264 = h.MockH264Stream.instances[0]
    expect(h264.args[0]).toBe(wsInst)
    expect(h264.args[1]).toBe(canvas)
    // 未带 ?swdecode=1 → 不下发硬件加速偏好，走浏览器默认
    expect(h264.args[2]).toEqual({ hardwareAcceleration: undefined })
    expect(h264.start).toHaveBeenCalled()
    expect(h.MockInputController.instances.length).toBe(1)

    expect(wrapper.text()).toContain('模式: h264')
    expect(wrapper.text()).toContain('FPS: 0')
    expect(wrapper.text()).toContain('连接中')
    wrapper.unmount()
  })

  it('画布鼠标/触摸事件转发给 InputController', async () => {
    const wrapper = await mountPlayer()
    const ic = h.MockInputController.instances[0]
    const canvas = wrapper.find('canvas')
    const canvasEl = canvas.element

    await canvas.trigger('mousedown')
    await canvas.trigger('mousemove')
    await canvas.trigger('mouseup')
    await canvas.trigger('mouseleave')

    canvasEl.dispatchEvent(new Event('touchstart', { cancelable: true }))
    canvasEl.dispatchEvent(new Event('touchmove', { cancelable: true }))
    canvasEl.dispatchEvent(new Event('touchend', { cancelable: true }))

    expect(ic.handleMouseDown.mock.calls[0][1]).toBe(canvasEl)
    expect(ic.handleMouseMove).toHaveBeenCalledTimes(1)
    // mouseleave 与 mouseup 复用同一处理器
    expect(ic.handleMouseUp).toHaveBeenCalledTimes(2)
    expect(ic.handleTouchStart).toHaveBeenCalledTimes(1)
    expect(ic.handleTouchMove).toHaveBeenCalledTimes(1)
    expect(ic.handleTouchEnd).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it('导航键按钮发送对应 KeyCode', async () => {
    const wrapper = await mountPlayer()
    const ic = h.MockInputController.instances[0]
    const buttons = wrapper.findAll('el-button-stub')
    expect(buttons.length).toBe(4)

    await buttons[0].trigger('click')
    await buttons[1].trigger('click')
    await buttons[2].trigger('click')
    await buttons[3].trigger('click')

    expect(ic.sendKey.mock.calls.map(([k]) => k)).toEqual([
      KeyCode.BACK,
      KeyCode.HOME,
      KeyCode.APP_SWITCH,
      KeyCode.MENU,
    ])
    wrapper.unmount()
  })

  it('WebSocket 错误与关闭事件更新界面状态', async () => {
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    const wrapper = await mountPlayer()
    const wsInst = h.MockWebSocketService.instances[0]

    wsInst.errorHandler!(new Error('boom'))
    expect(errSpy).toHaveBeenCalled()

    wsInst.closeHandler!()
    await nextTick()
    expect(wrapper.text()).toContain('已停止')
    expect(wrapper.find('.status-text').text()).toContain('已断开')
    errSpy.mockRestore()
    wrapper.unmount()
  })
})

describe('VideoPlayer H264 状态机与回退', () => {
  it('状态/统计处理器驱动 UI，error 触发回退并支持重连', async () => {
    vi.useFakeTimers()
    const wrapper = await mountPlayer()
    const h264 = h.MockH264Stream.instances[0]
    const onState = h264.setStateChangeHandler.mock.calls[0][0]
    const onStats = h264.setStatsUpdateHandler.mock.calls[0][0]

    onState('configuring')
    await nextTick()
    expect(wrapper.text()).toContain('配置中')

    onState('streaming')
    onStats({ fps: 30, frameCount: 120, droppedFrames: 3 })
    await nextTick()
    expect(wrapper.find('.status-overlay').exists()).toBe(false)
    expect(wrapper.text()).toContain('直播中')
    expect(wrapper.text()).toContain('FPS: 30')
    expect(wrapper.text()).toContain('帧: 120')
    expect(wrapper.text()).toContain('丢帧: 3')

    // 解码错误 → 自动回退截图模式（suspend 保留实例，统计上报停止）
    h264.stats.state = 'error'
    h264.stats.error = 'decoder crashed'
    onState('error')
    await nextTick()
    expect(h264.suspend).toHaveBeenCalled()
    expect(wrapper.text()).toContain('模式: screenshot')
    const vs = h.MockVideoStream.instances[0]
    expect(vs.start).toHaveBeenCalled()

    await vi.advanceTimersByTimeAsync(2000)
    expect(statsReportCount()).toBe(0)

    // 截图流进入 streaming → 只读徽标
    const onVsState = vs.setStateChangeHandler.mock.calls[0][0]
    onVsState('streaming')
    await nextTick()
    expect(wrapper.text()).toContain('只读预览 · 输入不可用')

    // 截图流报错 → 错误覆盖层 + 重连按钮
    vs.stats.error = 'screenshot stream failed'
    onVsState('error')
    await nextTick()
    expect(wrapper.find('.status-text.error').text()).toContain('screenshot stream failed')

    await wrapper.find('.status-text.error el-button-stub').trigger('click')
    expect(h264.start).toHaveBeenCalledTimes(2)
    await nextTick()
    expect(wrapper.find('.status-text.error').exists()).toBe(false)
    expect(wrapper.text()).toContain('连接中')
    wrapper.unmount()
  })

  it('截图模式下连续窗口帧数达标后自动回切 H264', async () => {
    vi.useFakeTimers()
    const wrapper = await mountPlayer()
    const h264 = h.MockH264Stream.instances[0]

    h264.stats.state = 'error'
    h264.setStateChangeHandler.mock.calls[0][0]('error')
    await nextTick()
    const vs = h.MockVideoStream.instances[0]
    expect(vs.start).toHaveBeenCalled()
    expect(wrapper.text()).toContain('模式: screenshot')

    // 连续 3 个探测窗口（各 2s）帧数 ≥ MIN_FRAMES 才回切
    for (let i = 1; i <= 3; i++) {
      h264.probeStats.frameCount = i * 5
      await vi.advanceTimersByTimeAsync(2000)
    }
    expect(vs.stop).toHaveBeenCalled()
    expect(h264.resume).toHaveBeenCalled()
    await nextTick()
    expect(wrapper.text()).toContain('模式: h264')

    // 回切后 fps 上报恢复
    await vi.advanceTimersByTimeAsync(2000)
    expect(statsReportCount()).toBe(1)
    wrapper.unmount()
  })

  it('WebCodecs 不可用时走截图模式（默认分辨率 1080x1920）', async () => {
    vi.unstubAllGlobals()
    const wrapper = shallowMount(VideoPlayer, {
      props: { deviceId: 'dev1' },
      global: { renderStubDefaultSlot: true, stubs: { 'el-button': true, 'el-button-group': true } },
    })
    await flushPromises()

    const canvas = wrapper.find('canvas').element as HTMLCanvasElement
    expect(canvas.width).toBe(1080)
    expect(canvas.height).toBe(1920)
    expect(h.MockH264Stream.instances.length).toBe(0)

    const vs = h.MockVideoStream.instances[0]
    expect(vs.start).toHaveBeenCalled()
    expect(h.MockInputController.instances.length).toBe(1)
    expect(wrapper.text()).toContain('模式: screenshot')

    vs.setStateChangeHandler.mock.calls[0][0]('streaming')
    await nextTick()
    expect(wrapper.text()).toContain('只读预览 · 输入不可用')

    vs.setStatsUpdateHandler.mock.calls[0][0]({ fps: 1, frameCount: 7 })
    await nextTick()
    expect(wrapper.text()).toContain('FPS: 1')
    expect(wrapper.text()).toContain('帧: 7')

    // 截图流报错 → 重连按钮重启截图流
    vs.stats.error = 'screenshot failed'
    vs.setStateChangeHandler.mock.calls[0][0]('error')
    await nextTick()
    expect(wrapper.find('.status-text.error').text()).toContain('screenshot failed')

    await wrapper.find('.status-text.error el-button-stub').trigger('click')
    await flushPromises()
    expect(vs.start).toHaveBeenCalledTimes(2)
    expect(wrapper.find('.status-text.error').exists()).toBe(false)

    wrapper.unmount()
    expect(vs.stop).toHaveBeenCalled()
    expect(h.MockWebSocketService.instances[0].close).toHaveBeenCalled()
  })
})