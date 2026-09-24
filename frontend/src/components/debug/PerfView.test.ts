/**
 * PerfView 组件测试
 * ==================
 *
 * 覆盖：
 *   - 挂载：连接 /ws/perf/{deviceId}，4 张指标卡片与 2 个图表的空态初始渲染
 *   - WS 推送指标：卡片数值/当前应用更新，数据点计数正确，超过 60 点 FIFO 裁剪
 *   - 非法 JSON 不影响已有数据点；卸载时关闭 WebSocket
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, shallowMount } from '@vue/test-utils'
import { defineComponent, h as hVue, nextTick } from 'vue'

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
    setMessageHandler = vi.fn((handler: (data: unknown) => void) => {
      this.messageHandler = handler
    })
    setCloseHandler = vi.fn((handler: () => void) => {
      this.closeHandler = handler
    })
    setErrorHandler = vi.fn((handler: (e: unknown) => void) => {
      this.errorHandler = handler
    })

    constructor(url: string) {
      this.url = url
      MockWebSocketService.instances.push(this)
    }
  }
  MockWebSocketService.instances = []
  return { MockWebSocketService }
})

vi.mock('@/services/websocket', () => ({ WebSocketService: h.MockWebSocketService }))

const mockApi = vi.hoisted(() => ({
  getPerfRecordStatus: vi.fn().mockResolvedValue({
    recording: false, reason: '', rows: 0, oldest_ts: null, newest_ts: null,
  }),
  startPerfRecording: vi.fn().mockResolvedValue({
    recording: true, reason: '', rows: 0, oldest_ts: null, newest_ts: null,
  }),
  stopPerfRecording: vi.fn().mockResolvedValue({
    recording: false, reason: 'stopped', rows: 12, oldest_ts: null, newest_ts: null,
  }),
  exportPerfMetrics: vi.fn().mockResolvedValue({
    data: new Blob(['x']),
    headers: { 'x-export-count': '42', 'x-export-oldest-ts': '1710000000', 'x-export-newest-ts': '1710003600' },
  }),
}))
vi.mock('@/services/api', () => ({ api: mockApi }))

const elMessage = vi.hoisted(() => ({
  success: vi.fn(), error: vi.fn(), warning: vi.fn(),
}))
vi.mock('element-plus', () => ({ ElMessage: elMessage }))

import PerfView from './PerfView.vue'

const metrics = {
  ts: 1710000000,
  cpu_percent: 45.2,
  total_memory_mb: 4096,
  used_memory_mb: 2048,
  fps: 58,
  jank_count: 0,
  current_activity: 'com.example/.MainActivity',
  top_package: 'com.example.app',
}

const ElButton = defineComponent({
  name: 'ElButton',
  props: { loading: Boolean },
  inheritAttrs: false,
  setup(props, { slots, attrs }) {
    return () =>
      hVue('button', { ...attrs, 'data-loading': String(!!props.loading) }, slots.default?.())
  },
})

const ElDropdown = defineComponent({
  name: 'ElDropdown',
  emits: ['command'],
  setup(_, { slots }) {
    return () => hVue('div', { class: 'el-dropdown' }, [slots.default?.(), slots.dropdown?.()])
  },
})

const ElDropdownMenu = defineComponent({
  name: 'ElDropdownMenu',
  setup(_, { slots }) {
    return () => hVue('div', { class: 'el-dropdown-menu' }, slots.default?.())
  },
})

const ElDropdownItem = defineComponent({
  name: 'ElDropdownItem',
  props: { command: String, disabled: Boolean },
  setup(props, { slots }) {
    return () =>
      hVue(
        'div',
        {
          class: 'el-dropdown-item',
          'data-command': props.command,
          'data-disabled': String(!!props.disabled),
        },
        slots.default?.(),
      )
  },
})

function elStubs() {
  return { ElButton, ElDropdown, ElDropdownMenu, ElDropdownItem }
}

function mountView() {
  return shallowMount(PerfView, {
    props: { deviceId: 'dev1' },
    global: { stubs: elStubs() },
  })
}

/** 按可见文本找按钮（stub 按钮渲染为原生 <button>）。 */
function findButton(wrapper: ReturnType<typeof mountView>, text: string) {
  const btn = wrapper.findAll('button').find((b) => b.text().includes(text))
  if (!btn) throw new Error(`button not found: ${text}`)
  return btn
}

/** Blob 下载环境 stub（happy-dom 无 createObjectURL / anchor.click 实现）。 */
function stubBlobDownload() {
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
  return { createObjectURL, revokeObjectURL, clickSpy, getAnchor: () => clickedAnchor }
}

function latestWs() {
  return h.MockWebSocketService.instances.at(-1)!
}

beforeEach(() => {
  h.MockWebSocketService.instances = []
  mockApi.getPerfRecordStatus.mockClear()
  mockApi.getPerfRecordStatus.mockResolvedValue({
    recording: false, reason: '', rows: 0, oldest_ts: null, newest_ts: null,
  })
  mockApi.startPerfRecording.mockClear()
  mockApi.startPerfRecording.mockResolvedValue({
    recording: true, reason: '', rows: 0, oldest_ts: null, newest_ts: null,
  })
  mockApi.stopPerfRecording.mockClear()
  mockApi.stopPerfRecording.mockResolvedValue({
    recording: false, reason: 'stopped', rows: 12, oldest_ts: null, newest_ts: null,
  })
  mockApi.exportPerfMetrics.mockClear()
  mockApi.exportPerfMetrics.mockResolvedValue({
    data: new Blob(['x']),
    headers: {
      'x-export-count': '42',
      'x-export-oldest-ts': '1710000000',
      'x-export-newest-ts': '1710003600',
    },
  })
  elMessage.success.mockClear()
  elMessage.error.mockClear()
  elMessage.warning.mockClear()
  vi.spyOn(console, 'error').mockImplementation(() => {})
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('PerfView', () => {
  it('挂载时连接性能 WS 并渲染空态卡片与图表', async () => {
    const wrapper = mountView()
    // onMounted 里置 wsConnected=true，需等一次渲染刷新才能看到 LIVE
    await nextTick()

    expect(h.MockWebSocketService.instances).toHaveLength(1)
    const ws = latestWs()
    expect(ws.url).toContain('/ws/perf/dev1')
    expect(ws.connect).toHaveBeenCalled()
    expect(ws.setMessageHandler).toHaveBeenCalled()
    expect(ws.setErrorHandler).toHaveBeenCalled()
    expect(ws.setCloseHandler).toHaveBeenCalled()

    const cards = wrapper.findAll('.metric-card')
    expect(cards).toHaveLength(4)
    expect(cards[0].text()).toContain('CPU 使用率')
    expect(cards[0].text()).toContain('0.0%')
    expect(cards[1].text()).toContain('0 MB')
    expect(cards[2].text()).toContain('- FPS')
    expect(cards[3].text()).toContain('未知')

    expect(wrapper.findAll('.chart-container')).toHaveLength(2)
    // 两个 ECharts 图表（v-chart 被 shallowMount 自动 stub，不引入 canvas）
    expect(wrapper.findAllComponents({ name: 'echarts' })).toHaveLength(2)
    expect(wrapper.find('.status-indicator').text()).toBe('LIVE')
    expect(wrapper.find('.status-text').text()).toContain('数据点: 0 / 60')
  })

  it('WS 推送指标后更新卡片，超过 60 个数据点按 FIFO 裁剪', async () => {
    const wrapper = mountView()
    const ws = latestWs()

    ws.messageHandler!(JSON.stringify(metrics))
    await nextTick()

    const cards = wrapper.findAll('.metric-card')
    expect(cards[0].text()).toContain('45.2%')
    expect(cards[1].text()).toContain('2048 MB')
    expect(cards[1].text()).toContain('/ 4096 MB')
    expect(cards[2].text()).toContain('58 FPS')
    expect(cards[3].text()).toContain('com.example.app')
    expect(cards[3].text()).toContain('com.example/.MainActivity')
    expect(wrapper.find('.status-text').text()).toContain('数据点: 1 / 60')

    for (let i = 0; i < 70; i++) {
      ws.messageHandler!(JSON.stringify({ ...metrics, cpu_percent: i }))
    }
    await nextTick()
    expect(wrapper.find('.status-text').text()).toContain('数据点: 60 / 60')
    // 最新的数据点胜出（最早的已被 shift 掉）
    expect(cards[0].text()).toContain('69.0%')
  })

  it('卡顿数按「采样窗口」口径展示（gfxinfo 降频后非每秒口径）', async () => {
    const wrapper = mountView()
    const ws = latestWs()
    const fpsCard = () => wrapper.findAll('.metric-card')[2]

    ws.messageHandler!(JSON.stringify({ ...metrics, jank_count: 0 }))
    await nextTick()
    expect(fpsCard().text()).not.toContain('卡顿')

    ws.messageHandler!(JSON.stringify({ ...metrics, jank_count: 7 }))
    await nextTick()
    expect(fpsCard().text()).toContain('卡顿: 7 帧/采样窗口')
    expect(fpsCard().text()).not.toContain('帧/秒')

    wrapper.unmount()
  })

  it('非法 JSON 被忽略且不破坏已有数据；卸载时关闭 WebSocket', async () => {
    const wrapper = mountView()
    const ws = latestWs()

    ws.messageHandler!(JSON.stringify(metrics))
    ws.messageHandler!('not-json')
    await nextTick()

    expect(wrapper.find('.status-text').text()).toContain('数据点: 1 / 60')
    expect(console.error).toHaveBeenCalled()

    wrapper.unmount()
    expect(ws.close).toHaveBeenCalled()
  })

  it('挂载拉取录制状态；未录制时无 REC 徽标', async () => {
    const wrapper = mountView()
    await flushPromises()
    expect(mockApi.getPerfRecordStatus).toHaveBeenCalledWith('dev1')
    expect(wrapper.find('.rec-indicator').exists()).toBe(false)
    wrapper.unmount()
  })

  it('录制徽标：录制中显示 REC 与行数', async () => {
    mockApi.getPerfRecordStatus.mockResolvedValue({
      recording: true, reason: '', rows: 7, oldest_ts: null, newest_ts: null,
    })
    const wrapper = mountView()
    await flushPromises()

    const badge = wrapper.find('.rec-indicator')
    expect(badge.exists()).toBe(true)
    expect(badge.text()).toContain('REC')
    expect(badge.text()).toContain('7 行')
    expect(findButton(wrapper, '停止录制').exists()).toBe(true)
    wrapper.unmount()
  })

  it('点击开始录制：调用 startPerfRecording 并刷新状态', async () => {
    const wrapper = mountView()
    await flushPromises()

    await findButton(wrapper, '开始录制').trigger('click')
    await flushPromises()

    expect(mockApi.startPerfRecording).toHaveBeenCalledWith('dev1')
    expect(elMessage.success).toHaveBeenCalledWith('录制已开启')
    expect(mockApi.getPerfRecordStatus).toHaveBeenCalled()
    wrapper.unmount()
  })

  it('点击停止录制：调用 stopPerfRecording 并提示落盘行数', async () => {
    mockApi.getPerfRecordStatus.mockResolvedValue({
      recording: true, reason: '', rows: 7, oldest_ts: null, newest_ts: null,
    })
    const wrapper = mountView()
    await flushPromises()

    await findButton(wrapper, '停止录制').trigger('click')
    await flushPromises()

    expect(mockApi.stopPerfRecording).toHaveBeenCalledWith('dev1')
    expect(elMessage.success).toHaveBeenCalledWith('录制已停止，本次落盘 12 行')
    wrapper.unmount()
  })

  it('录制操作失败：弹错误提示', async () => {
    mockApi.startPerfRecording.mockRejectedValueOnce({ response: { data: { detail: 'RECORDING_DISABLED' } } })
    const wrapper = mountView()
    await flushPromises()

    await findButton(wrapper, '开始录制').trigger('click')
    await flushPromises()

    expect(elMessage.error).toHaveBeenCalledWith('录制操作失败: RECORDING_DISABLED')
    wrapper.unmount()
  })

  it('导出：下拉选择 CSV/JSON 触发 blob 下载并回显行数', async () => {
    const { createObjectURL, revokeObjectURL, getAnchor } = stubBlobDownload()
    const wrapper = mountView()
    await flushPromises()
    const dropdown = wrapper.findComponent(ElDropdown)

    dropdown.vm.$emit('command', 'buffer-json')
    await flushPromises()

    expect(mockApi.exportPerfMetrics).toHaveBeenCalledWith('dev1', 'json', 'buffer')
    expect(createObjectURL).toHaveBeenCalled()
    expect(getAnchor()!.download).toBe('perf_dev1_buffer.json')
    expect(getAnchor()!.href).toContain('blob:mock-url')
    expect(revokeObjectURL).toHaveBeenCalled()
    expect(elMessage.success.mock.calls[0][0]).toContain('42 条')
    expect(String(elMessage.success.mock.calls[0][0])).toContain('导出完成')

    dropdown.vm.$emit('command', 'cache-csv')
    await flushPromises()
    expect(mockApi.exportPerfMetrics).toHaveBeenCalledWith('dev1', 'csv', 'cache')
    expect(getAnchor()!.download).toBe('perf_dev1_cache.csv')
    wrapper.unmount()
  })

  it('导出菜单：无缓存数据时缓存项置灰并提示需开启录制', async () => {
    // beforeEach 默认：recording=false, rows=0（从未录制）→ 缓存两项不可用
    const wrapper = mountView()
    await flushPromises()
    const byCmd = (c: string) =>
      wrapper.findAll('[data-command]').find((i) => i.attributes('data-command') === c)!

    expect(byCmd('buffer-csv').attributes('data-disabled')).toBe('false')
    expect(byCmd('buffer-json').attributes('data-disabled')).toBe('false')
    expect(byCmd('cache-csv').attributes('data-disabled')).toBe('true')
    expect(byCmd('cache-json').attributes('data-disabled')).toBe('true')
    expect(byCmd('cache-csv').text()).toContain('需先开启录制')
    wrapper.unmount()
  })

  it('导出菜单：已有落盘缓存（未在录制）时缓存项可用', async () => {
    mockApi.getPerfRecordStatus.mockResolvedValue({
      recording: false, reason: 'stopped', rows: 200, oldest_ts: null, newest_ts: null,
    })
    const wrapper = mountView()
    await flushPromises()
    const byCmd = (c: string) =>
      wrapper.findAll('[data-command]').find((i) => i.attributes('data-command') === c)!

    expect(byCmd('cache-csv').attributes('data-disabled')).toBe('false')
    expect(byCmd('cache-json').attributes('data-disabled')).toBe('false')
    expect(byCmd('cache-csv').text()).not.toContain('需先开启录制')
    wrapper.unmount()
  })

  it('导出菜单：录制中（尚未落盘任何批）缓存项可用', async () => {
    mockApi.getPerfRecordStatus.mockResolvedValue({
      recording: true, reason: '', rows: 0, oldest_ts: null, newest_ts: null,
    })
    const wrapper = mountView()
    await flushPromises()
    const byCmd = (c: string) =>
      wrapper.findAll('[data-command]').find((i) => i.attributes('data-command') === c)!

    expect(byCmd('cache-csv').attributes('data-disabled')).toBe('false')
    wrapper.unmount()
  })

  it('导出空数据（404）与失败分别提示', async () => {
    mockApi.exportPerfMetrics.mockRejectedValueOnce({ response: { status: 404 } })
    mockApi.exportPerfMetrics.mockRejectedValueOnce({ response: { status: 500, data: { detail: 'boom' } } })
    const wrapper = mountView()
    await flushPromises()
    const dropdown = wrapper.findComponent(ElDropdown)

    dropdown.vm.$emit('command', 'buffer-csv')
    await flushPromises()
    expect(elMessage.warning).toHaveBeenCalledWith('暂无数据可导出')

    dropdown.vm.$emit('command', 'buffer-csv')
    await flushPromises()
    expect(elMessage.error).toHaveBeenCalledWith('导出失败: boom')
    wrapper.unmount()
  })
})