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

/** 方案 37 D5 新 4 键（后端 D3 已透传；断言用 payload 以此为准）。 */
const d5Fields = {
  // user+kernel 降序：procI(21) procL(21) procB(17) procF(14) procC(13)
  // procG(10.5) procD(10) procH(8) | 裁掉 procE/procK(7) procA(2) procJ(0.2)
  cpu_processes: [
    { proc: 'procA', user: 1, kernel: 1 },
    { proc: 'procB', user: 9, kernel: 8 },
    { proc: 'procC', user: 7, kernel: 6 },
    { proc: 'procD', user: 5, kernel: 5 },
    { proc: 'procE', user: 3, kernel: 4 },
    { proc: 'procF', user: 11, kernel: 3 },
    { proc: 'procG', user: 10, kernel: 0.5 },
    { proc: 'procH', user: 2, kernel: 6 },
    { proc: 'procI', user: 20, kernel: 1 },
    { proc: 'procJ', user: 0.1, kernel: 0.1 },
    { proc: 'procK', user: 4, kernel: 3 },
    { proc: 'procL', user: 12, kernel: 9 },
  ],
  battery_temp: 42.4,
  battery_voltage: 4200,
  battery_health: 'GOOD',
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
  return {
    ElButton,
    ElDropdown,
    ElDropdownMenu,
    ElDropdownItem,
    ElTable: ElTableStub,
    // 消解「Failed to resolve component: el-table-column」警告（测试环境未全局注册 element-plus）
    'el-table-column': true,
  }
}

/** 进程表格渲染 stub（AppsView.test.ts ElTableStub 先例）：按 data 渲染行 + slot 透传。 */
const ElTableStub = defineComponent({
  name: 'ElTable',
  props: { data: Array },
  setup(props, { slots }) {
    return () =>
      hVue('div', { class: 'stub-table' }, [
        ((props.data ?? []) as Array<Record<string, unknown>>).map((row, i) =>
          hVue('div', { key: i, class: 'stub-table-row' }, [
            hVue('span', { class: 'stub-proc' }, String(row.proc)),
            hVue('span', { class: 'stub-user' }, String(row.user)),
            hVue('span', { class: 'stub-kernel' }, String(row.kernel)),
          ]),
        ),
        slots.default?.(),
      ])
  },
})

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

  it('alerts 消息渲染徽标条并驱动卡片警示色；同 episode 只弹一次 toast', async () => {
    const wrapper = mountView()
    const ws = latestWs()

    const alerts = [
      { id: 'cpu_percent', value: 87.2, threshold: 80, direction: 'above', since: 1, notify: true },
      { id: 'memory_percent', value: 93.5, threshold: 90, direction: 'above', since: 2, notify: false },
      { id: 'fps', value: 25.4, threshold: 30, direction: 'below', since: 3, notify: true },
    ]
    ws.messageHandler!(JSON.stringify({ ...metrics, alerts }))
    await nextTick()

    // 徽标条：3 枚 chip
    const chips = wrapper.findAll('.alert-chip')
    expect(chips).toHaveLength(3)
    expect(chips[0].text()).toContain('CPU 使用率')
    expect(chips[0].text()).toContain('87.2')

    // 卡片警示色由 alerts 驱动（cpu/内存/fps 三卡均 warning）
    const values = wrapper.findAll('.metric-value')
    expect(values[0].classes()).toContain('warning')
    expect(values[1].classes()).toContain('warning')
    expect(values[2].classes()).toContain('warning')

    // notify=true 两枚各弹一次；notify=false 不弹
    expect(elMessage.warning).toHaveBeenCalledTimes(2)

    // 同 episode 第二帧：不再弹、徽标仍在
    ws.messageHandler!(JSON.stringify({ ...metrics, alerts }))
    await nextTick()
    expect(elMessage.warning).toHaveBeenCalledTimes(2)
    expect(wrapper.findAll('.alert-chip')).toHaveLength(3)

    // 告警解除（空数组）：徽标消失、卡片警示色消失
    ws.messageHandler!(JSON.stringify({ ...metrics, alerts: [] }))
    await nextTick()
    expect(wrapper.findAll('.alert-chip')).toHaveLength(0)
    expect(wrapper.findAll('.metric-value')[0].classes()).not.toContain('warning')
  })

  it('无 alerts 键的消息不渲染徽标、不弹 toast（既有推送防御式兼容）', async () => {
    const wrapper = mountView()
    const ws = latestWs()

    ws.messageHandler!(JSON.stringify(metrics))
    await nextTick()

    expect(wrapper.findAll('.alert-chip')).toHaveLength(0)
    expect(elMessage.warning).not.toHaveBeenCalled()
  })

  // ---------------- 方案 37 D5：进程 CPU Top N 表格 ----------------

  it('渲染进程 CPU Top-N 表格（user+kernel 降序取前 8）', async () => {
    const wrapper = mountView()
    const ws = latestWs()

    ws.messageHandler!(JSON.stringify({ ...metrics, ...d5Fields }))
    await nextTick()

    const table = wrapper.find('.stub-table')
    expect(table.exists()).toBe(true)
    const rows = wrapper.findAll('.stub-table-row')
    expect(rows).toHaveLength(8)
    // 降序：前两名并列 21 保持原相对序
    expect(rows[0].text()).toContain('procI')
    expect(rows[1].text()).toContain('procL')
    expect(rows[2].text()).toContain('procB')
    expect(rows[7].text()).toContain('procH')
    // user/kernel 数值透出
    expect(rows[0].text()).toContain('20')
    expect(rows[0].text()).toContain('1')
    expect(rows[5].text()).toContain('0.5')
    // 第 9-12 名被裁掉
    const text = table.text()
    expect(text).not.toContain('procE')
    expect(text).not.toContain('procK')
    expect(text).not.toContain('procA')
    expect(text).not.toContain('procJ')
    wrapper.unmount()
  })

  // ---------------- 方案 37 D5：电池温度/电压/health 展示条 ----------------

  it('展示电池温度/电压/health（摄氏原值 toFixed(1) + mV + 归一字符串）', async () => {
    const wrapper = mountView()
    const ws = latestWs()

    ws.messageHandler!(JSON.stringify({ ...metrics, ...d5Fields }))
    await nextTick()

    const bar = wrapper.find('.battery-bar')
    expect(bar.exists()).toBe(true)
    expect(bar.text()).toContain('42.4 °C')
    expect(bar.text()).toContain('4200 mV')
    expect(bar.text()).toContain('GOOD')

    // 板级怪值如实展示（后端已 ÷10，前端不再 /10）；电压 None 单项隐藏
    ws.messageHandler!(
      JSON.stringify({ ...metrics, battery_temp: 2.6, battery_voltage: null, battery_health: 'UNKNOWN' }),
    )
    await nextTick()
    expect(bar.text()).toContain('2.6 °C')
    expect(bar.text()).not.toContain('mV')
    expect(bar.text()).toContain('UNKNOWN')
    wrapper.unmount()
  })

  // ---------------- 方案 37 D5：缺字段优雅降级 ----------------

  it('新字段缺失/空数组/None 时隐藏新分段（旧后端/旧数据降级）', async () => {
    const wrapper = mountView()
    const ws = latestWs()

    // 旧 8 键 payload：两个新分段都不出现
    ws.messageHandler!(JSON.stringify(metrics))
    await nextTick()
    expect(wrapper.find('.stub-table').exists()).toBe(false)
    expect(wrapper.find('.battery-bar').exists()).toBe(false)

    // 空数组 + 温度/电压 None + health 兜底串：表格不出现，电池条仅剩 UNKNOWN
    ws.messageHandler!(
      JSON.stringify({ ...metrics, cpu_processes: [], battery_temp: null, battery_voltage: null, battery_health: 'UNKNOWN' }),
    )
    await nextTick()
    expect(wrapper.find('.stub-table').exists()).toBe(false)
    const bar = wrapper.find('.battery-bar')
    expect(bar.exists()).toBe(true)
    expect(bar.text()).toContain('UNKNOWN')
    expect(bar.text()).not.toContain('°C')
    expect(bar.text()).not.toContain('mV')

    // 全部缺失（undefined 键）→ 电池条整体隐藏
    ws.messageHandler!(JSON.stringify(metrics))
    await nextTick()
    expect(wrapper.find('.battery-bar').exists()).toBe(false)
    wrapper.unmount()
  })
})
