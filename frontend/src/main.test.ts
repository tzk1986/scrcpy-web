/**
 * main.ts 全局错误兜底测试（方案 51 F2-07）
 * =========================================
 *
 * main 模块加载即挂载，本测试用 mock 隔离视图层与插件，聚焦三件事：
 *   - 注册了 app.config.errorHandler 与 unhandledrejection 监听并挂载
 *   - console.error 全量记录（排障依据不丢）
 *   - 同类轻提示 7s debounce：同文案连发只出一条、不同文案立即出
 *
 * vi.resetModules + 动态 import 保证模块级 debounce 态每例独立。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const h = vi.hoisted(() => ({
  ElMessage: { error: vi.fn() },
  mount: vi.fn(),
  setHandler: vi.fn(),
}))

vi.mock('element-plus', () => ({
  default: { install: vi.fn() },
  ElMessage: h.ElMessage,
}))
vi.mock('pinia', () => ({ default: { install: vi.fn() }, createPinia: () => ({ install: vi.fn() }) }))
vi.mock('vue', () => ({
  createApp: () => ({
    use: () => createAppReturn(),
    mount: h.mount,
    config: {
      set errorHandler(fn: (err: unknown) => void) {
        h.setHandler(fn)
      },
    },
  }),
}))

function createAppReturn() {
  return {
    use: () => createAppReturn(),
    mount: h.mount,
    config: {
      set errorHandler(fn: (err: unknown) => void) {
        h.setHandler(fn)
      },
    },
  }
}

vi.mock('./App.vue', () => ({ default: {} }))

async function importMain(): Promise<(err: unknown) => void> {
  vi.resetModules()
  await import('./main')
  return h.setHandler.mock.calls.at(-1)?.[0] as (err: unknown) => void
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('main.ts 全局错误兜底', () => {
  it('加载即注册 errorHandler 与 unhandledrejection 并挂载应用', async () => {
    const addSpy = vi.spyOn(window, 'addEventListener')
    const fn = await importMain()

    expect(fn).toBeTypeOf('function')
    expect(addSpy.mock.calls.some((c) => c[0] === 'unhandledrejection')).toBe(true)
    expect(h.mount).toHaveBeenCalledWith('#app')
    addSpy.mockRestore()
  })

  it('errorHandler 与 rejection：console 全量记录，同文案 debounce、异文案立出', async () => {
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    const fn = await importMain()

    fn(new Error('boom'))
    expect(errSpy).toHaveBeenCalledWith('[App] Unhandled error:', expect.any(Error))
    expect(h.ElMessage.error).toHaveBeenCalledWith('页面发生异常，如持续出现请刷新重试')

    // 同文案连发：debounce 窗口内只出一条，但 console 记录不丢
    fn(new Error('boom2'))
    expect(errSpy).toHaveBeenCalledTimes(2)
    expect(h.ElMessage.error).toHaveBeenCalledTimes(1)

    // 不同文案（rejection 链）：立出
    const rejectionEvent = new Event('unhandledrejection')
    Object.defineProperty(rejectionEvent, 'reason', { value: 'net down' })
    window.dispatchEvent(rejectionEvent)
    expect(errSpy).toHaveBeenCalledWith('[App] Unhandled rejection:', 'net down')
    expect(h.ElMessage.error).toHaveBeenCalledWith('后台请求异常，如持续出现请刷新重试')

    errSpy.mockRestore()
  })
})
