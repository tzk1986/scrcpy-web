/**
 * Dashboard 组件测试
 * ====================
 *
 * 覆盖：
 *   - 挂载：拉取设备列表、启动 SSE（EventSource 指向 /api/devices/events）、空态提示、卸载停 SSE
 *   - 重进体验：store 已有设备时静默刷新（不置 loading），冷进入照常置 loading
 *   - 添加设备对话框：打开、填写 IP、点击连接 → connectDevice 调用与成功提示、关闭对话框
 *   - 无线调试配对（方案 47 T5）：页签两步式——配对成功进步骤 2 连接关闭、
 *     失败按后端文案回显停留步骤 1、空端口警告不连接、关闭复位页签与表单
 *   - SSE 事件驱动设备列表增删；点击刷新重新拉取
 *   - 网段扫描（方案 36）：参数透传（含仅扫描不连接）、loading 文案切换、结果 alert 回显、失败提示
 *   - 缩略图列（方案 36）：点击加载 → img 渲染（blob URL + title）、失败重试、刷新缩略图强制重拉
 *
 * 说明：Element Plus 的 el-* 组件以轻量 stub 替代；el-form stub 提供 validate()
 * 供 handleConnect 的表单校验调用。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, shallowMount, type DOMWrapper, type VueWrapper } from '@vue/test-utils'
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
    ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
    ElMessageBox: { confirm: vi.fn() },
  }
})

const mockApi = vi.hoisted(() => ({
  listDevices: vi.fn(),
  connectDevice: vi.fn(),
  pairDevice: vi.fn(),
  disconnectDevice: vi.fn(),
  getDevice: vi.fn(),
  scanSubnet: vi.fn(),
  screenshot: vi.fn(),
}))
vi.mock('@/services/api', () => ({ api: mockApi }))
vi.mock('element-plus', () => ({
  ElMessage: h.ElMessage,
  ElMessageBox: h.ElMessageBox,
}))

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
  emits: ['selection-change'],
  setup(props, { slots, expose }) {
    provide('tableData', () => props.data as Array<Record<string, unknown>>)
    // 方案 36 增补 D10：clearSelection 暴露给测试断言（批量断开后调用）
    const clearSelection = vi.fn()
    expose({ clearSelection })
    return () => vueH('div', { class: 'stub-table' }, slots.default?.())
  },
})

const ElTableColumnStub = defineComponent({
  name: 'ElTableColumn',
  setup(_props, { slots }) {
    const tableData = inject<(() => Array<Record<string, unknown>>) | undefined>('tableData')
    return () => {
      const slot = slots.default
      // 无 default slot 的列（如 type=selection）仍渲染根元素：
      // type/selectable 等 props 经 fallthrough attrs 可达，供测试断言
      if (!slot || !tableData) return vueH('div', { class: 'stub-column stub-no-slot' })
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

const ElPopoverStub = {
  name: 'ElPopover',
  template: '<div class="stub-popover"><slot /><slot name="reference" /></div>',
}

// 方案 47 T5：el-tabs 轻量替身——provide 当前 modelValue，pane 仅激活者渲染内容
// （模拟真实懒渲染，避免非激活页签污染既有 ElInput 定位）
const ElTabsStub = defineComponent({
  name: 'ElTabs',
  props: { modelValue: { type: String, default: '' } },
  emits: ['update:modelValue'],
  setup(props, { slots }) {
    provide('elTabsModel', () => props.modelValue)
    return () => vueH('div', { class: 'stub-tabs' }, slots.default?.())
  },
})

const ElTabPaneStub = defineComponent({
  name: 'ElTabPane',
  props: { label: { type: String, default: '' }, name: { type: String, default: '' } },
  setup(props, { slots }) {
    const model = inject<(() => string) | undefined>('elTabsModel')
    const active = () => !model || model() === props.name
    return () => {
      const children = active()
        ? [vueH('div', { class: 'stub-tab-label' }, props.label), ...(slots.default?.() ?? [])]
        : undefined
      return vueH(
        'div',
        { class: ['stub-tab-pane', { 'stub-inactive': !active() }] },
        children
      )
    }
  },
})

/** 扫描结果样例：开放 2 台，1 成功 1 connect_failed（方案 36） */
function scanFixture(overrides: Record<string, unknown> = {}) {
  return {
    label: '192.168.8.0/24',
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

/** 勾选原生 checkbox 输入（setChecked 为测试库私有 API，改用手动 checked + change） */
async function checkRowCheckbox(input: DOMWrapper<Element>) {
  ;(input.element as HTMLInputElement).checked = true
  await input.trigger('change')
}

async function mountDashboard(options: { pinia?: Pinia } = {}): Promise<VueWrapper> {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'dashboard', component: { template: '<div />' } },
      { path: '/device/:id', name: 'device-detail', component: { template: '<div />' } },
    ],
  })
  await router.push('/')
  await router.isReady()

  pinia = options.pinia ?? createPinia()
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
        'el-popover': ElPopoverStub,
        'el-tabs': ElTabsStub,
        'el-tab-pane': ElTabPaneStub,
      },
      directives: { loading: {} },
    },
  })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  localStorage.clear()
  vi.stubGlobal('EventSource', h.MockEventSource)
  h.MockEventSource.instances = []
  mockApi.listDevices.mockReset()
  mockApi.connectDevice.mockReset()
  mockApi.pairDevice.mockReset()
  mockApi.disconnectDevice.mockReset()
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
  h.ElMessage.warning.mockClear()
  h.ElMessageBox.confirm.mockReset().mockResolvedValue('confirm')
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

  it('配对页签两步式：配对成功进步骤 2，连接后关闭对话框', async () => {
    mockApi.listDevices.mockResolvedValue([])
    mockApi.pairDevice.mockResolvedValue({ success: true, paired: '192.168.1.5:37123' })
    mockApi.connectDevice.mockResolvedValue({ success: true, device_id: '192.168.1.5:41234' })
    const wrapper = await mountDashboard()

    const addBtn = wrapper.findAll('button').find((b) => b.text().includes('添加设备'))
    await addBtn!.trigger('click')
    await nextTick()
    const dialog = wrapper.find('.stub-dialog')

    // 切到配对页签（stub 经 update:modelValue 驱动 activeTab）
    dialog.findComponent({ name: 'ElTabs' }).vm.$emit('update:modelValue', 'pair')
    await nextTick()
    expect(dialog.text()).toContain('配对端口')

    // 步骤 1：配对页签内三个输入——IP、配对端口（input-number 同桩名）、配对码
    const step1Inputs = dialog.findAllComponents({ name: 'ElInput' })
    expect(step1Inputs).toHaveLength(3)
    step1Inputs[0].vm.$emit('update:modelValue', '192.168.1.5')
    step1Inputs[1].vm.$emit('update:modelValue', 37123)
    step1Inputs[2].vm.$emit('update:modelValue', '123456')
    await nextTick()

    const pairBtn = dialog.findAll('button').find((b) => b.text() === '配对')
    await pairBtn!.trigger('click')
    await flushPromises()
    expect(mockApi.pairDevice).toHaveBeenCalledWith('192.168.1.5', 37123, '123456')
    expect(h.ElMessage.success).toHaveBeenCalledWith(expect.stringContaining('无线调试'))

    // 步骤 2：只剩调试端口输入（label 不经 stub 渲染，以步骤 2 独有文案判位）；
    // 空端口点击 → 警告且不连接
    expect(dialog.text()).toContain('返回重新配对')
    const step2Inputs = dialog.findAllComponents({ name: 'ElInput' })
    expect(step2Inputs).toHaveLength(1)
    const connectBtn = dialog.findAll('button').find((b) => b.text() === '连接')
    await connectBtn!.trigger('click')
    await flushPromises()
    expect(h.ElMessage.warning).toHaveBeenCalledWith(expect.stringContaining('端口'))
    expect(mockApi.connectDevice).not.toHaveBeenCalled()

    // 填端口后连接：走既有 connectDevice 链路并关闭对话框
    step2Inputs[0].vm.$emit('update:modelValue', 41234)
    await nextTick()
    await connectBtn!.trigger('click')
    await flushPromises()
    expect(mockApi.connectDevice).toHaveBeenCalledWith('192.168.1.5', 41234)
    await flushPromises()
    expect(wrapper.find('.stub-dialog').exists()).toBe(false)
  })

  it('配对失败：回显后端分类文案且停留步骤 1', async () => {
    mockApi.listDevices.mockResolvedValue([])
    mockApi.pairDevice.mockRejectedValue({
      response: {
        data: { error: { code: 'PAIR_FAILED', message: '配对失败：配对码不正确或已过期，请核对手机弹窗显示的 6 位配对码后重试' } },
      },
    })
    const wrapper = await mountDashboard()

    const addBtn = wrapper.findAll('button').find((b) => b.text().includes('添加设备'))
    await addBtn!.trigger('click')
    await nextTick()
    const dialog = wrapper.find('.stub-dialog')
    dialog.findComponent({ name: 'ElTabs' }).vm.$emit('update:modelValue', 'pair')
    await nextTick()

    const inputs = dialog.findAllComponents({ name: 'ElInput' })
    inputs[0].vm.$emit('update:modelValue', '192.168.1.5')
    inputs[1].vm.$emit('update:modelValue', 37123)
    inputs[2].vm.$emit('update:modelValue', '000000')
    await nextTick()

    const pairBtn = dialog.findAll('button').find((b) => b.text() === '配对')
    await pairBtn!.trigger('click')
    await flushPromises()

    expect(h.ElMessage.error).toHaveBeenCalledWith(expect.stringContaining('配对码不正确或已过期'))
    // 仍停留步骤 1（未出现步骤 2 独有的「返回重新配对」）
    expect(dialog.text()).toContain('配对码')
    expect(dialog.text()).not.toContain('返回重新配对')
  })

  it('对话框关闭复位：重开回网络 ADB 页签且配对表单清空', async () => {
    mockApi.listDevices.mockResolvedValue([])
    mockApi.pairDevice.mockResolvedValue({ success: true, paired: '192.168.1.5:37123' })
    const wrapper = await mountDashboard()

    const addBtn = wrapper.findAll('button').find((b) => b.text().includes('添加设备'))
    await addBtn!.trigger('click')
    await nextTick()
    let dialog = wrapper.find('.stub-dialog')
    dialog.findComponent({ name: 'ElTabs' }).vm.$emit('update:modelValue', 'pair')
    await nextTick()
    const inputs = dialog.findAllComponents({ name: 'ElInput' })
    inputs[0].vm.$emit('update:modelValue', '192.168.1.5')
    inputs[1].vm.$emit('update:modelValue', 37123)
    inputs[2].vm.$emit('update:modelValue', '123456')
    await nextTick()
    const pairBtn = dialog.findAll('button').find((b) => b.text() === '配对')
    await pairBtn!.trigger('click')
    await flushPromises()
    expect(dialog.text()).toContain('返回重新配对') // 已到步骤 2

    // 模拟真实 el-dialog 关闭动画后的 closed 事件 → resetDialog
    dialog.findComponent({ name: 'ElDialog' }).vm.$emit('closed')
    await nextTick()

    // 重开：默认回网络 ADB 页签；切到配对页签为清空后的步骤 1
    await addBtn!.trigger('click')
    await nextTick()
    dialog = wrapper.find('.stub-dialog')
    expect(dialog.text()).toContain('adb tcpip 5555')
    dialog.findComponent({ name: 'ElTabs' }).vm.$emit('update:modelValue', 'pair')
    await nextTick()
    expect(dialog.text()).toContain('6 位配对码')
    expect(dialog.text()).not.toContain('返回重新配对')
    const reopened = dialog.findAllComponents({ name: 'ElInput' })
    expect((reopened[0].element as HTMLInputElement).value).toBe('')
    expect((reopened[2].element as HTMLInputElement).value).toBe('')
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

  it('重进 Dashboard：store 已有设备时静默刷新，不置 loading 遮罩', async () => {
    mockApi.listDevices.mockResolvedValue([deviceA])
    const first = await mountDashboard()
    const store = useDeviceStore()
    first.unmount()
    expect(store.devices.map((d) => d.id)).toEqual(['dev-a'])

    // 二次挂载复用同一 pinia（模拟从录屏/设置切回），列表请求挂起观察 loading
    mockApi.listDevices.mockImplementation(() => new Promise(() => {}))
    const second = await mountDashboard({ pinia })
    expect(store.loading).toBe(false)
    expect(store.devices.map((d) => d.id)).toEqual(['dev-a'])
    second.unmount()
  })

  it('空 store 冷进入：挂载仍非静默（置 loading 遮罩）', async () => {
    mockApi.listDevices.mockImplementation(() => new Promise(() => {}))
    const wrapper = await mountDashboard()
    const store = useDeviceStore()

    expect(store.loading).toBe(true)

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
    // 挂载时 D7 自动加载 dev-a（1 次）+ 扫描后 ok 设备（1 次）；失败设备不加载
    expect(mockApi.screenshot).toHaveBeenCalledWith('dev-a')
    expect(mockApi.screenshot).toHaveBeenCalledTimes(2)

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

  it('扫描进行中回车不重复触发请求（终评 M-1）', async () => {
    mockApi.listDevices.mockResolvedValue([])
    let resolveScan!: (v: unknown) => void
    mockApi.scanSubnet.mockImplementation(() => new Promise((res) => { resolveScan = res }))
    const wrapper = await mountDashboard()

    const input = wrapper.find('input.scan-cidr')
    await input.setValue('192.168.8.0/24')
    await input.trigger('keyup.enter')
    await input.trigger('keyup.enter') // 扫描中再按回车
    await flushPromises()

    expect(mockApi.scanSubnet).toHaveBeenCalledTimes(1)

    resolveScan(scanFixture())
    await flushPromises()
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

  it('缩略图列：D7 自动加载失败 → 点击重试成功渲染 img（blob URL + 快照时间 title）', async () => {
    mockApi.listDevices.mockResolvedValue([deviceA])
    mockApi.screenshot.mockRejectedValueOnce(new Error('offline'))
    const wrapper = await mountDashboard()
    await flushPromises()

    // D7 挂载即自动加载（「点击加载」占位不复现于稳定态）→ 失败呈现重试态
    const cellSel = '.stub-row[data-id="dev-a"]'
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

  // ==================== 方案 36 增补 D9：开放主机清单可交互 ====================

  it('扫描记录独立卡片：已连接行不呈现、失败/未连接两分类呈现，alert 内不再有清单（第三批）', async () => {
    mockApi.listDevices.mockResolvedValue([deviceA])
    mockApi.scanSubnet.mockResolvedValue(
      scanFixture({ open_hosts: ['192.168.8.18', '192.168.8.25', '192.168.8.30'] })
    )
    const wrapper = await mountDashboard()

    await wrapper.find('input.scan-cidr').setValue('192.168.8.0/24')
    await wrapper.findAll('button').find((b) => b.text() === '扫描并连接')!.trigger('click')
    await flushPromises()

    // 独立卡片（设备表格上方），已连接行（.18）不进入记录
    const card = wrapper.find('.scan-record-card')
    expect(card.exists()).toBe(true)
    expect(card.find('.scan-record-title').text()).toBe('扫描记录')
    const rows = wrapper.findAll('.scan-record-row')
    expect(rows).toHaveLength(2)
    expect(rows.some((r) => r.text().includes('192.168.8.18'))).toBe(false)

    // .25 connect_failed → 连接失败（悬停 message）+ 重试按钮
    const row25 = rows.find((r) => r.text().includes('192.168.8.25'))!
    expect(row25.find('.stub-tag').text()).toBe('连接失败')
    expect(row25.find('.stub-tag').attributes('title')).toContain('握手超时')
    expect(row25.findAll('button').find((b) => b.text() === '重试')).toBeDefined()

    // .30 不在 connect_results → 未连接 + 连接按钮
    const row30 = rows.find((r) => r.text().includes('192.168.8.30'))!
    expect(row30.find('.stub-tag').text()).toBe('未连接')
    expect(row30.findAll('button').find((b) => b.text() === '连接')).toBeDefined()

    // alert 仅保留摘要文本，不再内嵌清单
    expect(wrapper.find('.scan-result .scan-record-row').exists()).toBe(false)

    wrapper.unmount()
  })

  it('记录行「连接」透传扫描端口快照；成功后行移出卡片且持久化修剪（第三批）', async () => {
    mockApi.listDevices.mockResolvedValue([deviceA])
    mockApi.scanSubnet.mockResolvedValue(
      scanFixture({ open_hosts: ['192.168.8.30'], connect_results: [] })
    )
    mockApi.connectDevice.mockResolvedValue({ success: true, device_id: '192.168.8.30:5555' })
    const wrapper = await mountDashboard()

    await wrapper.find('input.scan-cidr').setValue('192.168.8.0/24')
    await wrapper.findAll('button').find((b) => b.text() === '扫描并连接')!.trigger('click')
    await flushPromises()

    // 扫描完成后中途改端口表单：连接动作必须用扫描时的端口快照（防错位）
    await wrapper.find('input.scan-port').setValue('6666')
    const row30 = wrapper.find('.scan-record-row')
    await row30.findAll('button').find((b) => b.text() === '连接')!.trigger('click')
    await flushPromises()

    expect(mockApi.connectDevice).toHaveBeenCalledWith('192.168.8.30', 5555)
    expect(h.ElMessage.success).toHaveBeenCalledWith('已连接 192.168.8.30:5555')
    // 成功后行移出：记录为空 → 卡片整体隐藏
    expect(wrapper.find('.scan-record-card').exists()).toBe(false)
    // localStorage 同步修剪
    const data = JSON.parse(localStorage.getItem('oscrpy-scan-record')!)
    expect(data.hosts).toEqual([])

    wrapper.unmount()
  })

  // ==================== 方案 36 增补 D10：选择 + 批量断开 ====================

  it('selection 列 selectable 按行对象行使 TCP 判定：USB 不可勾选、TCP 可勾选（D10）', async () => {
    mockApi.listDevices.mockResolvedValue([deviceA, deviceB])
    const wrapper = await mountDashboard()

    const cols = wrapper.findAllComponents({ name: 'ElTableColumn' })
    const selectionCol = cols.find((c) => c.attributes('type') === 'selection')
    expect(selectionCol).toBeDefined()
    // ElTableColumnStub 未声明 props：selectable 函数经 fallthrough attrs 可达，
    // 直接以 EP 真实调用形态（传 row 对象）执行断言
    const selectable = (selectionCol!.vm.$attrs as { selectable: (row: unknown) => boolean })
      .selectable
    expect(typeof selectable).toBe('function')
    expect(selectable(deviceA)).toBe(false)
    expect(selectable(deviceB)).toBe(true)

    wrapper.unmount()
  })

  it('选择 TCP 设备出现工具条；批量断开经确认逐台调用并清除选择（D10）', async () => {
    mockApi.listDevices.mockResolvedValue([deviceA, deviceB])
    mockApi.disconnectDevice.mockResolvedValue({ success: true })
    const wrapper = await mountDashboard()

    // 未选择时无工具条
    expect(wrapper.find('.batch-bar').exists()).toBe(false)

    // 模拟表格 selection-change：选中 TCP 设备 deviceB
    const tableVm = wrapper.findComponent({ name: 'ElTable' }).vm as unknown as {
      clearSelection: ReturnType<typeof vi.fn>
    }
    wrapper.findComponent({ name: 'ElTable' }).vm.$emit('selection-change', [deviceB])
    await nextTick()

    const bar = wrapper.find('.batch-bar')
    expect(bar.exists()).toBe(true)
    expect(bar.text()).toContain('已选 1 台')

    // 批量断开：先走 ElMessageBox.confirm 确认
    await bar.findAll('button').find((b) => b.text() === '批量断开')!.trigger('click')
    await flushPromises()

    expect(h.ElMessageBox.confirm).toHaveBeenCalled()
    expect(mockApi.disconnectDevice).toHaveBeenCalledWith('192.168.1.33:5555')
    expect(mockApi.disconnectDevice).toHaveBeenCalledTimes(1)
    expect(h.ElMessage.success).toHaveBeenCalledWith('已断开 1 台设备')
    // 完成后显式清除选择：工具条消失 + tableRef.clearSelection 被调
    expect(wrapper.find('.batch-bar').exists()).toBe(false)
    expect(tableVm.clearSelection).toHaveBeenCalled()

    wrapper.unmount()
  })

  it('批量断开确认取消不调用断开；工具条「取消选择」显式清空（D10）', async () => {
    mockApi.listDevices.mockResolvedValue([deviceA, deviceB])
    h.ElMessageBox.confirm.mockRejectedValue('cancel')
    const wrapper = await mountDashboard()

    const tableVm = wrapper.findComponent({ name: 'ElTable' }).vm as unknown as {
      clearSelection: ReturnType<typeof vi.fn>
    }
    wrapper.findComponent({ name: 'ElTable' }).vm.$emit('selection-change', [deviceB])
    await nextTick()

    // 确认取消 → 不调用断开
    const bar = wrapper.find('.batch-bar')
    await bar.findAll('button').find((b) => b.text() === '批量断开')!.trigger('click')
    await flushPromises()
    expect(mockApi.disconnectDevice).not.toHaveBeenCalled()

    // 再次选中后走「取消选择」
    wrapper.findComponent({ name: 'ElTable' }).vm.$emit('selection-change', [deviceB])
    await nextTick()
    await wrapper.findAll('button').find((b) => b.text() === '取消选择')!.trigger('click')
    expect(tableVm.clearSelection).toHaveBeenCalled()

    wrapper.unmount()
  })

  it('批量断开部分失败走 warning 汇总提示（D10）', async () => {
    const deviceB2 = { ...deviceB, id: '192.168.1.34:5555' }
    mockApi.listDevices.mockResolvedValue([deviceA, deviceB, deviceB2])
    mockApi.disconnectDevice
      .mockResolvedValueOnce({ success: false })
      .mockResolvedValue({ success: true })
    const wrapper = await mountDashboard()

    wrapper.findComponent({ name: 'ElTable' }).vm.$emit('selection-change', [deviceB, deviceB2])
    await nextTick()
    await wrapper.findAll('button').find((b) => b.text() === '批量断开')!.trigger('click')
    await flushPromises()

    expect(mockApi.disconnectDevice).toHaveBeenCalledTimes(2)
    expect(h.ElMessage.warning).toHaveBeenCalled()
    expect(h.ElMessage.success).not.toHaveBeenCalled()

    wrapper.unmount()
  })

  // ==================== 方案 36 第三批：扫描记录独立卡片 + localStorage ====================

  it('扫描完成后写入 localStorage：label/端口/时间/未连接主机清单（第三批）', async () => {
    mockApi.listDevices.mockResolvedValue([])
    mockApi.scanSubnet.mockResolvedValue(scanFixture())
    const wrapper = await mountDashboard()

    await wrapper.find('input.scan-cidr').setValue('192.168.8.0/24')
    await wrapper.findAll('button').find((b) => b.text() === '扫描并连接')!.trigger('click')
    await flushPromises()

    const raw = localStorage.getItem('oscrpy-scan-record')
    expect(raw).toBeTruthy()
    const data = JSON.parse(raw!)
    expect(data.label).toBe('192.168.8.0/24')
    expect(data.port).toBe(5555)
    expect(typeof data.scannedAt).toBe('number')
    // 已连接（.18 ok）不入记录；失败主机带原因
    expect(data.hosts).toEqual([{ ip: '192.168.8.25', failed: '握手超时' }])

    wrapper.unmount()
  })

  it('页载恢复扫描记录：卡片呈现、端口快照取记录内端口（第三批）', async () => {
    localStorage.setItem(
      'oscrpy-scan-record',
      JSON.stringify({
        label: '10.0.0.0/24',
        port: 6666,
        scannedAt: 1700000000000,
        hosts: [{ ip: '10.0.0.5' }],
      })
    )
    mockApi.listDevices.mockResolvedValue([])
    mockApi.connectDevice.mockResolvedValue({ success: true, device_id: '10.0.0.5:6666' })
    const wrapper = await mountDashboard()

    const card = wrapper.find('.scan-record-card')
    expect(card.exists()).toBe(true)
    expect(card.find('.scan-record-meta').text()).toContain('10.0.0.0/24')
    expect(card.find('.scan-record-meta').text()).toContain('6666')
    expect(card.find('.scan-record-row').text()).toContain('10.0.0.5')

    // 恢复出口：行内连接用记录里存的端口（非表单当前 5555）
    await card
      .find('.scan-record-row')
      .findAll('button')
      .find((b) => b.text() === '连接')!
      .trigger('click')
    await flushPromises()
    expect(mockApi.connectDevice).toHaveBeenCalledWith('10.0.0.5', 6666)

    wrapper.unmount()
  })

  it('「清除记录」删除卡片与 localStorage（第三批）', async () => {
    mockApi.listDevices.mockResolvedValue([])
    mockApi.scanSubnet.mockResolvedValue(
      scanFixture({ open_hosts: ['192.168.8.30'], connect_results: [] })
    )
    const wrapper = await mountDashboard()

    await wrapper.find('input.scan-cidr').setValue('192.168.8.0/24')
    await wrapper.findAll('button').find((b) => b.text() === '扫描并连接')!.trigger('click')
    await flushPromises()
    expect(wrapper.find('.scan-record-card').exists()).toBe(true)

    await wrapper.findAll('button').find((b) => b.text() === '清除记录')!.trigger('click')
    expect(wrapper.find('.scan-record-card').exists()).toBe(false)
    expect(localStorage.getItem('oscrpy-scan-record')).toBeNull()

    wrapper.unmount()
  })

  it('记录行勾选出现批量工具条；「连接所选」逐台连接并汇总成功（第三批）', async () => {
    mockApi.listDevices.mockResolvedValue([])
    mockApi.scanSubnet.mockResolvedValue(
      scanFixture({ open_hosts: ['192.168.8.30', '192.168.8.31'], connect_results: [] })
    )
    mockApi.connectDevice.mockResolvedValue({ success: true, device_id: 'x' })
    const wrapper = await mountDashboard()

    await wrapper.find('input.scan-cidr').setValue('192.168.8.0/24')
    await wrapper.findAll('button').find((b) => b.text() === '扫描并连接')!.trigger('click')
    await flushPromises()

    // 无勾选无工具条
    expect(wrapper.find('.record-batch-bar').exists()).toBe(false)

    const checkboxes = wrapper.findAll('.scan-record-row input[type="checkbox"]')
    expect(checkboxes).toHaveLength(2)
    await checkRowCheckbox(checkboxes[0])
    await checkRowCheckbox(checkboxes[1])

    const bar = wrapper.find('.record-batch-bar')
    expect(bar.exists()).toBe(true)
    expect(bar.text()).toContain('已选 2 台')

    await bar.findAll('button').find((b) => b.text() === '连接所选')!.trigger('click')
    await flushPromises()

    expect(mockApi.connectDevice).toHaveBeenCalledTimes(2)
    expect(mockApi.connectDevice).toHaveBeenCalledWith('192.168.8.30', 5555)
    expect(mockApi.connectDevice).toHaveBeenCalledWith('192.168.8.31', 5555)
    expect(h.ElMessage.success).toHaveBeenCalledWith('已连接 2 台设备')

    wrapper.unmount()
  })

  it('批量连接部分失败：失败台及时报错、结束 warning 汇总「成功 X 失败 Y」（第三批）', async () => {
    mockApi.listDevices.mockResolvedValue([])
    mockApi.scanSubnet.mockResolvedValue(
      scanFixture({ open_hosts: ['192.168.8.30', '192.168.8.31'], connect_results: [] })
    )
    mockApi.connectDevice
      .mockResolvedValueOnce({ success: true, device_id: '192.168.8.30:5555' })
      .mockRejectedValueOnce(new Error('无响应'))
    const wrapper = await mountDashboard()

    await wrapper.find('input.scan-cidr').setValue('192.168.8.0/24')
    await wrapper.findAll('button').find((b) => b.text() === '扫描并连接')!.trigger('click')
    await flushPromises()

    const checkboxes = wrapper.findAll('.scan-record-row input[type="checkbox"]')
    await checkRowCheckbox(checkboxes[0])
    await checkRowCheckbox(checkboxes[1])

    await wrapper
      .find('.record-batch-bar')
      .findAll('button')
      .find((b) => b.text() === '连接所选')!
      .trigger('click')
    await flushPromises()

    // 失败台即时报错 + 结束汇总（有成功有失败 → warning）
    expect(h.ElMessage.error).toHaveBeenCalled()
    expect(h.ElMessage.warning).toHaveBeenCalledWith('成功 1 台，失败 1 台')
    expect(h.ElMessage.success).not.toHaveBeenCalled()

    wrapper.unmount()
  })

  it('批量工具条「取消选择」清空勾选不发起连接（第三批）', async () => {
    mockApi.listDevices.mockResolvedValue([])
    mockApi.scanSubnet.mockResolvedValue(
      scanFixture({ open_hosts: ['192.168.8.30'], connect_results: [] })
    )
    const wrapper = await mountDashboard()

    await wrapper.find('input.scan-cidr').setValue('192.168.8.0/24')
    await wrapper.findAll('button').find((b) => b.text() === '扫描并连接')!.trigger('click')
    await flushPromises()

    await checkRowCheckbox(wrapper.find('.scan-record-row input[type="checkbox"]'))
    const bar = wrapper.find('.record-batch-bar')
    expect(bar.exists()).toBe(true)

    await bar.findAll('button').find((b) => b.text() === '取消选择')!.trigger('click')
    expect(wrapper.find('.record-batch-bar').exists()).toBe(false)
    expect(mockApi.connectDevice).not.toHaveBeenCalled()

    wrapper.unmount()
  })

  it('记录行单台连接失败：即时 error 提示、行转「连接失败」可重试（第三批）', async () => {
    mockApi.listDevices.mockResolvedValue([])
    mockApi.scanSubnet.mockResolvedValue(
      scanFixture({ open_hosts: ['192.168.8.30'], connect_results: [] })
    )
    mockApi.connectDevice.mockRejectedValue(new Error('无响应'))
    const wrapper = await mountDashboard()

    await wrapper.find('input.scan-cidr').setValue('192.168.8.0/24')
    await wrapper.findAll('button').find((b) => b.text() === '扫描并连接')!.trigger('click')
    await flushPromises()

    const row = wrapper.find('.scan-record-row')
    expect(row.find('.stub-tag').text()).toBe('未连接')

    await row.findAll('button').find((b) => b.text() === '连接')!.trigger('click')
    await flushPromises()

    expect(h.ElMessage.error).toHaveBeenCalledWith('连接失败: 无响应')
    expect(row.find('.stub-tag').text()).toBe('连接失败')
    expect(row.findAll('button').find((b) => b.text() === '重试')).toBeDefined()

    wrapper.unmount()
  })

  it('屏幕列对齐：缩略图容器四态同尺寸居中，刷新按钮角标叠加不撑高行（第三批）', async () => {
    mockApi.listDevices.mockResolvedValue([deviceA, deviceB])
    const wrapper = await mountDashboard()
    await flushPromises()

    const cells = wrapper.findAll('.stub-row .thumb-cell')
    expect(cells.length).toBeGreaterThan(0)
    for (const cell of cells) {
      const style = cell.attributes('style') ?? ''
      expect(style).toContain('width: 96px')
      expect(style).toContain('height: 72px')
    }

    // 刷新按钮角标叠加（absolute 定位），不参与流式布局（避免撑高行导致参差）
    const refreshBtn = wrapper.find('.stub-row[data-id="dev-a"] .thumb-refresh')
    expect(refreshBtn.exists()).toBe(true)
    expect(refreshBtn.attributes('style') ?? '').toContain('position: absolute')

    wrapper.unmount()
  })

  it('屏幕与分辨率列居中对齐：两列 align=center，缩略图与分辨率文本横向同轴', async () => {
    mockApi.listDevices.mockResolvedValue([deviceA])
    const wrapper = await mountDashboard()
    await flushPromises()

    // 无 slot 列与有 slot 列的 align prop 均经 fallthrough attrs 落在根元素
    const screenCol = wrapper
      .findAll('.stub-column')
      .find((c) => c.find('.thumb-cell').exists())
    const resCol = wrapper
      .findAll('.stub-column')
      .find((c) => c.text().includes('1080x2400'))
    expect(screenCol).toBeDefined()
    expect(resCol).toBeDefined()
    expect(screenCol!.attributes('align')).toBe('center')
    expect(resCol!.attributes('align')).toBe('center')

    wrapper.unmount()
  })

  it('设备ID 列收窄：min-width 140 + 溢出省略，压缩与屏幕列间空隙', async () => {
    mockApi.listDevices.mockResolvedValue([deviceA])
    const wrapper = await mountDashboard()
    await flushPromises()

    // prop="id" 列无 slot：column props 经 fallthrough attrs 落在 stub-no-slot 根
    const idCol = wrapper
      .findAll('.stub-column.stub-no-slot')
      .find((c) => c.attributes('prop') === 'id')
    expect(idCol).toBeDefined()
    expect(idCol!.attributes('min-width')).toBe('140')
    expect(idCol!.attributes()).toHaveProperty('show-overflow-tooltip')

    wrapper.unmount()
  })

  // ==================== 方案 36 增补 D11：输入说明 ====================

  it('扫描输入框 placeholder 更新，帮助 popover 说明三种输入形态（D11）', async () => {
    mockApi.listDevices.mockResolvedValue([])
    const wrapper = await mountDashboard()

    expect(wrapper.find('input.scan-cidr').attributes('placeholder'))
      .toBe('如 192.168.8.0/24 或 192.168.8.10-100')

    const pop = wrapper.find('.stub-popover .scan-help-body')
    expect(pop.exists()).toBe(true)
    const text = pop.text()
    expect(text).toContain('192.168.8.18')
    expect(text).toContain('192.168.8.0/24')
    expect(text).toContain('192.168.8.10-100')
    expect(text).toContain('192.168.8.10-192.168.9.20')
    expect(text).toContain('仅扫描不连接')

    wrapper.unmount()
  })
})
