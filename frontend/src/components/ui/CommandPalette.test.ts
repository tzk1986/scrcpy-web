/**
 * CommandPalette 组件测试（Ctrl+K 命令面板）
 * ==========================================
 *
 * 覆盖：
 *   - Ctrl+K 打开面板：渲染命令分组（导航/设备/调试/视图）、输入框聚焦、提示行
 *   - 输入过滤：命中单条 / 无匹配空态 / Esc 关闭
 *   - Enter 执行选中命令（路由跳转）并关闭面板
 *   - 点击「切换到 logcat 面板」派发 openscrcpy:set-debug-tab CustomEvent
 *   - 方案58 E-24/E-12：面板断开走确认+await+成败播报
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'

const mockApi = vi.hoisted(() => ({
  listDevices: vi.fn().mockResolvedValue([]),
  disconnectDevice: vi.fn().mockResolvedValue({ success: true }),
  screenshot: vi.fn().mockResolvedValue(new Blob(['png'])),
}))

const urlStub = vi.hoisted(() => ({
  createObjectURL: vi.fn(),
  revokeObjectURL: vi.fn(),
}))
vi.mock('@/services/api', () => ({ api: mockApi }))

const h = vi.hoisted(() => ({
  ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
  ElMessageBox: { confirm: vi.fn() },
}))
vi.mock('element-plus', () => ({ ElMessage: h.ElMessage, ElMessageBox: h.ElMessageBox }))

import CommandPalette from './CommandPalette.vue'
import { useDeviceStore } from '@/stores/device'

const Empty = { template: '<div />' }

let router: Router
let pinia: Pinia
let wrapper: VueWrapper | undefined

async function mountPalette(initialPath = '/') {
  router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'dashboard', component: Empty },
      { path: '/device/:id', name: 'device-detail', component: Empty },
    ],
  })
  await router.push(initialPath)
  await router.isReady()

  pinia = createPinia()
  setActivePinia(pinia)
  wrapper = mount(CommandPalette, {
    attachTo: document.body,
    global: {
      plugins: [router, pinia],
      // Teleport 到 body，stub 为原地渲染便于断言
      stubs: { teleport: true },
    },
  })
  return wrapper
}

function pressCtrlK() {
  window.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', ctrlKey: true }))
}

function listItemTexts(w: VueWrapper): string[] {
  return w.findAll('.cmd-item').map((i) => i.text())
}

beforeEach(() => {
  // afterEach 的 vi.restoreAllMocks 会把 hoisted 时绑定的 mockResolvedValue
  // 实现一并冲掉（store 取 result.success 抛错→面板报「未知错误」），
  // 故这里 mockReset 后必须重设实现（同 Dashboard.test.ts 口径）。
  mockApi.listDevices.mockReset().mockResolvedValue([])
  mockApi.disconnectDevice.mockReset().mockResolvedValue({ success: true })
  mockApi.screenshot.mockReset().mockResolvedValue(new Blob(['png']))
  urlStub.createObjectURL.mockReset().mockImplementation(() => 'blob:cp-thumb')
  urlStub.revokeObjectURL.mockReset()
  Object.defineProperty(URL, 'createObjectURL', {
    configurable: true,
    value: urlStub.createObjectURL,
  })
  Object.defineProperty(URL, 'revokeObjectURL', {
    configurable: true,
    value: urlStub.revokeObjectURL,
  })
  h.ElMessageBox.confirm.mockReset().mockResolvedValue('confirm')
  h.ElMessage.success.mockClear()
  h.ElMessage.error.mockClear()
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = undefined
  vi.restoreAllMocks()
})

describe('CommandPalette', () => {
  it('Ctrl+K 打开面板并渲染命令分组与设备命令', async () => {
    const w = await mountPalette()
    expect(w.find('.cmd-overlay').exists()).toBe(false)

    const store = useDeviceStore()
    store.devices = [
      { id: 'dev-a', model: 'Pixel 8', status: 'online', os_version: '14', resolution: [1080, 2400], battery: 90 },
    ]

    pressCtrlK()
    await flushPromises()

    expect(w.find('.cmd-overlay').exists()).toBe(true)
    expect(w.find('.cmd-input').attributes('placeholder')).toBe('输入命令…')
    expect(w.findAll('.cmd-section').map((s) => s.text())).toEqual(['导航', '设备', '调试', '视图'])

    const items = listItemTexts(w)
    expect(items).toContain('打开 Dashboard（设备列表）')
    expect(items).toContain('打开设备 Pixel 8')
    expect(items).toContain('切换到 perf 面板')
    expect(items).toContain('切换全屏')
    expect(w.find('.cmd-hint').text()).toContain('Enter 执行')

    // 打开后输入框自动聚焦
    expect(document.activeElement).toBe(w.find('.cmd-input').element)

    // 再次 Ctrl+K 关闭
    pressCtrlK()
    await flushPromises()
    expect(w.find('.cmd-overlay').exists()).toBe(false)
  })

  it('输入过滤：命中单条、无匹配空态、Esc 关闭', async () => {
    const w = await mountPalette()
    pressCtrlK()
    await flushPromises()

    const input = w.find('.cmd-input')
    await input.setValue('全屏')
    expect(listItemTexts(w)).toEqual(['切换全屏'])

    await input.setValue('zzz-不存在')
    expect(w.findAll('.cmd-item')).toHaveLength(0)
    expect(w.find('.cmd-empty').text()).toBe('无匹配命令')

    await input.trigger('keydown', { key: 'Escape' })
    expect(w.find('.cmd-overlay').exists()).toBe(false)
  })

  it('Enter 执行选中命令并关闭面板（路由跳转）', async () => {
    const w = await mountPalette('/device/dev-a')
    const pushSpy = vi.spyOn(router, 'push')

    pressCtrlK()
    await flushPromises()
    // 处于设备页：首条命令为「打开 Dashboard（设备列表）」
    expect(w.findAll('.cmd-item')[0].text()).toBe('打开 Dashboard（设备列表）')
    expect(w.findAll('.cmd-item')[0].classes()).toContain('active')

    await w.find('.cmd-input').trigger('keydown', { key: 'Enter' })
    await flushPromises()

    expect(pushSpy).toHaveBeenCalledWith('/')
    expect(w.find('.cmd-overlay').exists()).toBe(false)
  })

  it('点击调试 Tab 命令派发 openscrcpy:set-debug-tab CustomEvent', async () => {
    const w = await mountPalette()
    const received: string[] = []
    const onTab = (e: Event) => received.push((e as CustomEvent).detail)
    window.addEventListener('openscrcpy:set-debug-tab', onTab)

    pressCtrlK()
    await flushPromises()

    const target = w.findAll('.cmd-item').find((i) => i.text() === '切换到 logcat 面板')
    expect(target).toBeDefined()
    await target!.trigger('click')

    expect(received).toEqual(['logcat'])
    expect(w.find('.cmd-overlay').exists()).toBe(false)

    window.removeEventListener('openscrcpy:set-debug-tab', onTab)
  })

  // 方案58 E-24 裁决 a + E-12：面板断开统一确认、await 完成并播报成败
  async function runDisconnect() {
    const w = await mountPalette('/device/dev-a')
    pressCtrlK()
    await flushPromises()
    await w.find('.cmd-input').setValue('断开当前设备')
    await w.find('.cmd-item').trigger('click')
    await flushPromises()
    return w
  }

  it('E-24：面板断开先确认，确认后才调用断开并成功播报', async () => {
    const w = await runDisconnect()
    expect(h.ElMessageBox.confirm).toHaveBeenCalledTimes(1)
    expect(mockApi.disconnectDevice).toHaveBeenCalledWith('dev-a')
    expect(h.ElMessage.success).toHaveBeenCalledWith('已断开 dev-a')
    expect(w.find('.cmd-overlay').exists()).toBe(false)
  })

  it('E-24：确认取消 → 不调用断开、无播报', async () => {
    h.ElMessageBox.confirm.mockRejectedValueOnce('cancel')
    await runDisconnect()
    expect(mockApi.disconnectDevice).not.toHaveBeenCalled()
    expect(h.ElMessage.success).not.toHaveBeenCalled()
    expect(h.ElMessage.error).not.toHaveBeenCalled()
  })

  it('E-12：断开失败不再静默，error 播报后端 detail', async () => {
    mockApi.disconnectDevice.mockRejectedValueOnce({
      response: { data: { detail: 'device busy' } },
    })
    await runDisconnect()
    expect(h.ElMessage.error).toHaveBeenCalledWith('断开失败: device busy')
    expect(h.ElMessage.success).not.toHaveBeenCalled()
  })
})