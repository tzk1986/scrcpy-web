/**
 * DebugPanel 组件测试
 * ====================
 *
 * 覆盖：
 *   - 标签页渲染与切换（Logcat/Shell/Network/Perf/Apps，未知标签页兜底）
 *   - 子组件 deviceId 透传、停靠位置切换与 position-change 事件
 *   - 键盘快捷键（Ctrl+` / Ctrl+L / Ctrl+Shift+`）与命令面板 CustomEvent
 *   - DockPanel 尺寸 v-model 回写、卸载后监听移除
 */
import { describe, expect, it } from 'vitest'
import { defineComponent, h, nextTick } from 'vue'
import { mount, type VueWrapper } from '@vue/test-utils'

import DebugPanel from './DebugPanel.vue'
import DockPanel from '@/components/ui/DockPanel.vue'

// ===== Element Plus / 子视图轻量 stub =====

const ElButton = defineComponent({
  name: 'ElButton',
  props: { loading: Boolean },
  inheritAttrs: false,
  setup(props, { slots, attrs }) {
    return () =>
      h('button', { ...attrs, 'data-loading': String(!!props.loading) }, slots.default?.())
  },
})

const ElTabs = defineComponent({
  name: 'ElTabs',
  props: { modelValue: { type: String, default: '' } },
  emits: ['update:modelValue'],
  setup(_, { slots }) {
    return () => h('div', { class: 'el-tabs' }, slots.default?.())
  },
})

const ElTabPane = defineComponent({
  name: 'ElTabPane',
  props: { label: String, name: String },
  setup(props) {
    return () => h('div', { class: 'el-tab-pane', 'data-name': props.name }, props.label)
  },
})

const ElTooltip = defineComponent({
  name: 'ElTooltip',
  setup(_, { slots }) {
    return () => h('span', { class: 'el-tooltip' }, slots.default?.())
  },
})

/** 子视图 stub：把 deviceId 渲染进文本便于断言透传。 */
function makeViewStub(name: string) {
  return defineComponent({
    name,
    props: { deviceId: String },
    setup(props) {
      return () => h('div', { class: `view-${name}` }, `${name}:${props.deviceId}`)
    },
  })
}

function mountPanel(): VueWrapper {
  return mount(DebugPanel, {
    props: { deviceId: 'dev-42' },
    global: {
      stubs: {
        ElButton,
        ElTabs,
        ElTabPane,
        ElTooltip,
        LogcatView: makeViewStub('LogcatView'),
        ShellView: makeViewStub('ShellView'),
        NetworkView: makeViewStub('NetworkView'),
        PerfView: makeViewStub('PerfView'),
        AppsView: makeViewStub('AppsView'),
      },
    },
  })
}

/** 派发键盘事件并返回，便于断言 preventDefault。 */
function pressKey(init: KeyboardEventInit): KeyboardEvent {
  const ev = new KeyboardEvent('keydown', { cancelable: true, ...init })
  window.dispatchEvent(ev)
  return ev
}

/** 派发命令面板切换标签事件。 */
function setDebugTab(detail: string) {
  window.dispatchEvent(new CustomEvent('openscrcpy:set-debug-tab', { detail }))
}

describe('DebugPanel', () => {
  it('默认渲染 Logcat 标签并把 deviceId 透传给子视图', async () => {
    const wrapper = mountPanel()
    await nextTick()

    // 5 个标签页标题
    expect(wrapper.findAll('.el-tab-pane').map((n) => n.text())).toEqual([
      'Logcat',
      'Shell',
      'Network',
      'Perf',
      'Apps',
    ])

    // 默认激活 logcat，仅渲染对应子视图
    expect(wrapper.find('.view-LogcatView').text()).toBe('LogcatView:dev-42')
    expect(wrapper.find('.view-ShellView').exists()).toBe(false)
    expect(wrapper.find('.view-PerfView').exists()).toBe(false)

    // DockPanel 收到默认停靠参数
    const dock = wrapper.findComponent(DockPanel)
    expect(dock.props('position')).toBe('right')
    expect(dock.props('width')).toBe(500)
    expect(dock.props('height')).toBe(400)
    // right 模式下按钮显示「移到底部」符号
    expect(wrapper.find('.debug-actions button').text()).toBe('⇩')

    wrapper.unmount()
  })

  it('切换标签页（v-model）并渲染对应子视图，未知标签页兜底', async () => {
    const wrapper = mountPanel()
    await nextTick()

    wrapper.findComponent(ElTabs).vm.$emit('update:modelValue', 'perf')
    await nextTick()
    expect(wrapper.find('.view-PerfView').text()).toBe('PerfView:dev-42')
    expect(wrapper.find('.view-LogcatView').exists()).toBe(false)

    wrapper.findComponent(ElTabs).vm.$emit('update:modelValue', 'apps')
    await nextTick()
    expect(wrapper.find('.view-AppsView').exists()).toBe(true)
    expect(wrapper.find('.view-PerfView').exists()).toBe(false)

    // 非法标签名 → v-else 兜底
    wrapper.findComponent(ElTabs).vm.$emit('update:modelValue', 'nope')
    await nextTick()
    expect(wrapper.find('.view-AppsView').exists()).toBe(false)
    expect(wrapper.find('.coming-soon').text()).toBe('未知标签页')

    wrapper.unmount()
  })

  it('点击按钮切换停靠位置并 emit position-change', async () => {
    const wrapper = mountPanel()
    await nextTick()

    await wrapper.find('.debug-actions button').trigger('click')
    expect(wrapper.emitted('position-change')?.[0]).toEqual(['bottom'])
    expect(wrapper.findComponent(DockPanel).props('position')).toBe('bottom')
    expect(wrapper.find('.debug-actions button').text()).toBe('⇨')

    await wrapper.find('.debug-actions button').trigger('click')
    expect(wrapper.emitted('position-change')?.[1]).toEqual(['right'])
    expect(wrapper.findComponent(DockPanel).props('position')).toBe('right')
    expect(wrapper.find('.debug-actions button').text()).toBe('⇩')

    wrapper.unmount()
  })

  it('DockPanel 尺寸变化通过 v-model 回写', async () => {
    const wrapper = mountPanel()
    await nextTick()
    const dock = wrapper.findComponent(DockPanel)

    dock.vm.$emit('update:width', 640)
    dock.vm.$emit('update:height', 320)
    await nextTick()

    expect(wrapper.findComponent(DockPanel).props('width')).toBe(640)
    expect(wrapper.findComponent(DockPanel).props('height')).toBe(320)

    wrapper.unmount()
  })

  it('键盘快捷键切换位置与标签页，并阻止默认行为', async () => {
    const wrapper = mountPanel()
    await nextTick()

    // Ctrl+` → 切换停靠位置
    const ev1 = pressKey({ key: '`', ctrlKey: true })
    await nextTick()
    expect(ev1.defaultPrevented).toBe(true)
    expect(wrapper.emitted('position-change')?.[0]).toEqual(['bottom'])

    // Ctrl+L → 回到 Logcat
    wrapper.findComponent(ElTabs).vm.$emit('update:modelValue', 'perf')
    await nextTick()
    const ev2 = pressKey({ key: 'l', ctrlKey: true })
    await nextTick()
    expect(ev2.defaultPrevented).toBe(true)
    expect(wrapper.find('.view-LogcatView').exists()).toBe(true)

    // Ctrl+Shift+` → Shell 标签
    const ev3 = pressKey({ key: '`', ctrlKey: true, shiftKey: true })
    await nextTick()
    expect(ev3.defaultPrevented).toBe(true)
    expect(wrapper.find('.view-ShellView').exists()).toBe(true)

    // 无关按键不拦截
    const ev4 = pressKey({ key: 'a' })
    expect(ev4.defaultPrevented).toBe(false)
    expect(wrapper.find('.view-ShellView').exists()).toBe(true)

    wrapper.unmount()
  })

  it('命令面板通过 CustomEvent 切换标签页，非法值忽略', async () => {
    const wrapper = mountPanel()
    await nextTick()

    setDebugTab('apps')
    await nextTick()
    expect(wrapper.find('.view-AppsView').exists()).toBe(true)

    setDebugTab('not-a-tab')
    await nextTick()
    // 非法值不影响当前标签
    expect(wrapper.find('.view-AppsView').exists()).toBe(true)

    setDebugTab('network')
    await nextTick()
    expect(wrapper.find('.view-NetworkView').exists()).toBe(true)

    wrapper.unmount()
  })

  it('卸载后移除全局监听', async () => {
    const wrapper = mountPanel()
    await nextTick()
    wrapper.unmount()

    pressKey({ key: '`', ctrlKey: true })
    setDebugTab('shell')
    await nextTick()

    // 卸载后不再响应快捷键与 CustomEvent
    expect(wrapper.emitted('position-change')).toBeUndefined()
  })
})