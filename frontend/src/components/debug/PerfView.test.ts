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
import { shallowMount } from '@vue/test-utils'
import { nextTick } from 'vue'

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

function mountView() {
  return shallowMount(PerfView, { props: { deviceId: 'dev1' } })
}

function latestWs() {
  return h.MockWebSocketService.instances.at(-1)!
}

beforeEach(() => {
  h.MockWebSocketService.instances = []
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
})