/**
 * Dashboard 组件测试
 * ====================
 *
 * 覆盖：
 *   - 挂载：拉取设备列表、启动 SSE（EventSource 指向 /api/devices/events）、空态提示、卸载停 SSE
 *   - 添加设备对话框：打开、填写 IP、点击连接 → connectDevice 调用与成功提示、关闭对话框
 *   - SSE 事件驱动设备列表增删；点击刷新重新拉取
 *
 * 说明：Element Plus 的 el-* 组件以轻量 stub 替代；el-form stub 提供 validate()
 * 供 handleConnect 的表单校验调用。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, shallowMount, type VueWrapper } from '@vue/test-utils'
import { nextTick } from 'vue'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'

const h = vi.hoisted(() => {
  class MockEventSource {
    static instances: MockEventSource[] = []
    onmessage: ((event: { data: string }) => void) | null = null
    onerror: (() => void) | null = null
    close = vi.fn()
    url: string

    constructor(url: string) {
      this.url = url
      MockEventSource.instances.push(this)
    }
  }
  return {
    MockEventSource,
    ElMessage: { success: vi.fn(), error: vi.fn() },
  }
})

const mockApi = vi.hoisted(() => ({
  listDevices: vi.fn(),
  connectDevice: vi.fn(),
  disconnectDevice: vi.fn(),
  getDevice: vi.fn(),
}))
vi.mock('@/services/api', () => ({ api: mockApi }))
vi.mock('element-plus', () => ({ ElMessage: h.ElMessage }))

import Dashboard from './Dashboard.vue'
import { useDeviceStore } from '@/stores/device'

const deviceA = {
  id: 'dev-a',
  model: 'Pixel 8',
  os_version: '14',
  resolution: [1080, 2400] as [number, number],
  battery: 88,
  status: 'online',
}

const deviceB = {
  id: '192.168.1.33:5555',
  model: 'Galaxy S23',
  os_version: '13',
  resolution: [1080, 2340] as [number, number],
  battery: 55,
  status: 'online',
}

const ElButtonStub = {
  name: 'ElButton',
  template: '<button class="stub-btn" type="button"><slot /></button>',
}

const ElDialogStub = {
  name: 'ElDialog',
  props: ['modelValue', 'title'],
  emits: ['closed'],
  template:
    '<div v-if="modelValue" class="stub-dialog"><div class="stub-dialog-title">{{ title }}</div><slot /><slot name="footer" /></div>',
}

const ElFormStub = {
  name: 'ElForm',
  methods: { validate: () => Promise.resolve(true) },
  template: '<form class="stub-form"><slot /></form>',
}

const ElFormItemStub = {
  name: 'ElFormItem',
  template: '<div class="stub-form-item"><slot /></div>',
}

const ElInputStub = {
  name: 'ElInput',
  props: ['modelValue'],
  emits: ['update:modelValue'],
  template:
    '<input class="stub-input" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
}

const ElAlertStub = {
  name: 'ElAlert',
  props: ['title'],
  template: '<div class="stub-alert"><div class="stub-alert-title">{{ title }}</div><slot /></div>',
}

const ElTableStub = {
  name: 'ElTable',
  props: ['data'],
  template: '<div class="stub-table"><slot /></div>',
}

let pinia: Pinia

async function mountDashboard(): Promise<VueWrapper> {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'dashboard', component: { template: '<div />' } },
      { path: '/device/:id', name: 'device-detail', component: { template: '<div />' } },
    ],
  })
  await router.push('/')
  await router.isReady()

  pinia = createPinia()
  setActivePinia(pinia)

  const wrapper = shallowMount(Dashboard, {
    global: {
      plugins: [pinia, router],
      stubs: {
        'el-button': ElButtonStub,
        'el-dialog': ElDialogStub,
        'el-form': ElFormStub,
        'el-form-item': ElFormItemStub,
        'el-input': ElInputStub,
        'el-input-number': ElInputStub,
        'el-alert': ElAlertStub,
        'el-table': ElTableStub,
        'el-table-column': true,
        'el-icon': true,
        'el-tag': true,
      },
      directives: { loading: {} },
    },
  })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  vi.stubGlobal('EventSource', h.MockEventSource)
  h.MockEventSource.instances = []
  mockApi.listDevices.mockReset()
  mockApi.connectDevice.mockReset()
  h.ElMessage.success.mockClear()
  h.ElMessage.error.mockClear()
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('Dashboard', () => {
  it('挂载时拉取设备列表、启动 SSE，空列表显示空态提示', async () => {
    mockApi.listDevices.mockResolvedValue([])
    const wrapper = await mountDashboard()

    expect(mockApi.listDevices).toHaveBeenCalled()
    expect(h.MockEventSource.instances).toHaveLength(1)
    expect(h.MockEventSource.instances[0].url).toContain('/api/devices/events')

    expect(wrapper.find('h2').text()).toBe('设备管理')
    const buttonTexts = wrapper.findAll('button').map((b) => b.text())
    expect(buttonTexts.some((t) => t.includes('添加设备'))).toBe(true)
    expect(buttonTexts.some((t) => t.includes('刷新'))).toBe(true)

    const alert = wrapper.find('.stub-alert')
    expect(alert.find('.stub-alert-title').text()).toBe('暂无设备')
    expect(alert.text()).toContain('当前没有检测到设备')

    wrapper.unmount()
    expect(h.MockEventSource.instances[0].close).toHaveBeenCalled()
  })

  it('添加设备：填写 IP 后连接成功并关闭对话框', async () => {
    vi.useFakeTimers()
    mockApi.listDevices.mockResolvedValue([])
    mockApi.connectDevice.mockResolvedValue({ success: true, device_id: '192.168.1.50:5555' })
    const wrapper = await mountDashboard()

    expect(wrapper.find('.stub-dialog').exists()).toBe(false)

    const addBtn = wrapper.findAll('button').find((b) => b.text().includes('添加设备'))
    await addBtn!.trigger('click')
    await nextTick()

    const dialog = wrapper.find('.stub-dialog')
    expect(dialog.exists()).toBe(true)
    expect(dialog.find('.stub-dialog-title').text()).toBe('添加设备')
    expect(dialog.text()).toContain('adb tcpip 5555')

    wrapper.findComponent({ name: 'ElInput' }).vm.$emit('update:modelValue', '192.168.1.50')
    await nextTick()

    const connectBtn = dialog.findAll('button').find((b) => b.text() === '连接')
    expect(connectBtn).toBeDefined()
    await connectBtn!.trigger('click')
    await flushPromises()

    expect(mockApi.connectDevice).toHaveBeenCalledWith('192.168.1.50', 5555)

    // store.connectDevice 成功后等待 1s 再刷新列表
    await vi.advanceTimersByTimeAsync(1000)
    await flushPromises()

    expect(h.ElMessage.success).toHaveBeenCalled()
    expect(wrapper.find('.stub-dialog').exists()).toBe(false)
  })

  it('SSE 事件实时增删设备，点击刷新重新拉取列表', async () => {
    mockApi.listDevices.mockResolvedValue([deviceA])
    const wrapper = await mountDashboard()
    const store = useDeviceStore()
    expect(store.devices.map((d) => d.id)).toEqual(['dev-a'])

    const es = h.MockEventSource.instances[0]
    es.onmessage!({ data: JSON.stringify({ type: 'connected', device: deviceB }) })
    await nextTick()
    expect(store.devices.map((d) => d.id)).toEqual(['dev-a', '192.168.1.33:5555'])

    es.onmessage!({ data: JSON.stringify({ type: 'disconnected', device_id: 'dev-a' }) })
    await nextTick()
    expect(store.devices.map((d) => d.id)).toEqual(['192.168.1.33:5555'])

    const refreshBtn = wrapper.findAll('button').find((b) => b.text().includes('刷新'))
    await refreshBtn!.trigger('click')
    await flushPromises()
    expect(mockApi.listDevices).toHaveBeenCalledTimes(2)

    wrapper.unmount()
  })
})