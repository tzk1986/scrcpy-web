/**
 * NetworkView 组件测试
 * ======================
 *
 * 覆盖：
 *   - 挂载后拉取网络统计与连接列表：WiFi 状态、速率/流量格式化、连接列表渲染
 *   - 2 秒轮询：定时器触发第二次拉取，卸载后停止
 *   - 连接筛选（TCP/UDP）交互
 *   - 接口失败时降级为空态（0 KB/s、无连接）不抛错
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, shallowMount } from '@vue/test-utils'
import { defineComponent, h, nextTick } from 'vue'

const mockApi = vi.hoisted(() => ({
  getNetworkStats: vi.fn(),
  getNetworkConnections: vi.fn(),
  getNetworkRecordStatus: vi.fn().mockResolvedValue({
    recording: false, reason: '', rows: 0, oldest_ts: null, newest_ts: null,
  }),
  startNetworkRecording: vi.fn().mockResolvedValue({
    recording: true, reason: '', rows: 0, oldest_ts: null, newest_ts: null,
  }),
  stopNetworkRecording: vi.fn().mockResolvedValue({
    recording: false, reason: 'stopped', rows: 9, oldest_ts: null, newest_ts: null,
  }),
  exportNetworkStats: vi.fn().mockResolvedValue({
    data: new Blob(['x']),
    headers: { 'x-export-count': '30', 'x-export-oldest-ts': '1710000000', 'x-export-newest-ts': '1710007200' },
  }),
}))
vi.mock('@/services/api', () => ({ api: mockApi }))

const elMessage = vi.hoisted(() => ({
  success: vi.fn(), error: vi.fn(), warning: vi.fn(),
}))
vi.mock('element-plus', () => ({ ElMessage: elMessage }))

import NetworkView from './NetworkView.vue'

const stats = {
  ts: 1710000000,
  rx_bytes: 1048576,
  tx_bytes: 2048,
  rx_rate_kbps: 2048,
  tx_rate_kbps: 512,
  active_connections: 3,
  wifi_connected: true,
  wifi_ssid: 'HomeWiFi',
}

const connections = [
  {
    protocol: 'tcp',
    local_addr: '10.0.0.1',
    local_port: 1234,
    remote_addr: '1.1.1.1',
    remote_port: 443,
    state: 'ESTABLISHED',
    uid: null,
  },
  {
    protocol: 'udp',
    local_addr: '10.0.0.1',
    local_port: 5353,
    remote_addr: '224.0.0.251',
    remote_port: 5353,
    state: 'UNCONN',
    uid: null,
  },
]

const ElTagStub = {
  name: 'ElTag',
  template: '<span class="stub-tag"><slot /></span>',
}

const ElSelectStub = {
  name: 'ElSelect',
  props: ['modelValue'],
  emits: ['update:modelValue'],
  template: '<div class="stub-select"><slot /></div>',
}

const ElButton = defineComponent({
  name: 'ElButton',
  props: { loading: Boolean },
  inheritAttrs: false,
  setup(props, { slots, attrs }) {
    return () =>
      h('button', { ...attrs, 'data-loading': String(!!props.loading) }, slots.default?.())
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
  props: { command: String, disabled: Boolean },
  setup(props, { slots }) {
    return () =>
      h(
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

function mountView() {
  return shallowMount(NetworkView, {
    props: { deviceId: 'dev1' },
    global: {
      stubs: {
        'el-tag': ElTagStub,
        'el-select': ElSelectStub,
        'el-option': true,
        ElButton,
        ElDropdown,
        ElDropdownMenu,
        ElDropdownItem,
      },
    },
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

beforeEach(() => {
  mockApi.getNetworkStats.mockReset()
  mockApi.getNetworkConnections.mockReset()
  mockApi.getNetworkRecordStatus.mockClear()
  mockApi.getNetworkRecordStatus.mockResolvedValue({
    recording: false, reason: '', rows: 0, oldest_ts: null, newest_ts: null,
  })
  mockApi.startNetworkRecording.mockClear()
  mockApi.startNetworkRecording.mockResolvedValue({
    recording: true, reason: '', rows: 0, oldest_ts: null, newest_ts: null,
  })
  mockApi.stopNetworkRecording.mockClear()
  mockApi.stopNetworkRecording.mockResolvedValue({
    recording: false, reason: 'stopped', rows: 9, oldest_ts: null, newest_ts: null,
  })
  mockApi.exportNetworkStats.mockClear()
  mockApi.exportNetworkStats.mockResolvedValue({
    data: new Blob(['x']),
    headers: {
      'x-export-count': '30',
      'x-export-oldest-ts': '1710000000',
      'x-export-newest-ts': '1710007200',
    },
  })
  elMessage.success.mockClear()
  elMessage.error.mockClear()
  elMessage.warning.mockClear()
  vi.spyOn(console, 'error').mockImplementation(() => {})
})

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe('NetworkView', () => {
  it('挂载后拉取数据并渲染 WiFi 状态、指标卡片与连接列表', async () => {
    vi.useFakeTimers()
    mockApi.getNetworkStats.mockResolvedValue(stats)
    mockApi.getNetworkConnections.mockResolvedValue({ connections })
    const wrapper = mountView()
    await flushPromises()

    expect(mockApi.getNetworkStats).toHaveBeenCalledWith('dev1')
    expect(mockApi.getNetworkConnections).toHaveBeenCalledWith('dev1')

    expect(wrapper.find('.wifi-bar').classes()).toContain('connected')
    expect(wrapper.find('.wifi-ssid').text()).toBe('HomeWiFi')
    expect(wrapper.find('.wifi-bar').text()).toContain('已连接')

    const cards = wrapper.findAll('.metric-card')
    expect(cards[0].text()).toContain('2.0 MB/s')
    expect(cards[0].text()).toContain('1.0 MB')
    expect(cards[1].text()).toContain('512.0 KB/s')
    expect(cards[2].text()).toContain('3')
    expect(cards[2].text()).toContain('总连接: 2')

    expect(wrapper.find('.section-header').text()).toContain('活跃连接 (2)')
    const items = wrapper.findAll('.connection-item')
    expect(items).toHaveLength(2)
    expect(items[0].text()).toContain('TCP')
    expect(items[0].text()).toContain('10.0.0.1:1234')
    expect(items[0].text()).toContain('1.1.1.1:443')
    expect(items[0].text()).toContain('ESTABLISHED')

    // 流量曲线图（v-chart ← vue-echarts 的 ECharts 组件，被 shallowMount 自动 stub）
    expect(wrapper.findComponent({ name: 'echarts' }).exists()).toBe(true)
    expect(wrapper.find('.chart-container .chart').exists()).toBe(true)

    // 2 秒轮询第二次拉取；卸载后停止
    await vi.advanceTimersByTimeAsync(2000)
    expect(mockApi.getNetworkStats).toHaveBeenCalledTimes(2)

    wrapper.unmount()
    await vi.advanceTimersByTimeAsync(4000)
    expect(mockApi.getNetworkStats).toHaveBeenCalledTimes(2)
  })

  it('切换连接筛选只显示对应协议', async () => {
    mockApi.getNetworkStats.mockResolvedValue(stats)
    mockApi.getNetworkConnections.mockResolvedValue({ connections })
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.findAll('.connection-item')).toHaveLength(2)

    const select = wrapper.findComponent({ name: 'ElSelect' })
    select.vm.$emit('update:modelValue', 'tcp')
    await nextTick()

    let items = wrapper.findAll('.connection-item')
    expect(items).toHaveLength(1)
    expect(items[0].text()).toContain('TCP')

    select.vm.$emit('update:modelValue', 'udp')
    await nextTick()

    items = wrapper.findAll('.connection-item')
    expect(items).toHaveLength(1)
    expect(items[0].text()).toContain('UDP')
    expect(wrapper.find('.empty').exists()).toBe(false)

    wrapper.unmount()
  })

  it('接口失败时降级为空态且不抛错', async () => {
    mockApi.getNetworkStats.mockRejectedValue(new Error('boom'))
    mockApi.getNetworkConnections.mockRejectedValue(new Error('boom'))
    const wrapper = mountView()
    await flushPromises()

    expect(console.error).toHaveBeenCalled()
    expect(wrapper.find('.wifi-ssid').text()).toBe('未连接')
    const cards = wrapper.findAll('.metric-card')
    expect(cards[0].text()).toContain('0 KB/s')
    expect(wrapper.find('.empty').text()).toBe('无连接')

    wrapper.unmount()
  })

  it('轮询拉取录制状态；未录制时无 REC 徽标', async () => {
    mockApi.getNetworkStats.mockResolvedValue(stats)
    mockApi.getNetworkConnections.mockResolvedValue({ connections })
    const wrapper = mountView()
    await flushPromises()

    expect(mockApi.getNetworkRecordStatus).toHaveBeenCalledWith('dev1')
    expect(wrapper.find('.rec-indicator').exists()).toBe(false)
    wrapper.unmount()
  })

  it('录制中显示 REC 徽标与行数', async () => {
    mockApi.getNetworkStats.mockResolvedValue(stats)
    mockApi.getNetworkConnections.mockResolvedValue({ connections })
    mockApi.getNetworkRecordStatus.mockResolvedValue({
      recording: true, reason: '', rows: 5, oldest_ts: null, newest_ts: null,
    })
    const wrapper = mountView()
    await flushPromises()

    const badge = wrapper.find('.rec-indicator')
    expect(badge.exists()).toBe(true)
    expect(badge.text()).toContain('REC')
    expect(badge.text()).toContain('5 行')
    expect(findButton(wrapper, '停止录制').exists()).toBe(true)
    wrapper.unmount()
  })

  it('点击开始/停止录制并刷新状态', async () => {
    mockApi.getNetworkStats.mockResolvedValue(stats)
    mockApi.getNetworkConnections.mockResolvedValue({ connections })
    const wrapper = mountView()
    await flushPromises()

    // 第二次 getNetworkRecordStatus（点击开始后的刷新）返回录制中
    mockApi.getNetworkRecordStatus.mockResolvedValueOnce({
      recording: true, reason: '', rows: 5, oldest_ts: null, newest_ts: null,
    })
    await findButton(wrapper, '开始录制').trigger('click')
    await flushPromises()
    expect(mockApi.startNetworkRecording).toHaveBeenCalledWith('dev1')
    expect(elMessage.success).toHaveBeenCalledWith('录制已开启')

    await findButton(wrapper, '停止录制').trigger('click')
    await flushPromises()
    expect(mockApi.stopNetworkRecording).toHaveBeenCalledWith('dev1')
    expect(elMessage.success).toHaveBeenCalledWith('录制已停止，本次落盘 9 行')
    wrapper.unmount()
  })

  it('录制操作失败：弹错误提示', async () => {
    mockApi.getNetworkStats.mockResolvedValue(stats)
    mockApi.getNetworkConnections.mockResolvedValue({ connections })
    mockApi.startNetworkRecording.mockRejectedValueOnce({
      response: { data: { detail: 'RECORDING_DISABLED' } },
    })
    const wrapper = mountView()
    await flushPromises()

    await findButton(wrapper, '开始录制').trigger('click')
    await flushPromises()

    expect(elMessage.error).toHaveBeenCalledWith('录制操作失败: RECORDING_DISABLED')
    wrapper.unmount()
  })

  it('导出：下拉选择触发 blob 下载并回显行数', async () => {
    mockApi.getNetworkStats.mockResolvedValue(stats)
    mockApi.getNetworkConnections.mockResolvedValue({ connections })
    const { createObjectURL, revokeObjectURL, getAnchor } = stubBlobDownload()
    const wrapper = mountView()
    await flushPromises()
    const dropdown = wrapper.findComponent(ElDropdown)

    dropdown.vm.$emit('command', 'buffer-csv')
    await flushPromises()

    expect(mockApi.exportNetworkStats).toHaveBeenCalledWith('dev1', 'csv', 'buffer')
    expect(createObjectURL).toHaveBeenCalled()
    expect(getAnchor()!.download).toBe('network_dev1_buffer.csv')
    expect(getAnchor()!.href).toContain('blob:mock-url')
    expect(revokeObjectURL).toHaveBeenCalled()
    expect(String(elMessage.success.mock.calls[0][0])).toContain('30 条')

    dropdown.vm.$emit('command', 'cache-json')
    await flushPromises()
    expect(mockApi.exportNetworkStats).toHaveBeenCalledWith('dev1', 'json', 'cache')
    expect(getAnchor()!.download).toBe('network_dev1_cache.json')
    wrapper.unmount()
  })

  it('导出菜单：无缓存数据时缓存项置灰并提示需开启录制', async () => {
    // beforeEach 默认：recording=false, rows=0（从未录制）→ 缓存两项不可用
    mockApi.getNetworkStats.mockResolvedValue(stats)
    mockApi.getNetworkConnections.mockResolvedValue({ connections })
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
    mockApi.getNetworkStats.mockResolvedValue(stats)
    mockApi.getNetworkConnections.mockResolvedValue({ connections })
    mockApi.getNetworkRecordStatus.mockResolvedValue({
      recording: false, reason: 'stopped', rows: 120, oldest_ts: null, newest_ts: null,
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
    mockApi.getNetworkStats.mockResolvedValue(stats)
    mockApi.getNetworkConnections.mockResolvedValue({ connections })
    mockApi.getNetworkRecordStatus.mockResolvedValue({
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
    mockApi.getNetworkStats.mockResolvedValue(stats)
    mockApi.getNetworkConnections.mockResolvedValue({ connections })
    mockApi.exportNetworkStats.mockRejectedValueOnce({ response: { status: 404 } })
    mockApi.exportNetworkStats.mockRejectedValueOnce({
      response: { status: 500, data: { detail: 'boom' } },
    })
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