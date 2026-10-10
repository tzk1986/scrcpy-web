/**
 * Dashboard 组件测试
 * ====================
 *
 * 覆盖：
 *   - 挂载：拉取设备列表、启动 SSE（EventSource 指向 /api/devices/events）、空态提示、卸载停 SSE
 *   - 添加设备对话框：打开、填写 IP、点击连接 → connectDevice 调用与成功提示、关闭对话框
 *   - SSE 事件驱动设备列表增删；点击刷新重新拉取
 *   - 网段扫描（方案 36）：参数透传（含仅扫描不连接）、loading 文案切换、结果 alert 回显、失败提示
 *   - 缩略图列（方案 36）：点击加载 → img 渲染（blob URL + title）、失败重试、刷新缩略图强制重拉
 *
 * 说明：Element Plus 的 el-* 组件以轻量 stub 替代；el-form stub 提供 validate()
 * 供 handleConnect 的表单校验调用。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, shallowMount, type VueWrapper } from '@vue/test-utils'
import { defineComponent, h as vueH, inject, nextTick, provide } from 'vue'
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
  scanSubnet: vi.fn(),
  screenshot: vi.fn(),
}))
vi.mock('@/services/api', () => ({ api: mockApi }))
vi.mock('element-plus', () => ({ ElMessage: h.ElMessage }))

// happy-dom 无 createObjectURL / revokeObjectURL 实现（store 缩略图链路需要，m9）
const urlStub = vi.hoisted(() => ({
  createObjectURL: vi.fn(),
  revokeObjectURL: vi.fn(),
}))

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

const ElTableStub = defineComponent({
  name: 'ElTable',
  props: { data: { type: Array, default: () => [] } },
  setup(props, { slots }) {
    provide('tableData', () => props.data as Array<Record<string, unknown>>)
    return () => vueH('div', { class: 'stub-table' }, slots.default?.())
  },
})

const ElTableColumnStub = defineComponent({
  name: 'ElTableColumn',
  setup(_props, { slots }) {
    const tableData = inject<(() => Array<Record<string, unknown>>) | undefined>('tableData')
    return () => {
      const slot = slots.default
      if (!slot || !tableData) return null
      return vueH(
        'div',
        { class: 'stub-column' },
        tableData().map((row) =>
          vueH('div', { class: 'stub-row', 'data-id': String(row.id ?? '') }, slot({ row }))
        )
      )
    }
  },
})

const ElTagStub = {
  name: 'ElTag',
  props: ['type'],
  template: '<span class="stub-tag" :data-type="type"><slot /></span>',
}

const ElImageStub = {
  name: 'ElImage',
  props: ['src'],
  template: '<img class="stub-image" :src="src" />',
}

const ElCheckboxStub = {
  name: 'ElCheckbox',
  props: ['modelValue'],
  emits: ['update:modelValue'],
  template:
    '<label class="stub-checkbox"><input type="checkbox" :checked="modelValue" @change="$emit(\'update:modelValue\', $event.target.checked)" /><slot /></label>',
}

/** 扫描结果样例：开放 2 台，1 成功 1 connect_failed（方案 36） */
function scanFixture(overrides: Record<string, unknown> = {}) {
  return {
    cidr: '192.168.8.0/24',
    probed: 254,
    open_hosts: ['192.168.8.18', '192.168.8.25'],
    connect_results: [
      { ip: '192.168.8.18', ok: true, device_id: '192.168.8.18:5555', reason: null, message: null },
      { ip: '192.168.8.25', ok: false, device_id: null, reason: 'connect_failed', message: '握手超时' },
    ],
    truncated: false,
    ...overrides,
  }
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
        'el-table-column': ElTableColumnStub,
        'el-icon': true,
        'el-tag': ElTagStub,
        'el-image': ElImageStub,
        'el-checkbox': ElCheckboxStub,
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
  mockApi.scanSubnet.mockReset()
  mockApi.screenshot.mockReset().mockResolvedValue(new Blob(['png']))
  urlStub.createObjectURL.mockReset().mockImplementation(() => 'blob:dash-thumb')
  urlStub.revokeObjectURL.mockReset()
  Object.defineProperty(URL, 'createObjectURL', {
    configurable: true,
    value: urlStub.createObjectURL,
  })
  Object.defineProperty(URL, 'revokeObjectURL', {
    configurable: true,
    value: urlStub.revokeObjectURL,
  })
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

    // B1 修正：dialog 作用域内取首个 ElInput（scan-bar 也有 el-input，全局查找会被劫持）
    dialog.findComponent({ name: 'ElInput' }).vm.$emit('update:modelValue', '192.168.1.50')
    await nextTick()

    const connectBtn = dialog.findAll('button').find((b) => b.text() === '连接')
    expect(connectBtn).toBeDefined()
    await connectBtn!.trigger('click')
    await flushPromises()

    expect(mockApi.connectDevice).toHaveBeenCalledWith('192.168.1.50', 5555)

    // store.connectDevice 成功后静默重拉列表（不阻塞对话框关闭）
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

    const refreshBtn = wrapper.findAll('button').find((b) => b.text() === '刷新')
    await refreshBtn!.trigger('click')
    await flushPromises()
    expect(mockApi.listDevices).toHaveBeenCalledTimes(2)

    wrapper.unmount()
  })

  it('挂载顺序：SSE 先于列表刷新就位（列表挂起不影响事件订阅）', async () => {
    mockApi.listDevices.mockImplementation(() => new Promise(() => {}))
    const wrapper = await mountDashboard()

    expect(h.MockEventSource.instances).toHaveLength(1)

    wrapper.unmount()
  })

  it('慢设备行渲染「adb 慢」红灯，正常设备行无红灯', async () => {
    mockApi.listDevices.mockResolvedValue([{ ...deviceA, slow: true }, deviceB])
    const wrapper = await mountDashboard()

    const slowTags = wrapper.findAll('.stub-tag').filter((t) => t.text() === 'adb 慢')
    expect(slowTags).toHaveLength(1)

    const slowRows = wrapper.findAll('.stub-row[data-id="dev-a"]')
    expect(slowRows.some((r) => r.text().includes('adb 慢'))).toBe(true)

    const normalRows = wrapper.findAll('.stub-row[data-id="192.168.1.33:5555"]')
    expect(normalRows.length).toBeGreaterThan(0)
    expect(normalRows.every((r) => !r.text().includes('adb 慢'))).toBe(true)

    wrapper.unmount()
  })

  it('网段扫描：点击「扫描并连接」透传参数、结果 alert 回显、列表重拉与缩略图自动加载', async () => {
    mockApi.listDevices.mockResolvedValue([deviceA])
    mockApi.scanSubnet.mockResolvedValue(scanFixture())
    const wrapper = await mountDashboard()

    await wrapper.find('input.scan-cidr').setValue('192.168.8.0/24')
    const scanBtn = wrapper.findAll('button').find((b) => b.text() === '扫描并连接')
    expect(scanBtn).toBeDefined()
    await scanBtn!.trigger('click')
    await flushPromises()

    expect(mockApi.scanSubnet).toHaveBeenCalledWith('192.168.8.0/24', true, 5555)

    const alert = wrapper.find('.scan-result')
    expect(alert.exists()).toBe(true)
    expect(alert.text()).toContain('探测 254 台')
    expect(alert.text()).toContain('发现开放 2 台')
    expect(alert.text()).toContain('连接成功 1 台')
    expect(alert.text()).toContain('失败 1 台（连接失败 1）')

    // store.scanSubnet 内部静默重拉 + 对 ok 设备自动加载缩略图（D5 触发时机 1）
    await vi.waitFor(() => expect(mockApi.listDevices).toHaveBeenCalledTimes(2)) // 挂载 1 + 扫描后 1
    await vi.waitFor(() => expect(mockApi.screenshot).toHaveBeenCalledWith('192.168.8.18:5555'))
    expect(mockApi.screenshot).toHaveBeenCalledTimes(1) // 失败设备不加载

    wrapper.unmount()
  })

  it('「仅扫描不连接」勾选后 connect=false 透传', async () => {
    mockApi.listDevices.mockResolvedValue([])
    mockApi.scanSubnet.mockResolvedValue(scanFixture({ connect_results: [], open_hosts: [] }))
    const wrapper = await mountDashboard()

    wrapper.findComponent({ name: 'ElCheckbox' }).vm.$emit('update:modelValue', true)
    await nextTick()
    await wrapper.find('input.scan-cidr').setValue('10.0.0.0/24')
    const scanBtn = wrapper.findAll('button').find((b) => b.text() === '扫描并连接')
    await scanBtn!.trigger('click')
    await flushPromises()

    expect(mockApi.scanSubnet).toHaveBeenCalledWith('10.0.0.0/24', false, 5555)

    wrapper.unmount()
  })

  it('扫描中按钮文案切换为「正在扫描网段…」，完成后恢复', async () => {
    mockApi.listDevices.mockResolvedValue([])
    let resolveScan!: (v: unknown) => void
    mockApi.scanSubnet.mockImplementation(() => new Promise((res) => { resolveScan = res }))
    const wrapper = await mountDashboard()

    await wrapper.find('input.scan-cidr').setValue('192.168.8.0/24')
    // 点击未拦截 promise 前先记录按钮引用会被替换，改用文本查找
    await wrapper.findAll('button').find((b) => b.text() === '扫描并连接')!.trigger('click')
    await nextTick()

    expect(wrapper.findAll('button').some((b) => b.text().includes('正在扫描网段'))).toBe(true)
    expect(wrapper.find('.scan-result').exists()).toBe(false) // 结果 alert 初始隐藏

    resolveScan(scanFixture())
    await flushPromises()
    expect(wrapper.findAll('button').some((b) => b.text() === '扫描并连接')).toBe(true)
    expect(wrapper.find('.scan-result').exists()).toBe(true)

    wrapper.unmount()
  })

  it('扫描失败（后端结构化错误）走 ElMessage 且不显示结果 alert', async () => {
    mockApi.listDevices.mockResolvedValue([])
    mockApi.scanSubnet.mockRejectedValue({
      response: { data: { error: { message: '已有扫描任务进行中，请等待其完成后再试' } } },
    })
    const wrapper = await mountDashboard()

    await wrapper.find('input.scan-cidr').setValue('192.168.8.0/24')
    await wrapper.findAll('button').find((b) => b.text() === '扫描并连接')!.trigger('click')
    await flushPromises()

    expect(h.ElMessage.error).toHaveBeenCalledWith(
      '扫描失败: 已有扫描任务进行中，请等待其完成后再试'
    )
    expect(wrapper.find('.scan-result').exists()).toBe(false)

    wrapper.unmount()
  })

  it('缩略图列：点击加载 → 渲染 img（blob URL + 快照时间 title），失败重试可恢复', async () => {
    mockApi.listDevices.mockResolvedValue([deviceA])
    mockApi.screenshot.mockRejectedValueOnce(new Error('offline'))
    const wrapper = await mountDashboard()

    const cellSel = '.stub-row[data-id="dev-a"]'
    expect(wrapper.find(`${cellSel} .thumb-placeholder`).exists()).toBe(true)

    // 首次加载失败 → 失败重试态
    await wrapper.find(`${cellSel} .thumb-placeholder`).trigger('click')
    await flushPromises()
    expect(wrapper.find(`${cellSel} .thumb-failed`).exists()).toBe(true)
    expect(wrapper.find(`${cellSel} .stub-image`).exists()).toBe(false)

    // 点击重试（force）成功 → img 渲染
    await wrapper.find(`${cellSel} .thumb-failed`).trigger('click')
    await flushPromises()
    const img = wrapper.find(`${cellSel} .stub-image`)
    expect(img.exists()).toBe(true)
    expect(img.attributes('src')).toBe('blob:dash-thumb')
    expect(img.attributes('title')).toContain('快照时间')

    wrapper.unmount()
  })

  it('「刷新缩略图」按钮对全表设备强制重拉', async () => {
    mockApi.listDevices.mockResolvedValue([deviceA, deviceB])
    const wrapper = await mountDashboard()

    const refreshThumbBtn = wrapper.findAll('button').find((b) => b.text() === '刷新缩略图')
    expect(refreshThumbBtn).toBeDefined()
    await refreshThumbBtn!.trigger('click')
    await flushPromises()

    expect(mockApi.screenshot).toHaveBeenCalledWith('dev-a')
    expect(mockApi.screenshot).toHaveBeenCalledWith('192.168.1.33:5555')
    expect(wrapper.find('.stub-row[data-id="dev-a"] .stub-image').exists()).toBe(true)
    expect(wrapper.find('.stub-row[data-id="192.168.1.33:5555"] .stub-image').exists()).toBe(true)

    wrapper.unmount()
  })
})