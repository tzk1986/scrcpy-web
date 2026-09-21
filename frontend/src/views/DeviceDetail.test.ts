/**
 * DeviceDetail 组件测试
 * ======================
 *
 * 覆盖：
 *   - 挂载：拉取设备信息（分辨率注入 VideoPlayer）、创建调试会话
 *   - getDevice 失败时回退默认分辨率 1080x1920 且不抛错
 *   - DebugPanel 上报停靠位置切换布局方向（column-layout）
 *   - 点击「断开」关闭调试会话并返回上一页
 *
 * 说明：VideoPlayer / DebugPanel 以轻量 stub 替代，避免拖入视频流与调试面板的完整依赖。
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

import DeviceDetail from './DeviceDetail.vue'

const VideoPlayerStub = {
  name: 'VideoPlayer',
  props: ['deviceId', 'deviceWidth', 'deviceHeight'],
  template: '<div class="vp-stub" />',
}

const DebugPanelStub = {
  name: 'DebugPanel',
  props: ['deviceId'],
  emits: ['position-change'],
  template: '<div class="dp-stub" />',
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
        'el-button': ElButtonStub,
      },
    },
  })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  mockApi.getDevice.mockReset()
  mockApi.createDebugSession.mockReset().mockResolvedValue({ session_id: 's1' })
  mockApi.closeDebugSession.mockReset().mockResolvedValue(undefined)
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

  it('点击「断开」关闭调试会话并返回上一页', async () => {
    mockApi.getDevice.mockResolvedValue({ id: 'dev1', resolution: [1080, 1920] })
    const backSpy = vi.spyOn(window.history, 'back').mockImplementation(() => {})
    const wrapper = await mountView()

    const disconnectBtn = wrapper.findAll('button').find((b) => b.text().includes('断开'))
    expect(disconnectBtn).toBeDefined()
    await disconnectBtn!.trigger('click')
    await flushPromises()

    expect(mockApi.closeDebugSession).toHaveBeenCalledWith('s1')
    expect(backSpy).toHaveBeenCalled()

    wrapper.unmount()
  })
})