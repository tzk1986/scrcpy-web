/**
 * DockPanel 组件测试
 * ===================
 *
 * 覆盖：
 *   - 默认右侧停靠：宽度样式、header/default 插槽渲染、无 header 插槽时不渲染头部
 *   - 右侧模式拖拽改宽：body 光标切换、实时宽度、上限钳制、mouseup 后 emit update:width
 *   - 底部模式拖拽改高：emit update:height 与上限钳制
 */
import { afterEach, describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import DockPanel from './DockPanel.vue'

function mountPanel(props: Record<string, unknown> = {}) {
  return mount(DockPanel, {
    props,
    slots: {
      header: '<span class="hdr-text">面板头</span>',
      default: '<div class="body">内容区</div>',
    },
  })
}

/** 在 document 上派发拖拽过程事件（startResize 监听 document）。 */
function moveMouse(x: number, y: number) {
  document.dispatchEvent(new MouseEvent('mousemove', { clientX: x, clientY: y }))
}

function releaseMouse() {
  document.dispatchEvent(new MouseEvent('mouseup'))
}

afterEach(() => {
  document.body.style.cursor = ''
  document.body.style.userSelect = ''
})

describe('DockPanel', () => {
  it('默认右侧停靠渲染宽度样式与插槽内容', () => {
    const wrapper = mountPanel()

    expect(wrapper.classes()).toContain('position-right')
    expect(wrapper.attributes('style')).toContain('width: 500px')
    expect(wrapper.find('.dock-resize-handle').classes()).toContain('resize-right')
    expect(wrapper.find('.hdr-text').text()).toBe('面板头')
    expect(wrapper.find('.body').text()).toBe('内容区')

    // 未提供 header 插槽时不渲染头部
    const bare = mount(DockPanel)
    expect(bare.find('.dock-header').exists()).toBe(false)
  })

  it('右侧模式拖拽改宽：宽度实时更新、超上限钳制、mouseup 后 emit', async () => {
    const wrapper = mountPanel()

    await wrapper.find('.dock-resize-handle').trigger('mousedown', { clientX: 500, clientY: 100 })
    expect(document.body.style.cursor).toBe('ew-resize')
    expect(document.body.style.userSelect).toBe('none')

    moveMouse(350, 100) // delta = 500 - 350 = 150 → 500 + 150
    await nextTick()
    expect(wrapper.attributes('style')).toContain('width: 650px')

    moveMouse(-400, 100) // delta = 900 → 被 maxWidth(800) 钳制
    await nextTick()
    expect(wrapper.attributes('style')).toContain('width: 800px')

    releaseMouse()
    await nextTick()
    expect(wrapper.emitted('update:width')?.at(-1)).toEqual([800])
    expect(document.body.style.cursor).toBe('')
  })

  it('底部模式拖拽改高：height 样式更新并在 mouseup 后 emit update:height', async () => {
    const wrapper = mountPanel({ position: 'bottom', height: 400 })

    expect(wrapper.classes()).toContain('position-bottom')
    expect(wrapper.attributes('style')).toContain('height: 400px')

    // 头部同样是拖拽手柄
    await wrapper.find('.dock-header').trigger('mousedown', { clientX: 10, clientY: 400 })
    expect(document.body.style.cursor).toBe('ns-resize')

    moveMouse(10, 300) // delta = 400 - 300 = 100 → 400 + 100
    await nextTick()
    expect(wrapper.attributes('style')).toContain('height: 500px')

    moveMouse(10, -1000) // 超上限 → 钳制到 maxHeight(800)
    await nextTick()
    expect(wrapper.attributes('style')).toContain('height: 800px')

    releaseMouse()
    await nextTick()
    expect(wrapper.emitted('update:height')?.at(-1)).toEqual([800])

    // 拖拽结束后不再响应 document mousemove
    moveMouse(10, 500)
    await nextTick()
    expect(wrapper.attributes('style')).toContain('height: 800px')
  })
})