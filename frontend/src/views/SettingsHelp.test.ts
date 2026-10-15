/**
 * SettingsHelp 参数手册页测试（方案 47 T6）
 * ==========================================
 *
 * 覆盖：
 *   - 分组渲染：四组卡片、锚点 id=`<组>-<键>`、双向影响/适合场景/范围/生效时机文案
 *   - hash 直达：挂载后按 route.hash 定位锚点 scrollIntoView；无 hash 不滚动
 *   - 智能返回（评审焦点 #6）：上一跳恰为 /settings 或 /settings?device=… →
 *     back 不垫栈；其余来源 → push /settings
 *
 * 说明：照 Settings.test.ts 模式 mock vue-router；纯静态页无 API。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, shallowMount, type VueWrapper } from '@vue/test-utils'
import { defineComponent, h } from 'vue'

const routeMock = vi.hoisted(() => ({
  current: { hash: '' as string, query: {} as Record<string, unknown> },
}))
const routerPushMock = vi.hoisted(() => vi.fn())
vi.mock('vue-router', () => ({
  useRoute: () => routeMock.current,
  useRouter: () => ({ push: routerPushMock }),
}))

import SettingsHelp from './SettingsHelp.vue'
import { PARAM_HELP, anchorId } from './settingsHelpData'

const ElButtonStub = defineComponent({
  name: 'ElButton',
  inheritAttrs: false,
  setup(_props, { slots, attrs }) {
    return () => h('button', { ...attrs, class: 'stub-btn' }, slots.default?.())
  },
})

const ElCardStub = defineComponent({
  name: 'ElCard',
  inheritAttrs: false,
  setup(_props, { slots, attrs }) {
    return () =>
      h('div', { ...attrs, class: 'stub-card' }, [
        h('div', { class: 'stub-card-header' }, slots.header?.()),
        slots.default?.(),
      ])
  },
})

function setHistoryBack(back: string | undefined) {
  Object.defineProperty(window.history, 'state', {
    configurable: true,
    value: back === undefined ? null : { back },
  })
}

/** attach 到 document 供 hash 用料的 getElementById 定位（组件经 document 查询） */
async function mountHelp(attach = false): Promise<VueWrapper> {
  const wrapper = shallowMount(SettingsHelp, {
    attachTo: attach ? document.body : undefined,
    global: { stubs: { 'el-button': ElButtonStub, 'el-card': ElCardStub } },
  })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  vi.clearAllMocks()
  routeMock.current = { hash: '', query: {} }
  setHistoryBack(undefined)
})

describe('SettingsHelp.vue', () => {
  it('分组渲染四组卡片与全部参数条目，锚点 id 为「组-键」且全局唯一', async () => {
    const wrapper = await mountHelp()

    for (const id of ['stream', 'recording', 'override', 'general']) {
      expect(wrapper.find(`[data-test="group-${id}"]`).exists()).toBe(true)
    }
    expect(wrapper.findAll('.param-entry')).toHaveLength(PARAM_HELP.length)

    const anchors = wrapper.findAll('.param-entry').map((el) => el.attributes('id'))
    expect(new Set(anchors).size).toBe(PARAM_HELP.length)
    expect(anchors).toContain(anchorId({ group: 'stream', key: 'bit_rate' }))
    expect(anchors).toContain(anchorId({ group: 'recording', key: 'bit_rate' }))
  })

  it('串流 max_size 条目含含义/调大调小双向影响/范围/生效时机', async () => {
    const wrapper = await mountHelp()
    const entry = wrapper.find('#stream-max_size')
    expect(entry.exists()).toBe(true)
    expect(entry.text()).toContain('长边上限')
    expect(entry.text()).toContain('调大')
    expect(entry.text()).toContain('更清晰')
    expect(entry.text()).toContain('调小')
    expect(entry.text()).toContain('省带宽')
    expect(entry.text()).toContain('适合：')
    expect(entry.text()).toContain('范围：0 ~ 65535')
    expect(entry.text()).toContain('生效时机：下次连接生效')
  })

  it('布尔/枚举参数用覆写方向词（codec 选 h265/选 h264、自适应码率 开/关）', async () => {
    const wrapper = await mountHelp()
    expect(wrapper.find('#stream-codec').text()).toContain('选 h265')
    expect(wrapper.find('#stream-codec').text()).toContain('选 h264')
    const adaptive = wrapper.find('#stream-adaptive_bitrate').text()
    expect(adaptive).toContain('开')
    expect(adaptive).toContain('关')
    expect(adaptive).not.toContain('调大')
  })

  it('hash 直达挂载后定位锚点 scrollIntoView；无 hash 不触发', async () => {
    const scrollSpy = vi.fn()
    Element.prototype.scrollIntoView = scrollSpy

    routeMock.current = { hash: '#recording-time_limit', query: {} }
    const wrapper = await mountHelp(true)
    expect(scrollSpy).toHaveBeenCalledTimes(1)
    wrapper.unmount()

    scrollSpy.mockClear()
    routeMock.current = { hash: '', query: {} }
    const w2 = await mountHelp(true)
    expect(scrollSpy).not.toHaveBeenCalled()
    w2.unmount()
  })

  it('智能返回：上一跳为 /settings 或 /settings?… 时 back 不垫栈，否则 push', async () => {
    const backSpy = vi.spyOn(window.history, 'back').mockImplementation(() => {})

    setHistoryBack('/settings')
    let wrapper = await mountHelp()
    await wrapper.find('[data-test="back-settings"]').trigger('click')
    expect(backSpy).toHaveBeenCalledTimes(1)
    expect(routerPushMock).not.toHaveBeenCalled()
    wrapper.unmount()

    backSpy.mockClear()
    setHistoryBack('/settings?device=d1')
    wrapper = await mountHelp()
    await wrapper.find('[data-test="back-settings"]').trigger('click')
    expect(backSpy).toHaveBeenCalledTimes(1)
    wrapper.unmount()

    backSpy.mockClear()
    setHistoryBack('/recordings')
    wrapper = await mountHelp()
    await wrapper.find('[data-test="back-settings"]').trigger('click')
    expect(backSpy).not.toHaveBeenCalled()
    expect(routerPushMock).toHaveBeenCalledWith('/settings')
    wrapper.unmount()

    backSpy.mockRestore()
  })
})
