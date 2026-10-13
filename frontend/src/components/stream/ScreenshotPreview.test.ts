/**
 * ScreenshotPreview 组件测试（方案 37 B7）
 * ========================================
 *
 * 覆盖：
 *   - 渲染：visible 时弹窗渲染画布（尺寸=图片自然尺寸）与完整工具栏；不可见时不渲染
 *   - 图片入参：Blob → 组件自建 object URL 且关闭时回收；URL 字符串 → 直接使用、不回收
 *   - 画笔/箭头/矩形：pointer 拖拽提交命令（fake ctx 记录 beginPath/lineTo/stroke/strokeRect）
 *   - 文字：行内输入 + 「确认」后点击画布放置（fillText 携带文案）；未确认不落字
 *   - 撤销/清空：命令栈出栈/归零 + 画布重绘（clearRect）；无标注时按钮置灰
 *   - 参数化与双栈：命令存字号/线宽比例参数（渲染 ×图宽换算）；undo→redo 恢复、落新命令清 redo
 *   - 参数组与快捷键：滑块/滚轮调字号线宽（钳制）、滑块显隐、数字键切工具、Ctrl+Z/Ctrl+Shift+Z
 *     （window 监听；输入框聚焦时数字键不劫持）
 *   - 导出 PNG：canvas.toBlob(..., 'image/png') 产出 → annotated 文件名下载 + 产物 URL 回收
 *   - 直接保存：原始 blob URL 以原文件名下载，弹窗打开期不回收
 *
 * 说明：happy-dom 的 canvas.getContext('2d') 返回 null、无真实绘制——按语义层断言：
 * 把 2d context mock 成记录调用（clearRect/drawImage/beginPath/moveTo/lineTo/stroke/
 * strokeRect/fillText 等；其余绘制方法与属性 setter 一并预埋供后续任务），toBlob 同步
 * 回调产出 Blob；全局 Image 用 FakeImage（自然尺寸 800x600，src 赋值异步触发 onload）。
 * 仿 AppsView.test.ts 模式（el-* 轻量 stub）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, shallowMount, type VueWrapper } from '@vue/test-utils'
import { nextTick } from 'vue'
import ScreenshotPreview from './ScreenshotPreview.vue'

/** 记录全部绘制调用的 fake 2d context。 */
function createRecordingCtx() {
  const calls: string[] = []
  const fillTextLog: unknown[][] = []
  const fontLog: string[] = []
  const record = (name: string) => () => {
    calls.push(name)
  }
  return {
    calls,
    fillTextLog,
    fontLog,
    clearRect: record('clearRect'),
    drawImage: record('drawImage'),
    beginPath: record('beginPath'),
    moveTo: record('moveTo'),
    lineTo: record('lineTo'),
    stroke: record('stroke'),
    strokeRect: record('strokeRect'),
    ellipse: record('ellipse'),
    arc: record('arc'),
    fill: record('fill'),
    setLineDash: record('setLineDash'),
    save: record('save'),
    restore: record('restore'),
    measureText: (t: unknown) => ({ width: String(t).length * 12 }),
    fillText: (...args: unknown[]) => {
      calls.push('fillText')
      fillTextLog.push(args)
    },
    set strokeStyle(_v: string | CanvasGradient | CanvasPattern) {},
    set fillStyle(_v: string | CanvasGradient | CanvasPattern) {},
    set lineWidth(_v: number) {},
    set lineCap(_v: CanvasLineCap) {},
    set font(v: string) {
      fontLog.push(v)
    },
    set globalAlpha(_v: number) {},
    set imageSmoothingEnabled(_v: boolean) {},
    set textAlign(_v: CanvasTextAlign) {},
    set textBaseline(_v: CanvasTextBaseline) {},
  }
}

const fakeCtx = createRecordingCtx()
/** 返回给组件 getContext('2d') 的 fake context（记录调用，无真实绘制）。 */
const fakeCtx2d = fakeCtx as unknown as CanvasRenderingContext2D

/** 图片加载替身：自然尺寸 800x600，src 赋值后异步触发 onload（模拟解码）。 */
class FakeImage {
  naturalWidth = 800
  naturalHeight = 600
  onload: (() => void) | null = null
  onerror: (() => void) | null = null
  set src(_value: string) {
    // microtask 触发（happy-dom 的 setTimeout 与 flushPromises 定时器不保证 FIFO，
    // 微任务先于所有定时器执行，保证挂载 settle 前底图必已就绪）
    queueMicrotask(() => this.onload?.())
  }
}

const createUrl = vi.fn()
const revokeUrl = vi.fn()
const getContextMock = vi.fn()
const toBlobMock = vi.fn()

/** 测试内被点击的 a 元素集合（下载断言用）。 */
let anchors: HTMLAnchorElement[] = []

const ElDialogStub = {
  name: 'ElDialog',
  props: ['modelValue', 'title'],
  emits: ['update:modelValue'],
  template:
    '<div v-if="modelValue" class="stub-dialog"><div class="stub-dialog-title">{{ title }}</div><slot /></div>',
}

const ElButtonStub = {
  name: 'ElButton',
  template: '<button class="stub-btn" type="button"><slot /></button>',
}

interface PreviewOverrides {
  visible?: boolean
  image?: Blob | string | null
  deviceId?: string
}

function mountPreview(overrides: PreviewOverrides = {}) {
  return shallowMount(ScreenshotPreview, {
    props: {
      visible: true,
      image: new Blob(['png-bytes'], { type: 'image/png' }),
      deviceId: 'dev/1:5555',
      ...overrides,
    },
    global: {
      stubs: { 'el-dialog': ElDialogStub, 'el-button': ElButtonStub },
    },
  })
}

/** 挂载并等图片加载完成（FakeImage 异步 onload → 画布按自然尺寸定尺寸）。 */
async function mountAndSettle(overrides: PreviewOverrides = {}) {
  const wrapper = mountPreview(overrides)
  await flushPromises()
  await nextTick()
  return wrapper
}

function canvasOf(wrapper: VueWrapper): HTMLCanvasElement {
  return wrapper.find('canvas').element as HTMLCanvasElement
}

/** 把画布显示尺寸 mock 成 400x300（单元格像素空间 800x600 → 2x 缩放映射校验）。 */
function mockCanvasRect(wrapper: VueWrapper) {
  Object.defineProperty(canvasOf(wrapper), 'getBoundingClientRect', {
    configurable: true,
    value: () =>
      ({
        x: 0, y: 0, width: 400, height: 300,
        top: 0, left: 0, right: 400, bottom: 300,
        toJSON: () => ({}),
      }) as DOMRect,
  })
}

/** 读取组件暴露的已提交标注数（对应内部命令栈长度语义）。 */
function commandCount(wrapper: VueWrapper): number {
  return (wrapper.vm as unknown as { getAnnotationCount: () => number }).getAnnotationCount()
}

/** 读取组件暴露的命令栈快照（参数化断言用）。 */
function commandList(
  wrapper: VueWrapper,
): Array<{ kind: string; lineWidth?: number; fontSize?: number }> {
  return (
    wrapper.vm as unknown as {
      getCommands: () => Array<{ kind: string; lineWidth?: number; fontSize?: number }>
    }
  ).getCommands()
}

/** 读取组件暴露的 redo 栈长度（双栈语义断言用）。 */
function redoCount(wrapper: VueWrapper): number {
  return (wrapper.vm as unknown as { getRedoCount: () => number }).getRedoCount()
}

/** 读取组件暴露的默认字号比例（滚轮/滑块调参断言用）。 */
function defaultFontRatio(wrapper: VueWrapper): number {
  return (wrapper.vm as unknown as { getDefaultFontRatio: () => number }).getDefaultFontRatio()
}

/** 读取组件暴露的默认线宽比例（滚轮/滑块调参断言用）。 */
function defaultLineRatio(wrapper: VueWrapper): number {
  return (wrapper.vm as unknown as { getDefaultLineRatio: () => number }).getDefaultLineRatio()
}

/** 选工具 → pointer 拖拽 → 松手提交；返回前清空 ctx 记录只留提交时刻的重放。 */
async function drawShape(wrapper: VueWrapper, toolClass: string, to = { x: 60, y: 40 }) {
  await wrapper.find(toolClass).trigger('click')
  const canvas = wrapper.find('canvas')
  await canvas.trigger('pointerdown', { clientX: 10, clientY: 10 })
  await canvas.trigger('pointermove', { clientX: to.x, clientY: to.y })
  fakeCtx.calls.length = 0
  await canvas.trigger('pointerup', { clientX: to.x, clientY: to.y })
  await nextTick()
}

beforeEach(() => {
  anchors = []
  fakeCtx.calls.length = 0
  fakeCtx.fillTextLog.length = 0
  fakeCtx.fontLog.length = 0
  vi.stubGlobal('Image', FakeImage)
  getContextMock
    .mockReset()
    .mockImplementation((kind: string) => (kind === '2d' ? fakeCtx2d : null))
  ;(
    HTMLCanvasElement.prototype as unknown as { getContext: unknown }
  ).getContext = getContextMock
  toBlobMock
    .mockReset()
    .mockImplementation((cb: (blob: Blob | null) => void) => cb(new Blob(['out-png'], { type: 'image/png' })))
  ;(
    HTMLCanvasElement.prototype as unknown as { toBlob: unknown }
  ).toBlob = toBlobMock
  createUrl.mockReset().mockImplementation(() => `blob:test-${createUrl.mock.calls.length}`)
  revokeUrl.mockReset()
  Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: createUrl })
  Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: revokeUrl })
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
    this: HTMLAnchorElement,
  ) {
    anchors.push(this)
  })
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('ScreenshotPreview 渲染', () => {
  it('visible 时弹窗渲染画布（自然尺寸 800x600）与完整工具栏', async () => {
    const wrapper = await mountAndSettle()

    expect(wrapper.find('.stub-dialog').exists()).toBe(true)
    const canvas = canvasOf(wrapper)
    expect(canvas.width).toBe(800)
    expect(canvas.height).toBe(600)
    expect(getContextMock).toHaveBeenCalledWith('2d')

    const labels = wrapper.findAll('button').map((b) => b.text())
    expect(labels).toEqual(
      expect.arrayContaining(['画笔', '箭头', '矩形', '文字', '撤销', '清空', '直接保存', '导出 PNG'])
    )
    // 文字工具行内输入区仅在选中文字工具时展开
    expect(wrapper.find('.text-tool-row').exists()).toBe(false)
  })

  it('visible=false 时不渲染弹窗内容', () => {
    const wrapper = mountPreview({ visible: false })
    expect(wrapper.find('.stub-dialog').exists()).toBe(false)
    expect(wrapper.find('canvas').exists()).toBe(false)
  })

  it('Blob 入参：组件自建 object URL，关闭弹窗时回收', async () => {
    const wrapper = await mountAndSettle()
    expect(createUrl).toHaveBeenCalledTimes(1)
    expect(revokeUrl).not.toHaveBeenCalled()

    await wrapper.setProps({ visible: false })
    await nextTick()

    expect(revokeUrl).toHaveBeenCalledWith('blob:test-1')
  })

  it('URL 字符串入参：直接使用、可加载出自然尺寸，关闭不回收外部 URL', async () => {
    const wrapper = await mountAndSettle({ image: 'blob:external-url' })

    expect(createUrl).not.toHaveBeenCalled()
    expect(canvasOf(wrapper).width).toBe(800)

    await wrapper.setProps({ visible: false })
    await nextTick()

    expect(revokeUrl).not.toHaveBeenCalled()
  })
})

describe('ScreenshotPreview 标注工具', () => {
  it('画笔拖拽提交笔划（beginPath/moveTo/lineTo/stroke）', async () => {
    const wrapper = await mountAndSettle()
    mockCanvasRect(wrapper)

    await drawShape(wrapper, '.tool-pen')

    expect(fakeCtx.calls).toContain('beginPath')
    expect(fakeCtx.calls).toContain('moveTo')
    expect(fakeCtx.calls).toContain('lineTo')
    expect(fakeCtx.calls).toContain('stroke')
    expect(commandCount(wrapper)).toBe(1)
  })

  it('箭头拖拽绘制主线条与箭头头部（多次 moveTo/lineTo + stroke）', async () => {
    const wrapper = await mountAndSettle()
    mockCanvasRect(wrapper)

    await drawShape(wrapper, '.tool-arrow')

    expect(fakeCtx.calls.filter((c) => c === 'moveTo').length).toBeGreaterThanOrEqual(3)
    expect(fakeCtx.calls.filter((c) => c === 'lineTo').length).toBeGreaterThanOrEqual(2)
    expect(fakeCtx.calls.filter((c) => c === 'stroke').length).toBeGreaterThanOrEqual(2)
    expect(commandCount(wrapper)).toBe(1)
  })

  it('矩形拖拽绘制边框（strokeRect）', async () => {
    const wrapper = await mountAndSettle()
    mockCanvasRect(wrapper)

    await drawShape(wrapper, '.tool-rect')

    expect(fakeCtx.calls).toContain('strokeRect')
    expect(commandCount(wrapper)).toBe(1)
  })

  it('文字工具：行内输入 + 确认后点击画布放置（fillText 携带文案）', async () => {
    const wrapper = await mountAndSettle()
    mockCanvasRect(wrapper)

    await wrapper.find('.tool-text').trigger('click')
    const input = wrapper.find('input.text-draft')
    expect(input.exists()).toBe(true)
    await input.setValue('设备异常')
    await wrapper.find('.text-confirm').trigger('click')

    fakeCtx.calls.length = 0
    await wrapper.find('canvas').trigger('pointerdown', { clientX: 50, clientY: 30 })
    await nextTick()

    expect(fakeCtx.calls).toContain('fillText')
    expect(fakeCtx.fillTextLog.some((a) => a[0] === '设备异常')).toBe(true)
    expect(commandCount(wrapper)).toBe(1)
  })

  it('未确认文字时点击画布不落字', async () => {
    const wrapper = await mountAndSettle()
    mockCanvasRect(wrapper)

    await wrapper.find('.tool-text').trigger('click')
    fakeCtx.calls.length = 0
    await wrapper.find('canvas').trigger('pointerdown', { clientX: 50, clientY: 30 })
    await nextTick()

    expect(fakeCtx.calls).not.toContain('fillText')
    expect(commandCount(wrapper)).toBe(0)
  })

  it('IME 组合态回车（keydown Enter + isComposing）不确认文字', async () => {
    const wrapper = await mountAndSettle()
    mockCanvasRect(wrapper)

    await wrapper.find('.tool-text').trigger('click')
    const input = wrapper.find('input.text-draft')
    await input.setValue('中文候选词')
    // 输入法候选态回车（isComposing=true）不得把候选词确认为待放置文字
    await input.trigger('keydown', { key: 'Enter', isComposing: true })

    fakeCtx.calls.length = 0
    await wrapper.find('canvas').trigger('pointerdown', { clientX: 50, clientY: 30 })
    await nextTick()

    expect(fakeCtx.calls).not.toContain('fillText')
    expect(commandCount(wrapper)).toBe(0)
  })

  it('非组合态回车（isComposing=false）正常确认文字并落字', async () => {
    const wrapper = await mountAndSettle()
    mockCanvasRect(wrapper)

    await wrapper.find('.tool-text').trigger('click')
    await wrapper.find('input.text-draft').setValue('普通输入')
    await wrapper.find('input.text-draft').trigger('keydown', { key: 'Enter' })

    fakeCtx.calls.length = 0
    await wrapper.find('canvas').trigger('pointerdown', { clientX: 50, clientY: 30 })
    await nextTick()

    expect(fakeCtx.calls).toContain('fillText')
    expect(fakeCtx.fillTextLog.some((a) => a[0] === '普通输入')).toBe(true)
    expect(commandCount(wrapper)).toBe(1)
  })
})

describe('ScreenshotPreview 弹窗重开状态重置', () => {
  it('关闭重开后文字草稿/待放置文字/工具态归零（首击不落旧文案）', async () => {
    const wrapper = await mountAndSettle()
    mockCanvasRect(wrapper)

    await wrapper.find('.tool-text').trigger('click')
    await wrapper.find('input.text-draft').setValue('旧文案')
    await wrapper.find('.text-confirm').trigger('click')

    await wrapper.setProps({ visible: false })
    await wrapper.setProps({ visible: true })
    await nextTick()
    // 画布随弹窗重挂载，重新 mock 显示尺寸
    mockCanvasRect(wrapper)

    // 工具态归零回画笔：文字输入行不再渲染
    expect(wrapper.find('.text-tool-row').exists()).toBe(false)

    fakeCtx.calls.length = 0
    await wrapper.find('canvas').trigger('pointerdown', { clientX: 50, clientY: 30 })
    await nextTick()

    expect(fakeCtx.calls).not.toContain('fillText')
    expect(commandCount(wrapper)).toBe(0)
  })
})

describe('ScreenshotPreview 撤销 / 清空', () => {
  it('撤销：命令栈出栈归零并重绘画布（不再回放笔划）', async () => {
    const wrapper = await mountAndSettle()
    mockCanvasRect(wrapper)

    await drawShape(wrapper, '.tool-pen')
    expect(commandCount(wrapper)).toBe(1)

    fakeCtx.calls.length = 0
    await wrapper.find('.btn-undo').trigger('click')
    await nextTick()

    expect(commandCount(wrapper)).toBe(0)
    expect(fakeCtx.calls).toContain('clearRect')
    expect(fakeCtx.calls).toContain('drawImage')
    expect(fakeCtx.calls).not.toContain('stroke')
  })

  it('清空：命令栈归零并重绘画布', async () => {
    const wrapper = await mountAndSettle()
    mockCanvasRect(wrapper)

    await drawShape(wrapper, '.tool-pen')
    await drawShape(wrapper, '.tool-rect')
    expect(commandCount(wrapper)).toBe(2)

    fakeCtx.calls.length = 0
    await wrapper.find('.btn-clear').trigger('click')
    await nextTick()

    expect(commandCount(wrapper)).toBe(0)
    expect(fakeCtx.calls).toContain('clearRect')
    expect(fakeCtx.calls).not.toContain('stroke')
    expect(fakeCtx.calls).not.toContain('strokeRect')
  })

  it('无标注时撤销/清空按钮置灰，有标注后可用', async () => {
    const wrapper = await mountAndSettle()
    mockCanvasRect(wrapper)

    expect(wrapper.find('.btn-undo').attributes('disabled')).toBeDefined()
    expect(wrapper.find('.btn-clear').attributes('disabled')).toBeDefined()

    await drawShape(wrapper, '.tool-pen')

    expect(wrapper.find('.btn-undo').attributes('disabled')).toBeUndefined()
    expect(wrapper.find('.btn-clear').attributes('disabled')).toBeUndefined()
  })
})

describe('ScreenshotPreview 下载', () => {
  it('导出 PNG：canvas.toBlob(image/png) 产出 → annotated 文件名下载并回收产物 URL', async () => {
    const wrapper = await mountAndSettle()

    await wrapper.find('.btn-export').trigger('click')
    await nextTick()

    expect(toBlobMock).toHaveBeenCalledTimes(1)
    expect(toBlobMock.mock.calls[0][1]).toBe('image/png')
    expect(anchors).toHaveLength(1)
    expect(anchors[0].download).toBe('dev_1_5555_screenshot_annotated.png')
    expect(anchors[0].getAttribute('href')).toBe('blob:test-2')
    expect(revokeUrl).toHaveBeenCalledWith('blob:test-2')
    // 底图 URL 在弹窗打开期不回收
    expect(revokeUrl).not.toHaveBeenCalledWith('blob:test-1')
  })

  it('直接保存：原始截图以原文件名下载，弹窗打开期不回收 URL', async () => {
    const wrapper = await mountAndSettle()

    await wrapper.find('.btn-save').trigger('click')
    await nextTick()

    expect(toBlobMock).not.toHaveBeenCalled()
    expect(anchors).toHaveLength(1)
    expect(anchors[0].download).toBe('dev_1_5555_screenshot.png')
    expect(anchors[0].getAttribute('href')).toBe('blob:test-1')
    expect(revokeUrl).not.toHaveBeenCalled()
  })
})

describe('ScreenshotPreview 参数化与双栈', () => {
  it('文字命令按比例存字号（默认 0.045），渲染换算为像素', async () => {
    const wrapper = await mountAndSettle() // 画布 800x600
    mockCanvasRect(wrapper)

    await wrapper.find('.tool-text').trigger('click')
    await wrapper.find('input.text-draft').setValue('设备异常')
    await wrapper.find('.text-confirm').trigger('click')

    fakeCtx.calls.length = 0
    await wrapper.find('canvas').trigger('pointerdown', { clientX: 50, clientY: 30 })
    await nextTick()

    const cmds = commandList(wrapper)
    expect(cmds[0].fontSize).toBe(0.045)
    // 800×0.045 = 36px → font 设置过 '36px sans-serif'（比例 ×图宽换算落地）
    const fontArg = fakeCtx.fontLog.find((v) => v.endsWith('px sans-serif'))
    expect(fontArg).toBeDefined()
    expect(Number.parseFloat(fontArg as string)).toBeCloseTo(36, 1)
    expect(fakeCtx.calls).toContain('fillText')
  })

  it('画笔命令携带 lineWidth 比例（默认 0.0035）', async () => {
    const wrapper = await mountAndSettle()
    mockCanvasRect(wrapper)

    await drawShape(wrapper, '.tool-pen')

    expect(commandCount(wrapper)).toBe(1)
    expect(commandList(wrapper)[0].lineWidth).toBe(0.0035)
  })

  it('undo 后 redo 恢复命令并重绘', async () => {
    const wrapper = await mountAndSettle()
    mockCanvasRect(wrapper)

    await drawShape(wrapper, '.tool-pen')
    expect(commandCount(wrapper)).toBe(1)

    await wrapper.find('.btn-undo').trigger('click')
    await nextTick()
    expect(commandCount(wrapper)).toBe(0)
    expect(redoCount(wrapper)).toBe(1)

    fakeCtx.calls.length = 0
    await wrapper.find('.btn-redo').trigger('click')
    await nextTick()

    expect(commandCount(wrapper)).toBe(1)
    expect(redoCount(wrapper)).toBe(0)
    expect(fakeCtx.calls).toContain('stroke')
  })

  it('落新命令清空 redo 栈（redo 不可用）', async () => {
    const wrapper = await mountAndSettle()
    mockCanvasRect(wrapper)

    await drawShape(wrapper, '.tool-pen')
    await wrapper.find('.btn-undo').trigger('click')
    await nextTick()
    expect(redoCount(wrapper)).toBe(1)

    await drawShape(wrapper, '.tool-rect')

    expect(commandCount(wrapper)).toBe(1)
    expect(redoCount(wrapper)).toBe(0)
    expect(wrapper.find('.btn-redo').attributes('disabled')).toBeDefined()
  })
})

describe('ScreenshotPreview 参数组与快捷键', () => {
  it('重做按钮：无 redo 时置灰，撤销后可点且恢复命令', async () => {
    const wrapper = await mountAndSettle()
    mockCanvasRect(wrapper)

    expect(wrapper.find('.btn-redo').attributes('disabled')).toBeDefined()

    await drawShape(wrapper, '.tool-pen')
    expect(wrapper.find('.btn-redo').attributes('disabled')).toBeDefined()

    await wrapper.find('.btn-undo').trigger('click')
    await nextTick()
    expect(wrapper.find('.btn-redo').attributes('disabled')).toBeUndefined()

    await wrapper.find('.btn-redo').trigger('click')
    await nextTick()
    expect(commandCount(wrapper)).toBe(1)
  })

  it('滚轮调字号：文字工具下 wheel up 增大 fontSize 比例（钳制上限）', async () => {
    const wrapper = await mountAndSettle()
    mockCanvasRect(wrapper)

    await wrapper.find('.tool-text').trigger('click')
    await wrapper.find('canvas').trigger('wheel', { deltaY: -100 })
    expect(defaultFontRatio(wrapper)).toBeGreaterThan(0.045)

    // 连续上滚：比例单调增大但被上限钳制
    for (let i = 0; i < 50; i += 1) {
      await wrapper.find('canvas').trigger('wheel', { deltaY: -100 })
    }
    expect(defaultFontRatio(wrapper)).toBeLessThanOrEqual(0.15)
  })

  it('滚轮调线宽：画笔工具下 wheel down 减小 lineWidth 比例（钳制下限 0.001）', async () => {
    const wrapper = await mountAndSettle()
    mockCanvasRect(wrapper)

    // 默认画笔工具：下滚缩小线宽
    await wrapper.find('canvas').trigger('wheel', { deltaY: 100 })
    expect(defaultLineRatio(wrapper)).toBeLessThan(0.0035)

    for (let i = 0; i < 100; i += 1) {
      await wrapper.find('canvas').trigger('wheel', { deltaY: 100 })
    }
    expect(defaultLineRatio(wrapper)).toBeCloseTo(0.001, 6)
  })

  it('滑块输入更新默认参数并用于新命令', async () => {
    const wrapper = await mountAndSettle()
    mockCanvasRect(wrapper)

    // 字号滑块（文字工具下显示）→ 落字携带
    await wrapper.find('.tool-text').trigger('click')
    await wrapper.find('.font-size-slider').setValue('0.1')
    await wrapper.find('input.text-draft').setValue('参数缩放')
    await wrapper.find('.text-confirm').trigger('click')
    await wrapper.find('canvas').trigger('pointerdown', { clientX: 50, clientY: 30 })
    await nextTick()
    expect(commandList(wrapper)[0].fontSize).toBe(0.1)

    // 线宽滑块（画笔工具下显示）→ 笔划携带
    await wrapper.find('.tool-pen').trigger('click')
    await wrapper.find('.line-width-slider').setValue('0.01')
    await drawShape(wrapper, '.tool-pen')
    expect(commandList(wrapper)[1].lineWidth).toBe(0.01)
  })

  it('字号滑块仅文字/序号工具显示，线宽滑块仅画笔/箭头/矩形（含后续新工具）显示', async () => {
    const wrapper = await mountAndSettle()
    mockCanvasRect(wrapper)

    // 默认画笔：线宽滑块在、字号滑块不在
    expect(wrapper.find('.line-width-slider').exists()).toBe(true)
    expect(wrapper.find('.font-size-slider').exists()).toBe(false)

    // 文字：字号滑块在、线宽滑块不在
    await wrapper.find('.tool-text').trigger('click')
    expect(wrapper.find('.line-width-slider').exists()).toBe(false)
    expect(wrapper.find('.font-size-slider').exists()).toBe(true)
  })

  it('数字键 1-4 切工具（按 TOOL_KEYS 表序），输入框聚焦时不劫持', async () => {
    const wrapper = await mountAndSettle()
    mockCanvasRect(wrapper)

    window.dispatchEvent(new KeyboardEvent('keydown', { key: '2' }))
    await nextTick()
    expect(wrapper.find('.tool-arrow').classes()).toContain('tool-active')

    window.dispatchEvent(new KeyboardEvent('keydown', { key: '3' }))
    await nextTick()
    expect(wrapper.find('.tool-rect').classes()).toContain('tool-active')
    expect(wrapper.find('.tool-arrow').classes()).not.toContain('tool-active')

    window.dispatchEvent(new KeyboardEvent('keydown', { key: '1' }))
    await nextTick()
    expect(wrapper.find('.tool-pen').classes()).toContain('tool-active')
    expect(wrapper.find('.tool-rect').classes()).not.toContain('tool-active')

    // 4 → TOOL_KEYS[3]（椭圆，按钮 T4 才加入工具栏）：本轮切换后无可见按钮高亮
    window.dispatchEvent(new KeyboardEvent('keydown', { key: '4' }))
    await nextTick()
    expect(wrapper.find('.tool-pen').classes()).not.toContain('tool-active')
    expect(wrapper.find('.tool-arrow').classes()).not.toContain('tool-active')
    expect(wrapper.find('.tool-rect').classes()).not.toContain('tool-active')

    // 6 → TOOL_KEYS[5]（文字）：表序驱动，T4 补钮后 1-8 与工具栏全对齐
    window.dispatchEvent(new KeyboardEvent('keydown', { key: '6' }))
    await nextTick()
    expect(wrapper.find('.tool-text').classes()).toContain('tool-active')

    // 文字输入框聚焦打字时数字键不劫持工具切换
    await wrapper.find('input.text-draft').trigger('keydown', { key: '1' })
    await nextTick()
    expect(wrapper.find('.tool-text').classes()).toContain('tool-active')
  })

  it('Ctrl+Z 撤销 / Ctrl+Shift+Z 重做（window keydown）', async () => {
    const wrapper = await mountAndSettle()
    mockCanvasRect(wrapper)

    await drawShape(wrapper, '.tool-pen')
    expect(commandCount(wrapper)).toBe(1)

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'z', ctrlKey: true }))
    await nextTick()
    expect(commandCount(wrapper)).toBe(0)
    expect(redoCount(wrapper)).toBe(1)

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'z', ctrlKey: true, shiftKey: true }))
    await nextTick()
    expect(commandCount(wrapper)).toBe(1)
    expect(redoCount(wrapper)).toBe(0)
  })
})