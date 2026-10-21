/**
 * DeviceDetail 组件测试
 * ======================
 *
 * 覆盖：
 *   - 挂载：拉取设备信息（分辨率注入 VideoPlayer）、创建调试会话
 *   - getDevice 失败时回退默认分辨率 1080x1920 且不抛错
 *   - DebugPanel 上报停靠位置切换布局方向（column-layout）
 *   - 点击「断开」关闭调试会话并显式返回 Dashboard（不依赖历史栈）
 *   - 截图按钮（方案 37 B7 改道）：抓帧结果打开 ScreenshotPreview 预览弹窗（不再直接下载）
 *
 * 说明：VideoPlayer / DebugPanel / ScreenshotPreview 以轻量 stub 替代，避免拖入视频流、
 * 调试面板与标注组件的完整依赖。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, shallowMount, type VueWrapper } from '@vue/test-utils'
import { nextTick } from 'vue'
import { createPinia, setActivePinia, type Pinia } from 'pinia'

const mockApi = vi.hoisted(() => ({
  getDevice: vi.fn(),
  createDebugSession: vi.fn(),
  closeDebugSession: vi.fn(),
}))
vi.mock('@/services/api', () => ({ api: mockApi }))

const elMessageMock = vi.hoisted(() => ({ info: vi.fn(), error: vi.fn() }))
vi.mock('element-plus', () => ({ ElMessage: elMessageMock }))

/** 方案 39 T5：最小 mock vue-router 的 useRouter（DeviceDetail 文件按钮跳转用），其余导出保持真实 */
const routerPushMock = vi.hoisted(() => vi.fn())
vi.mock('vue-router', async (importOriginal) => {
  const actual = await importOriginal<typeof import('vue-router')>()
  return { ...actual, useRouter: () => ({ push: routerPushMock }) }
})

/** 方案 40 A：VideoPlayer stub 暴露 captureSnapshot，供父组件截图按钮调用 */
const captureSnapshotMock = vi.hoisted(() => vi.fn())

/** 方案 40 消费点 2：首帧缩略图写入设备 store */
const setThumbnailMock = vi.hoisted(() => vi.fn())
vi.mock('@/stores/device', () => ({
  useDeviceStore: () => ({ setThumbnailFromBlob: setThumbnailMock }),
}))

import DeviceDetail from './DeviceDetail.vue'

const VideoPlayerStub = {
  name: 'VideoPlayer',
  props: ['deviceId', 'deviceWidth', 'deviceHeight'],
  emits: ['first-frame'],
  setup(
    _props: Record<string, unknown>,
    ctx: { expose: (exposed: Record<string, unknown>) => void },
  ) {
    ctx.expose({ captureSnapshot: captureSnapshotMock })
  },
  template: '<div class="vp-stub" />',
}

const DebugPanelStub = {
  name: 'DebugPanel',
  props: ['deviceId'],
  emits: ['position-change'],
  template: '<div class="dp-stub" />',
}

/** 方案 37 B7：截图预览弹窗 stub（标注行为由 ScreenshotPreview.test 单独覆盖） */
const ScreenshotPreviewStub = {
  name: 'ScreenshotPreview',
  props: ['visible', 'image', 'deviceId'],
  emits: ['close'],
  template: '<div class="sp-stub" />',
}

const ElButtonStub = {
  name: 'ElButton',
  template: '<button class="stub-btn" type="button"><slot /></button>',
}

let pinia: Pinia

async function mountView(): Promise<VueWrapper> {
  pinia = createPinia()
  setActivePinia(pinia)
  const wrapper = shallowMount(DeviceDetail, {
    props: { id: 'dev1' },
    global: {
      plugins: [pinia],
      stubs: {
        VideoPlayer: VideoPlayerStub,
        DebugPanel: DebugPanelStub,
        ScreenshotPreview: ScreenshotPreviewStub,
        'el-button': ElButtonStub,
      },
    },
  })
  await flushPromises()
  return wrapper
}

const anchorClickSpy = vi
  .spyOn(HTMLAnchorElement.prototype, 'click')
  .mockImplementation(() => {})

beforeEach(() => {
  mockApi.getDevice.mockReset()
  mockApi.createDebugSession.mockReset().mockResolvedValue({ session_id: 's1' })
  mockApi.closeDebugSession.mockReset().mockResolvedValue(undefined)
  captureSnapshotMock.mockReset()
  setThumbnailMock.mockReset()
  elMessageMock.info.mockReset()
  elMessageMock.error.mockReset()
  routerPushMock.mockReset()
  anchorClickSpy.mockClear()
})

describe('DeviceDetail', () => {
  it('挂载时拉取设备信息、创建调试会话并把分辨率注入 VideoPlayer', async () => {
    mockApi.getDevice.mockResolvedValue({
      id: 'dev1',
      model: 'Pixel 8',
      os_version: '14',
      resolution: [2340, 1080],
      battery: 80,
      status: 'online',
    })
    const wrapper = await mountView()

    expect(mockApi.getDevice).toHaveBeenCalledWith('dev1')
    expect(mockApi.createDebugSession).toHaveBeenCalledWith('dev1', 'user-1')

    expect(wrapper.find('h2').text()).toBe('设备: dev1')
    expect(wrapper.findAll('button').map((b) => b.text())).toContain('断开')

    const vp = wrapper.findComponent({ name: 'VideoPlayer' })
    expect(vp.props('deviceId')).toBe('dev1')
    expect(vp.props('deviceWidth')).toBe(2340)
    expect(vp.props('deviceHeight')).toBe(1080)

    expect(wrapper.findComponent({ name: 'DebugPanel' }).props('deviceId')).toBe('dev1')
  })

  it('设备信息获取失败时回退默认分辨率且不抛错', async () => {
    mockApi.getDevice.mockRejectedValue(new Error('device not found'))
    const wrapper = await mountView()

    const vp = wrapper.findComponent({ name: 'VideoPlayer' })
    expect(vp.props('deviceWidth')).toBe(1080)
    expect(vp.props('deviceHeight')).toBe(1920)
    // 会话创建不依赖设备信息接口
    expect(mockApi.createDebugSession).toHaveBeenCalledWith('dev1', 'user-1')
  })

  it('DebugPanel 上报 positioning 变化时切换布局方向', async () => {
    mockApi.getDevice.mockResolvedValue({ id: 'dev1', resolution: [1080, 1920] })
    const wrapper = await mountView()

    expect(wrapper.find('.content').classes()).not.toContain('column-layout')

    wrapper.findComponent({ name: 'DebugPanel' }).vm.$emit('position-change', 'bottom')
    await nextTick()

    expect(wrapper.find('.content').classes()).toContain('column-layout')
  })

  it('点击「断开」关闭调试会话并显式返回 Dashboard（不依赖历史栈）', async () => {
    mockApi.getDevice.mockResolvedValue({ id: 'dev1', resolution: [1080, 1920] })
    const wrapper = await mountView()

    const disconnectBtn = wrapper.findAll('button').find((b) => b.text().includes('断开'))
    expect(disconnectBtn).toBeDefined()
    await disconnectBtn!.trigger('click')
    await flushPromises()

    expect(mockApi.closeDebugSession).toHaveBeenCalledWith('s1')
    expect(routerPushMock).toHaveBeenCalledWith('/')

    wrapper.unmount()
  })
})

describe('DeviceDetail 截图按钮（方案 37 B7 改道）', () => {
  async function clickScreenshot(wrapper: VueWrapper) {
    const shotBtn = wrapper.findAll('button').find((b) => b.text().includes('截图'))
    expect(shotBtn).toBeDefined()
    await shotBtn!.trigger('click')
    await flushPromises()
  }

  it('流抓帧结果交给预览弹窗标注（不再直接下载）', async () => {
    mockApi.getDevice.mockResolvedValue({ id: 'dev1', resolution: [1080, 1920] })
    captureSnapshotMock.mockResolvedValue({
      blob: new Blob(['frame-png'], { type: 'image/png' }),
      source: 'stream',
    })
    const wrapper = await mountView()

    await clickScreenshot(wrapper)

    expect(captureSnapshotMock).toHaveBeenCalledTimes(1)
    const preview = wrapper.findComponent({ name: 'ScreenshotPreview' })
    expect(preview.props('visible')).toBe(true)
    expect(preview.props('image')).toBeInstanceOf(Blob)
    expect(preview.props('deviceId')).toBe('dev1')
    expect(anchorClickSpy).not.toHaveBeenCalled()
    expect(elMessageMock.info).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('截图失败可见化（方案 51 F1-05）：ElMessage.error 且不弹预览', async () => {
    mockApi.getDevice.mockResolvedValue({ id: 'dev1', resolution: [1080, 1920] })
    captureSnapshotMock.mockRejectedValue(new Error('canvas 为空'))
    const wrapper = await mountView()

    await clickScreenshot(wrapper)

    expect(elMessageMock.error).toHaveBeenCalledWith('截图失败: canvas 为空')
    expect(wrapper.findComponent({ name: 'ScreenshotPreview' }).props('visible')).toBe(false)
    wrapper.unmount()
  })

  it('回退路径时提示文案且同样打开预览弹窗', async () => {
    mockApi.getDevice.mockResolvedValue({ id: 'dev1', resolution: [1080, 1920] })
    captureSnapshotMock.mockResolvedValue({
      blob: new Blob(['api-png']),
      source: 'fallback',
    })
    const wrapper = await mountView()

    await clickScreenshot(wrapper)

    expect(elMessageMock.info).toHaveBeenCalledWith('当前无视频流，已回退设备端截图')
    expect(wrapper.findComponent({ name: 'ScreenshotPreview' }).props('visible')).toBe(true)
    expect(anchorClickSpy).not.toHaveBeenCalled()
    wrapper.unmount()
  })
})

describe('DeviceDetail 首帧缩略图（方案 40 消费点 2）', () => {
  it('视频流首帧抓帧写入设备 store 缩略图缓存', async () => {
    mockApi.getDevice.mockResolvedValue({ id: 'dev1', resolution: [1080, 1920] })
    const wrapper = await mountView()
    const blob = new Blob(['frame'])

    wrapper.findComponent({ name: 'VideoPlayer' }).vm.$emit('first-frame', blob)
    await flushPromises()

    expect(setThumbnailMock).toHaveBeenCalledWith('dev1', blob)
    wrapper.unmount()
  })
})

describe('DeviceDetail 会话创建失败可见化（方案 51 F2-02）', () => {
  it('503 SESSION_LIMIT：ElMessage.error 上限专项文案', async () => {
    mockApi.getDevice.mockResolvedValue({ id: 'dev1', resolution: [1080, 1920] })
    mockApi.createDebugSession.mockRejectedValue({
      response: {
        status: 503,
        data: { error: { code: 'SESSION_LIMIT', message: '调试活跃会话数已达上限（32），请先关闭部分会话后重试' } },
      },
    })
    const wrapper = await mountView()

    expect(elMessageMock.error).toHaveBeenCalledWith(
      '调试会话已达上限（32），请先关闭部分调试页面后重试',
    )
    wrapper.unmount()
  })

  it('其它失败：透传后端 message；无 message 用兜底文案', async () => {
    mockApi.getDevice.mockResolvedValue({ id: 'dev1', resolution: [1080, 1920] })
    mockApi.createDebugSession.mockRejectedValue({
      response: { status: 404, data: { error: { code: 'DEVICE_NOT_FOUND', message: 'Device not found: dev1' } } },
    })
    const wrapper = await mountView()
    expect(elMessageMock.error).toHaveBeenLastCalledWith('Device not found: dev1')

    mockApi.createDebugSession.mockRejectedValue(new Error('network'))
    const wrapper2 = await mountView()
    expect(elMessageMock.error).toHaveBeenLastCalledWith('调试会话创建失败')
    wrapper.unmount()
    wrapper2.unmount()
  })
})

describe('DeviceDetail 文件管理入口（方案 39 T5）', () => {
  it('顶栏「文件」按钮跳转 device-files 路由并携带设备 id', async () => {
    mockApi.getDevice.mockResolvedValue({ id: 'dev1', resolution: [1080, 1920] })
    const wrapper = await mountView()

    const filesBtn = wrapper.find('.btn-files')
    expect(filesBtn.exists()).toBe(true)
    expect(filesBtn.text()).toBe('文件')
    await filesBtn.trigger('click')

    expect(routerPushMock).toHaveBeenCalledWith({
      name: 'device-files',
      params: { id: 'dev1' },
    })
    wrapper.unmount()
  })
})