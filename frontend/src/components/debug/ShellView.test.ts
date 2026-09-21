/**
 * ShellView 组件测试（PTY 终端）
 * ==============================
 *
 * 覆盖：
 *   - 挂载：创建 80x24 Terminal、加载 FitAddon 并 fit、写入欢迎信息、卸载 dispose
 *   - 输入框发送命令：空命令不发送、非空命令追加 \n 且清空输入框
 *   - 命令历史：↑↓ 在历史中前后切换
 *   - Tab 补全透传：输入框 Tab 与终端 onKey('\t') 均直接 sendInput('\t')
 *
 * 说明：xterm.js 依赖真实 DOM 渲染，测试中以 mock 替换 Terminal/FitAddon；
 * debug store 使用真实实例，sendInput 以 spy 断言。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, shallowMount } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'

const h = vi.hoisted(() => {
  class MockTerminal {
    static instances: MockTerminal[] = []
    options: Record<string, unknown>
    open = vi.fn()
    write = vi.fn()
    writeln = vi.fn()
    loadAddon = vi.fn()
    dispose = vi.fn()
    onKey = vi.fn()
    onData = vi.fn()
    keyHandler: ((e: { key: string; domEvent: { preventDefault(): void; stopPropagation(): void } }) => void) | null = null
    dataHandler: ((data: string) => void) | null = null

    constructor(options: Record<string, unknown>) {
      this.options = options
      this.onKey.mockImplementation((cb: typeof this.keyHandler) => {
        this.keyHandler = cb
      })
      this.onData.mockImplementation((cb: typeof this.dataHandler) => {
        this.dataHandler = cb
      })
      MockTerminal.instances.push(this)
    }
  }

  class MockFitAddon {
    static instances: MockFitAddon[] = []
    fit = vi.fn()

    constructor() {
      MockFitAddon.instances.push(this)
    }
  }

  MockTerminal.instances = []
  MockFitAddon.instances = []
  return { MockTerminal, MockFitAddon }
})

vi.mock('@xterm/xterm', () => ({ Terminal: h.MockTerminal }))
vi.mock('xterm-addon-fit', () => ({ FitAddon: h.MockFitAddon }))

import ShellView from './ShellView.vue'
import { useDebugStore } from '@/stores/debug'

let pinia: Pinia

async function mountView() {
  const wrapper = shallowMount(ShellView, {
    props: { deviceId: 'dev1' },
    global: { plugins: [pinia] },
  })
  await flushPromises()
  return wrapper
}

function inputValue(wrapper: ReturnType<typeof shallowMount>): string {
  return (wrapper.find('input.command-input').element as HTMLInputElement).value
}

beforeEach(() => {
  pinia = createPinia()
  setActivePinia(pinia)
  h.MockTerminal.instances = []
  h.MockFitAddon.instances = []
})

describe('ShellView', () => {
  it('挂载时初始化 80x24 终端并在卸载时销毁', async () => {
    const wrapper = await mountView()

    const term = h.MockTerminal.instances[0]
    expect(term).toBeDefined()
    expect(term.options).toMatchObject({ cols: 80, rows: 24 })
    expect(term.open).toHaveBeenCalledWith(wrapper.find('.terminal').element)
    expect(term.writeln).toHaveBeenCalledWith('OpenScrcpy Shell (PTY Mode)')

    const addon = h.MockFitAddon.instances[0]
    expect(term.loadAddon).toHaveBeenCalledWith(addon)
    expect(addon.fit).toHaveBeenCalled()

    expect(wrapper.find('input.command-input').attributes('placeholder')).toContain('Enter 发送')
    expect(wrapper.find('button.send-btn').text()).toBe('发送')

    wrapper.unmount()
    expect(term.dispose).toHaveBeenCalled()
  })

  it('输入框发送命令：空命令忽略，非空命令追加换行并清空输入', async () => {
    const store = useDebugStore()
    const sendSpy = vi.spyOn(store, 'sendInput').mockImplementation(() => {})
    const wrapper = await mountView()

    await wrapper.find('button.send-btn').trigger('click')
    expect(sendSpy).not.toHaveBeenCalled()

    const input = wrapper.find('input.command-input')
    await input.setValue('ls -l')
    await wrapper.find('button.send-btn').trigger('click')

    expect(sendSpy).toHaveBeenCalledWith('ls -l\n')
    expect(inputValue(wrapper)).toBe('')

    // 纯空白命令同样不发送
    await input.setValue('   ')
    await wrapper.find('button.send-btn').trigger('click')
    expect(sendSpy).toHaveBeenCalledTimes(1)
  })

  it('命令历史：↑ 向上回溯、↓ 向前切换', async () => {
    const store = useDebugStore()
    vi.spyOn(store, 'sendInput').mockImplementation(() => {})
    const wrapper = await mountView()
    const input = wrapper.find('input.command-input')

    await input.setValue('echo one')
    await input.trigger('keydown', { key: 'Enter' })
    await input.setValue('echo two')
    await input.trigger('keydown', { key: 'Enter' })
    expect(inputValue(wrapper)).toBe('')

    await input.trigger('keydown', { key: 'ArrowUp' })
    expect(inputValue(wrapper)).toBe('echo two')

    await input.trigger('keydown', { key: 'ArrowUp' })
    expect(inputValue(wrapper)).toBe('echo one')

    await input.trigger('keydown', { key: 'ArrowDown' })
    expect(inputValue(wrapper)).toBe('echo two')

    await input.trigger('keydown', { key: 'ArrowDown' })
    expect(inputValue(wrapper)).toBe('')
  })

  it('Tab 补全透传：输入框 Tab 与终端按键均发送 \\t，终端普通按键透传', async () => {
    const store = useDebugStore()
    const sendSpy = vi.spyOn(store, 'sendInput').mockImplementation(() => {})
    const wrapper = await mountView()
    const term = h.MockTerminal.instances[0]

    await wrapper.find('input.command-input').trigger('keydown', { key: 'Tab' })
    expect(sendSpy).toHaveBeenCalledWith('\t')

    // 终端内 Tab：拦截默认行为后发送 \t
    const domEvent = { preventDefault: vi.fn(), stopPropagation: vi.fn() }
    term.keyHandler!({ key: '\t', domEvent })
    expect(domEvent.preventDefault).toHaveBeenCalled()
    expect(domEvent.stopPropagation).toHaveBeenCalled()
    expect(sendSpy).toHaveBeenLastCalledWith('\t')

    // 终端普通按键透传
    term.dataHandler!('a')
    expect(sendSpy).toHaveBeenLastCalledWith('a')
  })
})