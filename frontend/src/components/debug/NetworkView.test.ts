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
import { nextTick } from 'vue'

const mockApi = vi.hoisted(() => ({
  getNetworkStats: vi.fn(),
  getNetworkConnections: vi.fn(),
}))
vi.mock('@/services/api', () => ({ api: mockApi }))

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

function mountView() {
  return shallowMount(NetworkView, {
    props: { deviceId: 'dev1' },
    global: {
      stubs: {
        'el-tag': ElTagStub,
        'el-select': ElSelectStub,
        'el-option': true,
      },
    },
  })
}

beforeEach(() => {
  mockApi.getNetworkStats.mockReset()
  mockApi.getNetworkConnections.mockReset()
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
})