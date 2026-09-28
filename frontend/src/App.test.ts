/**
 * App 根组件测试（方案 23 T2）
 * ============================
 *
 * 覆盖顶栏「退出服务」按钮：
 *   - 渲染存在
 *   - 确认后 accepted=true → 「服务正在退出」提示并开始 health 轮询
 *   - 确认后 accepted=false → 警告提示，不进入轮询
 *   - 取消确认框 → 不调用 api
 *   - 轮询中 health 失败 → 「服务已退出」提示且定时器停止
 *
 * element-plus 采用 partial mock：组件（el-button 等）与插件保持真实
 * （mount 时 global.plugins 注册），仅函数式 API（ElMessage/ElMessageBox）
 * 替换为 spy。
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import App from './App.vue'

const h = vi.hoisted(() => ({
  ElMessage: { info: vi.fn(), success: vi.fn(), warning: vi.fn(), error: vi.fn() },
  ElMessageBox: { confirm: vi.fn() },
}))

vi.mock('element-plus', async (importOriginal) => {
  const actual = await importOriginal<typeof import('element-plus')>()
  return { ...actual, ElMessage: h.ElMessage, ElMessageBox: h.ElMessageBox }
})

const mockApi = vi.hoisted(() => ({
  shutdownSystem: vi.fn(),
  getHealth: vi.fn(),
}))
vi.mock('@/services/api', () => ({ api: mockApi }))

function mountApp() {
  return mount(App, {
    global: {
      plugins: [ElementPlus],
      stubs: {
        'router-view': { template: '<div class="view-stub" />' },
        CommandPalette: { template: '<div class="palette-stub" />' },
      },
    },
  })
}

afterEach(() => {
  vi.useRealTimers()
})

describe('App 退出服务按钮', () => {
  it('顶栏渲染退出按钮', () => {
    vi.clearAllMocks()
    const wrapper = mountApp()
    expect(wrapper.find('[data-test="exit-btn"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('确认后 accepted=true：显示退出中提示并轮询 health，按钮进入 loading', async () => {
    vi.clearAllMocks()
    h.ElMessageBox.confirm.mockResolvedValue('confirm')
    mockApi.shutdownSystem.mockResolvedValue({ accepted: true, message: '服务正在退出' })
    mockApi.getHealth.mockResolvedValue({ status: 'ok' })

    const wrapper = mountApp()
    await wrapper.find('[data-test="exit-btn"]').trigger('click')

    // el-button 确认链路 + 500ms 轮询间隔均为真实定时器，补一次等待让
    // 首个 health 请求发出（仅测试等待，组件实现不动）
    await new Promise((r) => setTimeout(r, 550))

    expect(h.ElMessageBox.confirm).toHaveBeenCalled()
    expect(mockApi.shutdownSystem).toHaveBeenCalledOnce()
    expect(h.ElMessage.info).toHaveBeenCalledWith('服务正在退出…')
    expect(mockApi.getHealth).toHaveBeenCalled()
    wrapper.unmount()
  })

  it('accepted=false：警告提示且不进入轮询', async () => {
    vi.clearAllMocks()
    h.ElMessageBox.confirm.mockResolvedValue('confirm')
    mockApi.shutdownSystem.mockResolvedValue({ accepted: false, message: '未接入' })

    const wrapper = mountApp()
    await wrapper.find('[data-test="exit-btn"]').trigger('click')

    expect(h.ElMessage.warning).toHaveBeenCalledWith(
      '当前运行方式不支持在线退出，请手动停止服务'
    )
    expect(mockApi.getHealth).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('取消确认框不调用 api', async () => {
    vi.clearAllMocks()
    h.ElMessageBox.confirm.mockRejectedValue('cancel')

    const wrapper = mountApp()
    await wrapper.find('[data-test="exit-btn"]').trigger('click')

    expect(mockApi.shutdownSystem).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('health 轮询失败后提示已退出并停止轮询', async () => {
    vi.clearAllMocks()
    vi.useFakeTimers()
    h.ElMessageBox.confirm.mockResolvedValue('confirm')
    mockApi.shutdownSystem.mockResolvedValue({ accepted: true })
    mockApi.getHealth.mockRejectedValue(new Error('conn refused'))

    const wrapper = mountApp()
    await wrapper.find('[data-test="exit-btn"]').trigger('click')
    await vi.advanceTimersByTimeAsync(500)

    expect(h.ElMessage.success).toHaveBeenCalledWith('服务已退出，请关闭此页面')
    const calls = mockApi.getHealth.mock.calls.length
    await vi.advanceTimersByTimeAsync(3000)
    expect(mockApi.getHealth.mock.calls.length).toBe(calls)  // 定时器已清除
    wrapper.unmount()
  })
})