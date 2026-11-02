/**
 * VideoPlayer 方案 56 手动横竖屏旋转测试
 * =========================================
 *
 * 覆盖：
 *   - 默认 0°：按钮文案「旋转 0°」、canvas 无内联 transform（θ=0 走原 CSS 渲染）
 *   - 点击循环 0→90→180→270→0；每次同步 setDisplayRotation + 按设备持久化
 *   - 挂载时读回持久化角并同步控制器（非法值归零）
 *   - 旋转态 canvas 内联 contain-fit 样式（绝对定位 + rotate），依赖容器/帧尺寸
 *   - deviceId 切换时按新设备重载旋转角
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import { flushPromises, shallowMount, type VueWrapper } from '@vue/test-utils'

const h = vi.hoisted(() => {
  class MockWebSocketService {
    static instances: MockWebSocketService[] = []
    messageHandler: ((data: unknown) => void) | null = null
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
    setCloseHandler(_h: () => void) {}
    setErrorHandler(_h: (e: unknown) => void) {}
    setOpenHandler(_h: () => void) {}
    setReconnectHandler(_h: () => void) {}
  }

  class MockH264Stream {
    static instances: MockH264Stream[] = []
    restartGraceUntil = 0
    suspended = false
    state = 'streaming'
    stats = { fps: 0, frameCount: 0, lastFrameTime: 0, state: 'streaming', error: null as string | null }
    probeStats = { frameCount: 0, lastFrameTime: 0 }
    start = vi.fn()
    stop = vi.fn()
    suspend = vi.fn()
    resume = vi.fn()
    setStateChangeHandler = vi.fn()
    setStatsUpdateHandler = vi.fn()
    captureFrame = vi.fn(() => Promise.resolve(new Blob(['f'])))
    constructor() {
      MockH264Stream.instances.push(this)
    }
  }

  class MockInputController {
    static instances: MockInputController[] = []
    setEnabled = vi.fn()
    setInputHandler = vi.fn()
    setPreferText = vi.fn()
    setDisplayRotation = vi.fn()
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

const ElDialogStub = {
  name: 'ElDialog',
  props: ['modelValue'],
  emits: ['update:modelValue'],
  template: '<div v-if="modelValue"><slot /></div>',
}
const ElButtonStub = {
  name: 'ElButton',
  props: ['disabled'],
  template: '<button class="stub-btn" type="button" :disabled="disabled"><slot /></button>',
}

function buttonByText(wrapper: VueWrapper, text: string) {
  const btn = wrapper.findAll('button').find((b) => b.text().includes(text))
  if (!btn) throw new Error(`button not found: ${text}`)
  return btn
}

function mountPlayer(props: Record<string, unknown> = {}) {
  return (async () => {
    const wrapper = shallowMount(VideoPlayer, {
      props: { deviceId: 'dev1', deviceWidth: 640, deviceHeight: 480, ...props },
      global: {
        renderStubDefaultSlot: true,
        stubs: { 'el-button': ElButtonStub, 'el-button-group': true, 'el-dialog': ElDialogStub },
      },
    })
    await flushPromises()
    return wrapper
  })()
}

beforeEach(() => {
  h.MockWebSocketService.instances = []
  h.MockH264Stream.instances = []
  h.MockInputController.instances = []
  h.MockVideoStream.instances = []
  localStorage.clear()
  vi.stubGlobal('VideoDecoder', class {})
  vi.stubGlobal('EncodedVideoChunk', class {})
  // 驱动旋转态 contain-fit：容器 640×360、ResizeObserver 存在（否则 VideoPlayer 跳过测量）
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  Object.defineProperty(HTMLElement.prototype, 'clientWidth', { configurable: true, get: () => 640 })
  Object.defineProperty(HTMLElement.prototype, 'clientHeight', { configurable: true, get: () => 360 })
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('方案56 旋转按钮默认态', () => {
  it('默认 0°：挂载调用 setDisplayRotation(0)、按钮文案「旋转 0°」、canvas 无 transform', async () => {
    const wrapper = await mountPlayer()
    expect(h.MockInputController.instances[0].setDisplayRotation).toHaveBeenLastCalledWith(0)
    expect(buttonByText(wrapper, '旋转').text()).toContain('旋转 0°')
    expect(wrapper.find('canvas').attributes('style') || '').not.toContain('rotate')
    wrapper.unmount()
  })

  it('挂载读回持久化角并同步控制器', async () => {
    localStorage.setItem('osc.videoRotation.dev1', '180')
    const wrapper = await mountPlayer()
    expect(h.MockInputController.instances[0].setDisplayRotation).toHaveBeenLastCalledWith(180)
    expect(buttonByText(wrapper, '旋转').text()).toContain('旋转 180°')
    wrapper.unmount()
  })

  it('非法持久化值归零', async () => {
    localStorage.setItem('osc.videoRotation.dev1', '45')
    const wrapper = await mountPlayer()
    expect(h.MockInputController.instances[0].setDisplayRotation).toHaveBeenLastCalledWith(0)
    wrapper.unmount()
  })
})

describe('方案56 旋转按钮循环与持久化', () => {
  it('点击四次循环 0→90→180→270→0，逐步同步并持久化', async () => {
    const wrapper = await mountPlayer()
    const ic = h.MockInputController.instances[0]
    const btn = buttonByText(wrapper, '旋转')
    const seen: string[] = []
    for (let i = 0; i < 4; i++) {
      await btn.trigger('click')
      await nextTick()
      seen.push(btn.text())
      expect(ic.setDisplayRotation).toHaveBeenLastCalledWith(((i + 1) * 90) % 360)
      expect(localStorage.getItem('osc.videoRotation.dev1')).toBe(String(((i + 1) * 90) % 360))
    }
    expect(seen.map((s) => s.replace(/\s+/g, ' ').trim())).toEqual([
      '旋转 90°',
      '旋转 180°',
      '旋转 270°',
      '旋转 0°',
    ])
    wrapper.unmount()
  })
})

describe('方案56 旋转态 contain-fit 内联样式', () => {
  it('90°：绝对定位居中 + rotate(90deg)，宽高盒按容器 contain', async () => {
    localStorage.setItem('osc.videoRotation.dev1', '90')
    const wrapper = await mountPlayer()
    const style = wrapper.find('canvas').attributes('style') || ''
    expect(style).toContain('position: absolute')
    expect(style).toContain('rotate(90deg)')
    // 原生 640×480，旋转后 eff=480×640，scale=min(640/480,360/640)=0.5625
    // disp=270×360 → 未变换布局盒 boxW×boxH=360×270
    expect(style).toContain('width: 360px')
    expect(style).toContain('height: 270px')
    wrapper.unmount()
  })

  it('180°：不交换宽高，disp 盒 360×... 方向与原生同', async () => {
    localStorage.setItem('osc.videoRotation.dev1', '180')
    const wrapper = await mountPlayer()
    const style = wrapper.find('canvas').attributes('style') || ''
    expect(style).toContain('rotate(180deg)')
    // 原生 640×480 放入 640×360：scale=min(1,0.75)=0.75 → disp=480×360，不交换
    expect(style).toContain('width: 480px')
    expect(style).toContain('height: 360px')
    wrapper.unmount()
  })
})

describe('方案56 deviceId 切换重载', () => {
  it('切换到已持久化的 dev2 重载其角度并同步控制器', async () => {
    localStorage.setItem('osc.videoRotation.dev2', '270')
    const wrapper = await mountPlayer()
    await wrapper.setProps({ deviceId: 'dev2' })
    await nextTick()
    expect(h.MockInputController.instances[0].setDisplayRotation).toHaveBeenLastCalledWith(270)
    wrapper.unmount()
  })
})
