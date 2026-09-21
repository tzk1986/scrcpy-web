/**
 * LogcatView 组件测试（方案 17 实施项 5）
 * =========================================
 *
 * 覆盖：
 *   - 挂载时不发起任何网络请求（录制默认暂停，后端 logcat 采集保持关闭）
 *   - 开启录制后才连接 WS（filter 消息 paused=false 触发后端采集）并拉历史
 *   - 暂停录制后断开 WS（彻底省连接与后端采集）
 *   - 日志渲染/时间格式化/点击复制、级别过滤与搜索防抖
 *   - 录制开关按钮、刷新/清空/清理、导出、自动滚动
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h, nextTick } from 'vue'
import { flushPromises, shallowMount } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'

const h_ = vi.hoisted(() => {
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
  MockWebSocketService.instances = []
  return { MockWebSocketService }
})

vi.mock('@/services/websocket', () => ({
  WebSocketService: h_.MockWebSocketService,
}))

const mockApi = vi.hoisted(() => ({
  getLogs: vi.fn().mockResolvedValue({ logs: [] }),
  getDebugStats: vi.fn().mockResolvedValue({ db_size_mb: 0 }),
  createDebugSession: vi.fn().mockResolvedValue({ session_id: 's1' }),
  closeDebugSession: vi.fn().mockResolvedValue(undefined),
  execShell: vi.fn().mockResolvedValue({ output: '', success: true }),
  exportLogs: vi.fn().mockResolvedValue(new Blob(['[]'])),
  cleanupSessionLogs: vi.fn().mockResolvedValue(undefined),
}))
vi.mock('@/services/api', () => ({ api: mockApi }))

// ===== Element Plus 轻量 stub（happy-dom 下不引入真实组件库） =====

const ElButton = defineComponent({
  name: 'ElButton',
  props: { loading: Boolean },
  inheritAttrs: false,
  setup(props, { slots, attrs }) {
    return () =>
      h('button', { ...attrs, 'data-loading': String(!!props.loading) }, slots.default?.())
  },
})

const ElSelect = defineComponent({
  name: 'ElSelect',
  props: { modelValue: { type: String as unknown as () => string | null, default: null } },
  emits: ['update:modelValue'],
  setup(_, { slots }) {
    return () => h('div', { class: 'el-select' }, slots.default?.())
  },
})

const ElOption = defineComponent({
  name: 'ElOption',
  props: { label: String, value: String },
  setup(props) {
    return () => h('div', { class: 'el-option', 'data-value': props.value }, props.label)
  },
})

const ElInput = defineComponent({
  name: 'ElInput',
  props: { modelValue: { type: String, default: '' } },
  emits: ['update:modelValue'],
  setup(props, { emit }) {
    return () =>
      h('input', {
        class: 'el-input',
        value: props.modelValue,
        onInput: (e: Event) => emit('update:modelValue', (e.target as HTMLInputElement).value),
      })
  },
})

const ElSwitch = defineComponent({
  name: 'ElSwitch',
  props: { modelValue: Boolean },
  emits: ['update:modelValue'],
  setup() {
    return () => h('span', { class: 'el-switch' })
  },
})

const ElDropdown = defineComponent({
  name: 'ElDropdown',
  emits: ['command'],
  setup(_, { slots }) {
    return () => h('div', { class: 'el-dropdown' }, [slots.default?.(), slots.dropdown?.()])
  },
})

const ElDropdownMenu = defineComponent({
  name: 'ElDropdownMenu',
  setup(_, { slots }) {
    return () => h('div', { class: 'el-dropdown-menu' }, slots.default?.())
  },
})

const ElDropdownItem = defineComponent({
  name: 'ElDropdownItem',
  props: { command: String },
  setup(props, { slots }) {
    return () => h('div', { class: 'el-dropdown-item', 'data-command': props.command }, slots.default?.())
  },
})

function elStubs() {
  return {
    ElButton,
    ElSelect,
    ElOption,
    ElInput,
    ElSwitch,
    ElDropdown,
    ElDropdownMenu,
    ElDropdownItem,
  }
}

import LogcatView from './LogcatView.vue'
import { useDebugStore, type LogEntry } from '@/stores/debug'

let pinia: Pinia

/** 剪贴板写入 spy（happy-dom 的 navigator.clipboard 行为不保证，直接替换）。 */
const clipboard = vi.hoisted(() => ({ writeText: vi.fn().mockResolvedValue(undefined) }))

function makeLogs(): LogEntry[] {
  return [
    { ts: 1700000000, level: 'I', pid: 1, tid: 1, tag: 'TagA', message: 'hello world' },
    { ts: 1700000001, level: 'E', pid: 1, tid: 1, tag: 'TagB', message: 'connection error' },
    { ts: 1700000002, level: 'D', pid: 1, tid: 1, tag: 'TagC', message: 'debug info' },
  ]
}

function mountView() {
  return shallowMount(LogcatView, {
    props: { deviceId: 'dev1' },
    global: { plugins: [pinia], stubs: elStubs() },
  })
}

/** 按可见文本找按钮（stub 按钮渲染为原生 <button>）。 */
function findButton(wrapper: ReturnType<typeof mountView>, text: string) {
  const btn = wrapper.findAll('button').find((b) => b.text().includes(text))
  if (!btn) throw new Error(`button not found: ${text}`)
  return btn
}

beforeEach(() => {
  pinia = createPinia()
  setActivePinia(pinia)
  h_.MockWebSocketService.instances = []
  mockApi.getLogs.mockClear()
  mockApi.getLogs.mockResolvedValue({ logs: [] })
  mockApi.getDebugStats.mockClear()
  mockApi.exportLogs.mockClear()
  mockApi.cleanupSessionLogs.mockClear()
  clipboard.writeText.mockClear()
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: clipboard })
})

afterEach(() => {
  vi.useRealTimers()
})

describe('LogcatView', () => {
  it('挂载时不发起任何网络请求', async () => {
    const store = useDebugStore()
    store.sessionId = 'sess-1'
    mountView()
    await flushPromises()

    expect(h_.MockWebSocketService.instances.length).toBe(0)
    expect(mockApi.getLogs).not.toHaveBeenCalled()
    expect(mockApi.getDebugStats).not.toHaveBeenCalled()
    expect(store.isRecording).toBe(false)
  })

  it('开启录制后连接 WS（paused=false 触发后端采集）并拉历史', async () => {
    const store = useDebugStore()
    store.sessionId = 'sess-1'
    mountView()

    store.setRecording(true)
    // connectWebSocket 内部用 setInterval 轮询 readyState，需等待真实 timer
    await vi.waitFor(() => {
      expect(h_.MockWebSocketService.instances.length).toBe(1)
      const inst = h_.MockWebSocketService.instances[0]
      const filterMsgs = inst.send.mock.calls
        .map(([m]) => m as any)
        .filter((m) => m.op === 'filter')
      expect(filterMsgs.at(-1)).toMatchObject({ paused: false })
    })
    await vi.waitFor(() => expect(mockApi.getLogs).toHaveBeenCalled())
    expect(mockApi.getDebugStats).toHaveBeenCalled()
  })

  it('暂停录制后断开 WS', async () => {
    const store = useDebugStore()
    store.sessionId = 'sess-1'
    mountView()

    store.setRecording(true)
    await flushPromises()
    const inst = h_.MockWebSocketService.instances[0]

    store.setRecording(false)
    await flushPromises()

    expect(inst.close).toHaveBeenCalled()
    expect(store.wsConnected).toBe(false)
  })

  it('渲染日志条目（含格式化时间），点击复制到剪贴板', async () => {
    const store = useDebugStore()
    store.logs = makeLogs()
    const wrapper = mountView()
    await flushPromises()

    const entries = wrapper.findAll('.log-entry')
    expect(entries.length).toBe(3)
    expect(entries[0].text()).toContain('TagA')
    expect(entries[0].text()).toContain('hello world')
    // ts=1700000000 → 22:13:20.000（仅时间部分）
    expect(entries[0].find('.timestamp').text()).toBe('22:13:20.000')
    expect(wrapper.find('.stats-control').text()).toContain('共 3 条')
    expect(wrapper.find('.paused-banner').text()).toContain('当前显示 3 条历史日志')

    await entries[0].trigger('click')
    expect(clipboard.writeText).toHaveBeenCalledWith('22:13:20.000 [I] TagA: hello world')
  })

  it('级别过滤 + 搜索防抖（300ms）驱动列表与计数', async () => {
    const store = useDebugStore()
    store.logs = makeLogs()
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.findAll('.log-entry').length).toBe(3)

    // 搜索关键词（Tag 或 Message 均匹配）
    await wrapper.find('input.el-input').setValue('err')
    await vi.waitFor(() => expect(store.filter.tag).toBe('err'))
    expect(wrapper.findAll('.log-entry').length).toBe(1)
    expect(wrapper.find('.log-entry').text()).toContain('connection error')
    expect(wrapper.find('.stats-control').text()).toContain('显示 1 / 3')

    // 叠加级别过滤（level + search 组合分支）
    wrapper.findComponent(ElSelect).vm.$emit('update:modelValue', 'E')
    await nextTick()
    expect(store.filter.level).toBe('E')
    expect(wrapper.findAll('.log-entry').length).toBe(1)

    // 级别与搜索不匹配 → 空列表
    await wrapper.find('input.el-input').setValue('hello')
    await vi.waitFor(() => expect(store.filter.tag).toBe('hello'))
    expect(wrapper.findAll('.log-entry').length).toBe(0)

    // 清空搜索 → 仅按级别过滤
    await wrapper.find('input.el-input').setValue('')
    await vi.waitFor(() => expect(store.filter.tag).toBe(null))
    expect(wrapper.findAll('.log-entry').length).toBe(1)
    expect(wrapper.find('.log-entry').text()).toContain('connection error')

    // 级别选项渲染（el-option stub 透出 label）
    expect(wrapper.find('.el-select').text()).toContain('Verbose')
    expect(wrapper.find('.el-select').text()).toContain('Fatal')
  })

  it('录制开关按钮：按钮文案、空状态与 LIVE 指示', async () => {
    const store = useDebugStore()
    store.sessionId = 'sess-1'
    const wrapper = mountView()
    await flushPromises()

    expect(findButton(wrapper, '开始').exists()).toBe(true)
    expect(wrapper.find('.empty-state').text()).toContain('已暂停录制，无新日志')

    await findButton(wrapper, '开始').trigger('click')
    await vi.waitFor(() => expect(store.wsConnected).toBe(true))
    // 录制中：按钮切换为暂停、无暂停横幅、空列表提示「开始」、显示 LIVE
    expect(findButton(wrapper, '暂停').exists()).toBe(true)
    expect(wrapper.find('.paused-banner').exists()).toBe(false)
    expect(wrapper.find('.empty-state').text()).toContain('▶ 开始')
    expect(wrapper.find('.status-indicator.live').text()).toBe('LIVE')

    await findButton(wrapper, '暂停').trigger('click')
    await flushPromises()
    expect(store.isRecording).toBe(false)
    expect(store.wsConnected).toBe(false)
    expect(wrapper.find('.status-indicator').exists()).toBe(false)
    expect(wrapper.find('.paused-banner').exists()).toBe(true)
  })

  it('刷新/清空/清理按钮触发对应操作', async () => {
    const store = useDebugStore()
    store.sessionId = 'sess-1'
    store.logs = makeLogs()
    mockApi.getLogs.mockResolvedValueOnce({ logs: [makeLogs()[0]] })
    mockApi.getDebugStats.mockResolvedValue({ db_size_mb: 12.5 })
    const wrapper = mountView()
    await flushPromises()

    // 刷新：重新拉取日志（带当前过滤条件）
    await findButton(wrapper, '刷新').trigger('click')
    await flushPromises()
    expect(mockApi.getLogs).toHaveBeenCalledWith('sess-1', null, null, 1000)
    expect(store.logs.length).toBe(1)
    expect(wrapper.findAll('.log-entry').length).toBe(1)

    // 清空：仅清本地缓存
    await findButton(wrapper, '清空').trigger('click')
    await nextTick()
    expect(store.logs.length).toBe(0)

    // 清理：删除会话日志 + 刷新 DB 统计
    const cleanBtn = findButton(wrapper, '清理')
    expect(cleanBtn.attributes('data-loading')).toBe('false')
    await cleanBtn.trigger('click')
    await flushPromises()
    expect(mockApi.cleanupSessionLogs).toHaveBeenCalledWith('sess-1')
    expect(mockApi.getDebugStats).toHaveBeenCalled()
    expect(store.logs.length).toBe(0)
    expect(findButton(wrapper, '清理').attributes('data-loading')).toBe('false')
    expect(wrapper.find('.db-stats').text()).toContain('DB: 12.5 MB')

    // DB 统计拉取失败：记录错误但不影响清理流程
    const statsErrSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    mockApi.getDebugStats.mockRejectedValueOnce(new Error('stats down'))
    await findButton(wrapper, '清理').trigger('click')
    await flushPromises()
    expect(statsErrSpy).toHaveBeenCalled()
    expect(statsErrSpy.mock.calls.some(([msg]) => String(msg).includes('db stats'))).toBe(true)
    expect(findButton(wrapper, '清理').attributes('data-loading')).toBe('false')
    statsErrSpy.mockRestore()
  })

  it('清理失败时记录错误并复位 loading', async () => {
    const store = useDebugStore()
    store.sessionId = 'sess-1'
    mockApi.cleanupSessionLogs.mockRejectedValueOnce(new Error('db locked'))
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    const wrapper = mountView()
    await flushPromises()

    await findButton(wrapper, '清理').trigger('click')
    await flushPromises()

    expect(errSpy).toHaveBeenCalled()
    expect(errSpy.mock.calls.some(([msg]) => String(msg).includes('cleanup'))).toBe(true)
    expect(findButton(wrapper, '清理').attributes('data-loading')).toBe('false')
    errSpy.mockRestore()
  })

  it('导出日志：JSON/CSV 触发下载，无会话时跳过', async () => {
    const store = useDebugStore()
    store.sessionId = 'sess-1'
    const createObjectURL = vi.fn(() => 'blob:mock-url')
    const revokeObjectURL = vi.fn()
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: createObjectURL })
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: revokeObjectURL })
    let clickedAnchor: HTMLAnchorElement | null = null
    const clickSpy = vi
      .spyOn(HTMLAnchorElement.prototype, 'click')
      .mockImplementation(function (this: HTMLAnchorElement) {
        clickedAnchor = this
      })

    const wrapper = mountView()
    await flushPromises()
    const dropdown = wrapper.findComponent(ElDropdown)

    dropdown.vm.$emit('command', 'json')
    await flushPromises()
    expect(mockApi.exportLogs).toHaveBeenCalledWith('sess-1', 'json', null, null)
    expect(createObjectURL).toHaveBeenCalled()
    expect(clickedAnchor).not.toBeNull()
    expect(clickedAnchor!.download).toBe('logs_sess-1.json')
    expect(clickedAnchor!.href).toContain('blob:mock-url')
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:mock-url')

    dropdown.vm.$emit('command', 'csv')
    await flushPromises()
    expect(clickedAnchor!.download).toBe('logs_sess-1.csv')

    // 无 sessionId 时直接返回
    store.sessionId = null
    mockApi.exportLogs.mockClear()
    dropdown.vm.$emit('command', 'json')
    await flushPromises()
    expect(mockApi.exportLogs).not.toHaveBeenCalled()

    clickSpy.mockRestore()
  })

  it('导出失败时记录错误不崩溃', async () => {
    const store = useDebugStore()
    store.sessionId = 'sess-1'
    mockApi.exportLogs.mockRejectedValueOnce(new Error('export failed'))
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    const wrapper = mountView()
    await flushPromises()

    wrapper.findComponent(ElDropdown).vm.$emit('command', 'json')
    await flushPromises()

    expect(errSpy).toHaveBeenCalled()
    errSpy.mockRestore()
  })

  it('自动滚动：新日志滚到底部，关闭开关后不再滚动', async () => {
    const store = useDebugStore()
    const wrapper = mountView()
    await flushPromises()
    const list = wrapper.find('.log-list').element as HTMLElement
    Object.defineProperty(list, 'scrollHeight', { configurable: true, value: 777 })

    store.logs = makeLogs()
    await flushPromises()
    expect(list.scrollTop).toBe(777)

    // 关闭自动滚动
    wrapper.findComponent(ElSwitch).vm.$emit('update:modelValue', false)
    await nextTick()
    list.scrollTop = 0
    store.logs.push(makeLogs()[0])
    await flushPromises()
    expect(list.scrollTop).toBe(0)
  })
})