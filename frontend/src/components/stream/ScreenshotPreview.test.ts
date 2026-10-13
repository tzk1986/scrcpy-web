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
 *   - 新工具（T4）：椭圆（ctx.ellipse）/ 荧光笔（alpha 0.35）/ 序号（点击落字递增编号）/
 *     马赛克（拖拽提交无 color、重绘离屏缩-放 drawImage≥2、预览虚线框、两轴 ≥2px 守卫）
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
  const alphaLog: number[] = []
  const dashLog: unknown[][] = []
  const smoothLog: boolean[] = []
  const record = (name: string) => () => {
    calls.push(name)
  }
  return {
    calls,
    fillTextLog,
    fontLog,
    alphaLog,
    dashLog,
    smoothLog,
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
    setLineDash: (dash: unknown[]) => {
      calls.push('setLineDash')
      dashLog.push(dash)
    },
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
    set globalAlpha(v: number) {
      alphaLog.push(v)
    },
    set imageSmoothingEnabled(v: boolean) {
      smoothLog.push(v)
    },
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

/** 读取组件暴露的选中索引（命中选中/移动/删除断言用）。 */
function selectedIdx(wrapper: VueWrapper): number | null {
  return (wrapper.vm as unknown as { getSelectedIndex: () => number | null }).getSelectedIndex()
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
  fakeCtx.alphaLog.length = 0
  fakeCtx.dashLog.length = 0
  fakeCtx.smoothLog.length = 0
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
      expect.arrayContaining([
        '画笔', '箭头', '矩形', '椭圆', '荧光笔', '文字', '序号', '马赛克',
        '撤销', '清空', '直接保存', '导出 PNG',
      ])
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
    // 命中检测语义下起点压在上层命令上会进入移动而非新绘：第二笔起点避开画笔轨迹
    await wrapper.find('.tool-rect').trigger('click')
    const canvas = wrapper.find('canvas')
    await canvas.trigger('pointerdown', { clientX: 50, clientY: 10 })
    await canvas.trigger('pointermove', { clientX: 80, clientY: 60 })
    await canvas.trigger('pointerup', { clientX: 80, clientY: 60 })
    await nextTick()
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

    // 4 → TOOL_KEYS[3]（椭圆）：T4 后椭圆按钮已入工具栏并高亮
    window.dispatchEvent(new KeyboardEvent('keydown', { key: '4' }))
    await nextTick()
    expect(wrapper.find('.tool-ellipse').classes()).toContain('tool-active')
    expect(wrapper.find('.tool-pen').classes()).not.toContain('tool-active')
    expect(wrapper.find('.tool-arrow').classes()).not.toContain('tool-active')
    expect(wrapper.find('.tool-rect').classes()).not.toContain('tool-active')

    // 6 → TOOL_KEYS[5]（文字）：表序驱动，1-8 与工具栏全对齐
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

describe('ScreenshotPreview 选中与编辑', () => {
  it('文字 ghost：悬停半透明预览（save/restore + alpha 0.5），点击才落字，pointerleave 清预览', async () => {
    const wrapper = await mountAndSettle()
    mockCanvasRect(wrapper)

    await wrapper.find('.tool-text').trigger('click')
    await wrapper.find('input.text-draft').setValue('待放置文案')
    await wrapper.find('.text-confirm').trigger('click')

    const canvas = wrapper.find('canvas')
    fakeCtx.calls.length = 0
    fakeCtx.fillTextLog.length = 0
    fakeCtx.alphaLog.length = 0
    await canvas.trigger('pointermove', { clientX: 50, clientY: 30 })
    await nextTick()

    expect(fakeCtx.calls).toContain('save')
    expect(fakeCtx.calls).toContain('restore')
    expect(fakeCtx.alphaLog).toContain(0.5)
    expect(fakeCtx.fillTextLog.some((a) => a[0] === '待放置文案')).toBe(true)
    expect(commandCount(wrapper)).toBe(0) // ghost 未落字

    // 移出画布：hoverPoint 清空，重绘不再有 ghost（此时命令栈为空，无 fillText 即无 ghost）
    fakeCtx.calls.length = 0
    fakeCtx.fillTextLog.length = 0
    await canvas.trigger('pointerleave')
    await nextTick()
    expect(fakeCtx.fillTextLog.length).toBe(0)

    // 点击才落字（命令数 +1）
    await canvas.trigger('pointerdown', { clientX: 50, clientY: 30 })
    await nextTick()
    expect(commandCount(wrapper)).toBe(1)
    expect(fakeCtx.fillTextLog.some((a) => a[0] === '待放置文案')).toBe(true)
  })

  it('命中选中：点击矩形边框 → getSelectedIndex()===0，重绘含虚线选中框（无新增命令）', async () => {
    const wrapper = await mountAndSettle()
    mockCanvasRect(wrapper)

    await drawShape(wrapper, '.tool-rect') // 画布 from(20,20) to(120,80)
    expect(commandCount(wrapper)).toBe(1)

    fakeCtx.calls.length = 0
    const canvas = wrapper.find('canvas')
    await canvas.trigger('pointerdown', { clientX: 35, clientY: 10 }) // 上边框画布 (70,20)
    await nextTick()

    expect(selectedIdx(wrapper)).toBe(0)
    expect(commandCount(wrapper)).toBe(1)
    expect(fakeCtx.calls).toContain('setLineDash')
    expect(fakeCtx.calls).toContain('strokeRect')
    await canvas.trigger('pointerup')
  })

  it('拖拽移动：命中选中后拖动实时平移命令坐标（client Δ ×2 换算画布像素）', async () => {
    const wrapper = await mountAndSettle()
    mockCanvasRect(wrapper)

    await drawShape(wrapper, '.tool-rect') // 画布 from(20,20) to(120,80)

    const canvas = wrapper.find('canvas')
    // 上边框中点画布 (70,20)（client 35,10）按下选中并进入移动
    await canvas.trigger('pointerdown', { clientX: 35, clientY: 10 })
    // 拖到 client (55,20) → 画布 (110,40)，Δ(+40,+20)
    await canvas.trigger('pointermove', { clientX: 55, clientY: 20 })
    await canvas.trigger('pointerup', { clientX: 55, clientY: 20 })
    await nextTick()

    expect(commandCount(wrapper)).toBe(1)
    const cmd = commandList(wrapper)[0] as unknown as {
      from: { x: number; y: number }
      to: { x: number; y: number }
    }
    expect(cmd.from.x).toBeCloseTo(60, 6)
    expect(cmd.from.y).toBeCloseTo(40, 6)
    expect(cmd.to.x).toBeCloseTo(160, 6)
    expect(cmd.to.y).toBeCloseTo(100, 6)
  })

  it('Delete/Backspace 删除选中命令（redo 栈清空）；Esc 取消选中（命令保留）', async () => {
    const wrapper = await mountAndSettle()
    mockCanvasRect(wrapper)

    await drawShape(wrapper, '.tool-rect')
    const canvas = wrapper.find('canvas')

    // 点边框选中 → Esc 取消选中（命令保留）
    await canvas.trigger('pointerdown', { clientX: 35, clientY: 10 })
    await canvas.trigger('pointerup')
    expect(selectedIdx(wrapper)).toBe(0)
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await nextTick()
    expect(selectedIdx(wrapper)).toBeNull()
    expect(commandCount(wrapper)).toBe(1)

    // 再选中 → Delete 删除（选中清空、redo 栈清空）
    await canvas.trigger('pointerdown', { clientX: 35, clientY: 10 })
    await canvas.trigger('pointerup')
    expect(selectedIdx(wrapper)).toBe(0)
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Delete' }))
    await nextTick()
    expect(commandCount(wrapper)).toBe(0)
    expect(selectedIdx(wrapper)).toBeNull()
    expect(redoCount(wrapper)).toBe(0)

    // Backspace 同样删除选中
    await drawShape(wrapper, '.tool-rect')
    await canvas.trigger('pointerdown', { clientX: 35, clientY: 10 })
    await canvas.trigger('pointerup')
    expect(selectedIdx(wrapper)).toBe(0)
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Backspace' }))
    await nextTick()
    expect(commandCount(wrapper)).toBe(0)
  })

  it('双击文字进入编辑：确认后原地改文案（命令数不变、字号保留）', async () => {
    const wrapper = await mountAndSettle()
    mockCanvasRect(wrapper)

    // 放置文字：画布 (100,60)，fontSize 0.045 → 36px
    await wrapper.find('.tool-text').trigger('click')
    await wrapper.find('input.text-draft').setValue('告警')
    await wrapper.find('.text-confirm').trigger('click')
    const canvas = wrapper.find('canvas')
    await canvas.trigger('pointerdown', { clientX: 50, clientY: 30 })
    await nextTick()
    expect(commandCount(wrapper)).toBe(1)

    // 双击文字盒内（盒 x∈[100,124]（measure=length×12）、y∈[24,69]）→ 输入框预填原文案
    await canvas.trigger('dblclick', { clientX: 55, clientY: 32 }) // 画布 (110,64)
    await nextTick()
    const input = wrapper.find('input.text-draft')
    expect((input.element as HTMLInputElement).value).toBe('告警')

    await input.setValue('严重告警')
    await wrapper.find('.text-confirm').trigger('click')
    await nextTick()

    const cmds = commandList(wrapper)
    expect(cmds).toHaveLength(1) // 原地更新，无新增命令
    expect((cmds[0] as unknown as { text: string }).text).toBe('严重告警')
    expect(cmds[0].fontSize).toBe(0.045)
  })

  it('笔划类命中用点距：画笔线中点附近命中，远离不命中', async () => {
    const wrapper = await mountAndSettle()
    mockCanvasRect(wrapper)

    await drawShape(wrapper, '.tool-pen') // 画布 (20,20) → (120,80)
    const canvas = wrapper.find('canvas')

    // 线段中点画布 (70,50)（client 35,25）→ 命中索引 0
    await canvas.trigger('pointerdown', { clientX: 35, clientY: 25 })
    await nextTick()
    expect(selectedIdx(wrapper)).toBe(0)
    await canvas.trigger('pointerup')

    // 远离线段（画布 (20,120)，client 10,60）→ 不命中、选中清空
    await canvas.trigger('pointerdown', { clientX: 10, clientY: 60 })
    await nextTick()
    expect(selectedIdx(wrapper)).toBeNull()
    await canvas.trigger('pointerup')
    expect(commandCount(wrapper)).toBe(1)
  })
})

describe('ScreenshotPreview 新工具（T4）', () => {
  it('工具栏含椭圆/荧光笔/序号/马赛克按钮（表序位置），点击后选中高亮', async () => {
    const wrapper = await mountAndSettle()
    mockCanvasRect(wrapper)

    const cases = [
      ['.tool-ellipse', '椭圆'],
      ['.tool-highlight', '荧光笔'],
      ['.tool-number', '序号'],
      ['.tool-mosaic', '马赛克'],
    ] as const
    for (const [cls, label] of cases) {
      const btn = wrapper.find(cls)
      expect(btn.exists()).toBe(true)
      expect(btn.text()).toBe(label)
      await btn.trigger('click')
      expect(wrapper.find(cls).classes()).toContain('tool-active')
    }
    // 最后点击马赛克：其余工具失活
    expect(wrapper.find('.tool-pen').classes()).not.toContain('tool-active')
    expect(wrapper.find('.tool-text').classes()).not.toContain('tool-active')
  })

  it('数字键 4/5/7/8 切新工具（椭圆/荧光笔/序号/马赛克，按 TOOL_KEYS 表序）', async () => {
    const wrapper = await mountAndSettle()
    mockCanvasRect(wrapper)

    const cases = [
      ['4', '.tool-ellipse'],
      ['5', '.tool-highlight'],
      ['7', '.tool-number'],
      ['8', '.tool-mosaic'],
    ] as const
    for (const [key, cls] of cases) {
      window.dispatchEvent(new KeyboardEvent('keydown', { key }))
      await nextTick()
      expect(wrapper.find(cls).classes()).toContain('tool-active')
    }
  })

  it('椭圆拖拽提交：ctx.ellipse 绘制轮廓 + 命令入栈（携带线宽比例）', async () => {
    const wrapper = await mountAndSettle()
    mockCanvasRect(wrapper)

    await drawShape(wrapper, '.tool-ellipse')

    expect(fakeCtx.calls).toContain('ellipse') // pointerup 重绘（提交回放）已记 ellipse
    expect(commandCount(wrapper)).toBe(1)
    expect(commandList(wrapper)[0].kind).toBe('ellipse')
    expect(commandList(wrapper)[0].lineWidth).toBe(0.0035)
  })

  it('荧光笔提交：重绘含 save/restore 与 globalAlpha 0.35（半透明语义）', async () => {
    const wrapper = await mountAndSettle()
    mockCanvasRect(wrapper)

    await drawShape(wrapper, '.tool-highlight')
    expect(commandCount(wrapper)).toBe(1)
    expect(commandList(wrapper)[0].kind).toBe('highlight')

    // 清记录后触发一次回放重绘（无拖拽的 hover move），验证提交命令的渲染语义
    fakeCtx.calls.length = 0
    fakeCtx.alphaLog.length = 0
    await wrapper.find('canvas').trigger('pointermove', { clientX: 30, clientY: 30 })
    await nextTick()

    expect(fakeCtx.calls).toContain('save')
    expect(fakeCtx.calls).toContain('restore')
    expect(fakeCtx.calls).toContain('stroke')
    expect(fakeCtx.alphaLog).toContain(0.35)
  })

  it('序号工具：点击立即落字，两次点击序号依次 1/2（携带字号比例与当前色）', async () => {
    const wrapper = await mountAndSettle()
    mockCanvasRect(wrapper)

    await wrapper.find('.tool-number').trigger('click')
    const canvas = wrapper.find('canvas')
    await canvas.trigger('pointerdown', { clientX: 50, clientY: 30 }) // 画布 (100,60)
    await nextTick()
    await canvas.trigger('pointerdown', { clientX: 100, clientY: 100 }) // 画布 (200,200)
    await nextTick()

    const cmds = commandList(wrapper)
    expect(cmds).toHaveLength(2)
    expect((cmds[0] as unknown as { index: number }).index).toBe(1)
    expect((cmds[1] as unknown as { index: number }).index).toBe(2)
    expect(cmds[0].fontSize).toBe(0.045)
    expect((cmds[0] as unknown as { color: string }).color).toBe('#ff3b30')
    // 实心圆 + 序号填充字
    expect(fakeCtx.calls).toContain('arc')
    expect(fakeCtx.calls).toContain('fill')
    expect(fakeCtx.fillTextLog.some((a) => a[0] === '1')).toBe(true)
    expect(fakeCtx.fillTextLog.some((a) => a[0] === '2')).toBe(true)
  })

  it('马赛克拖拽提交：命令无 color；提交后重绘离屏缩-放（drawImage ≥ 2、关闭平滑）', async () => {
    const wrapper = await mountAndSettle()
    mockCanvasRect(wrapper)

    await drawShape(wrapper, '.tool-mosaic')

    expect(commandCount(wrapper)).toBe(1)
    const cmd = commandList(wrapper)[0] as unknown as { kind: string; lineWidth: number; color?: string }
    expect(cmd.kind).toBe('mosaic')
    expect(cmd.lineWidth).toBe(0.0035)
    expect(cmd.color).toBeUndefined()
    // pointerup 重绘：底图 1 次 + 离屏缩小 1 次 + 贴回 1 次
    expect(fakeCtx.calls.filter((c) => c === 'drawImage').length).toBeGreaterThanOrEqual(2)
    expect(fakeCtx.smoothLog).toContain(false)
  })

  it('马赛克拖拽预览：虚线矩形轮廓（setLineDash[4,4]），未松手不画真马赛克', async () => {
    const wrapper = await mountAndSettle()
    mockCanvasRect(wrapper)

    await wrapper.find('.tool-mosaic').trigger('click')
    const canvas = wrapper.find('canvas')
    await canvas.trigger('pointerdown', { clientX: 10, clientY: 10 })
    fakeCtx.calls.length = 0
    fakeCtx.dashLog.length = 0
    await canvas.trigger('pointermove', { clientX: 60, clientY: 40 })
    await nextTick()

    expect(fakeCtx.calls).toContain('strokeRect')
    expect(fakeCtx.dashLog.some((d) => String(d) === '4,4')).toBe(true)
    // 预览期间只有底图 1 次 drawImage（拖拽未提交 → 无离屏缩-放）
    expect(fakeCtx.calls.filter((c) => c === 'drawImage').length).toBe(1)
    await canvas.trigger('pointerup', { clientX: 60, clientY: 40 })
  })

  it('提交守卫：椭圆微小拖拽（任一轴均 <2px）不提交；马赛克单轴 <2px 不提交', async () => {
    const wrapper = await mountAndSettle()
    mockCanvasRect(wrapper)
    const canvas = wrapper.find('canvas')

    await wrapper.find('.tool-ellipse').trigger('click')
    await canvas.trigger('pointerdown', { clientX: 10, clientY: 10 })
    await canvas.trigger('pointermove', { clientX: 10.5, clientY: 10 }) // 画布 Δ(1,0)
    await canvas.trigger('pointerup', { clientX: 10.5, clientY: 10 })
    await nextTick()
    expect(commandCount(wrapper)).toBe(0)

    await wrapper.find('.tool-mosaic').trigger('click')
    await canvas.trigger('pointerdown', { clientX: 10, clientY: 10 })
    await canvas.trigger('pointermove', { clientX: 70, clientY: 10.5 }) // 画布 Δ(120,1)
    await canvas.trigger('pointerup', { clientX: 70, clientY: 10.5 })
    await nextTick()
    expect(commandCount(wrapper)).toBe(0)
  })

  it('参数行显隐：新工具显示线宽滑块；序号显示字号滑块；马赛克隐藏色板与自定义色', async () => {
    const wrapper = await mountAndSettle()
    mockCanvasRect(wrapper)

    await wrapper.find('.tool-ellipse').trigger('click')
    expect(wrapper.find('.line-width-slider').exists()).toBe(true)
    expect(wrapper.find('.font-size-slider').exists()).toBe(false)

    await wrapper.find('.tool-highlight').trigger('click')
    expect(wrapper.find('.line-width-slider').exists()).toBe(true)

    await wrapper.find('.tool-number').trigger('click')
    expect(wrapper.find('.font-size-slider').exists()).toBe(true)
    expect(wrapper.find('.line-width-slider').exists()).toBe(false)

    await wrapper.find('.tool-mosaic').trigger('click')
    expect(wrapper.find('.line-width-slider').exists()).toBe(true)
    expect(wrapper.find('.color-swatch').exists()).toBe(false)
    expect(wrapper.find('.color-input').exists()).toBe(false)
  })
})