/**
 * annotationModel 纯函数测试（方案 43 T1 / T4）
 * ============================================
 *
 * 覆盖：
 *   - 比例换算：linePx/fontPx 按图宽 ×canvasWidth 得像素
 *   - 下限钳制：超高分辨率下极小比例不消失（线 ≥1px、字号 ≥8px）
 *   - nextNumberIndex：现有 number 命令最大序号 +1（删除中间不重号）
 *   - drawCmd 渲染（T4）：ellipse/highlight/number/mosaic 全类型（fake ctx 记录式断言）
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  FONT_RATIO_DEFAULT,
  LINE_RATIO_DEFAULT,
  cmdBBox,
  drawCmd,
  fontPx,
  hitTest,
  linePx,
  nextNumberIndex,
  translateCmd,
} from './annotationModel'
import type {
  AnnotationCmd,
  ArrowCmd,
  EllipseCmd,
  HighlightCmd,
  MosaicCmd,
  NumberCmd,
  PenCmd,
  RectCmd,
  TextCmd,
} from './annotationModel'

describe('annotationModel 比例换算', () => {
  it('linePx/fontPx 按图宽换算', () => {
    expect(linePx(LINE_RATIO_DEFAULT, 1080)).toBeCloseTo(3.78, 2)
    expect(fontPx(FONT_RATIO_DEFAULT, 1080)).toBeCloseTo(48.6, 1)
  })
  it('极小比例钳到下限（超高分辨率不消失）', () => {
    expect(linePx(0.0001, 4000)).toBe(1)
    expect(fontPx(0.001, 4000)).toBe(8)
  })
  it('nextNumberIndex 取现有最大序号 +1（删除中间不重号）', () => {
    const cmds: AnnotationCmd[] = [
      { kind: 'number', at: { x: 0, y: 0 }, index: 3, color: '#f00', fontSize: 0.05 } satisfies NumberCmd,
    ]
    expect(nextNumberIndex(cmds)).toBe(4)
    expect(nextNumberIndex([])).toBe(1)
  })
})

describe('hitTest / cmdBBox / translateCmd', () => {
  const measure80 = () => 80

  it('矩形边框命中、内部不命中', () => {
    const rect: RectCmd = {
      kind: 'rect',
      from: { x: 100, y: 100 },
      to: { x: 300, y: 200 },
      color: '#f00',
      lineWidth: 0.0035,
    }
    expect(hitTest([rect], { x: 200, y: 100 }, 800, measure80)).toBe(0) // 上边框上
    expect(hitTest([rect], { x: 200, y: 150 }, 800, measure80)).toBeNull() // 内部
  })

  it('文字包围盒命中（宽度经 measure）', () => {
    const text: TextCmd = { kind: 'text', at: { x: 100, y: 200 }, text: '告警', color: '#f00', fontSize: 0.05 }
    // 800*0.05=40px；measure 返回 80 → 盒 x∈[100,180], y∈[160,210]
    expect(hitTest([text], { x: 140, y: 190 }, 800, measure80)).toBe(0)
    expect(hitTest([text], { x: 300, y: 190 }, 800, measure80)).toBeNull()
  })

  it('自顶向下：重叠时返回最上层（数组末尾）', () => {
    const bottom: RectCmd = {
      kind: 'rect',
      from: { x: 100, y: 100 },
      to: { x: 300, y: 200 },
      color: '#f00',
      lineWidth: 0.0035,
    }
    const top: RectCmd = { ...bottom, color: '#0f0' }
    expect(hitTest([bottom, top], { x: 200, y: 100 }, 800, measure80)).toBe(1)
    expect(hitTest([top, bottom], { x: 200, y: 100 }, 800, measure80)).toBe(1)
  })

  it('translateCmd 平移各类型（原地修改）', () => {
    const pen: PenCmd = {
      kind: 'pen',
      points: [{ x: 0, y: 0 }, { x: 10, y: 10 }],
      color: '#f00',
      lineWidth: 0.0035,
    }
    translateCmd(pen, 5, -3)
    expect(pen.points).toEqual([{ x: 5, y: -3 }, { x: 15, y: 7 }])

    const rect: RectCmd = {
      kind: 'rect',
      from: { x: 1, y: 2 },
      to: { x: 3, y: 4 },
      color: '#f00',
      lineWidth: 0.0035,
    }
    translateCmd(rect, 5, -3)
    expect(rect.from).toEqual({ x: 6, y: -1 })
    expect(rect.to).toEqual({ x: 8, y: 1 })

    const text: TextCmd = { kind: 'text', at: { x: 10, y: 20 }, text: 'x', color: '#f00', fontSize: 0.05 }
    translateCmd(text, 5, -3)
    expect(text.at).toEqual({ x: 15, y: 17 })

    const mosaic: MosaicCmd = { kind: 'mosaic', from: { x: 0, y: 0 }, to: { x: 10, y: 10 }, lineWidth: 0.0035 }
    translateCmd(mosaic, 5, -3)
    expect(mosaic.from).toEqual({ x: 5, y: -3 })
    expect(mosaic.to).toEqual({ x: 15, y: 7 })
  })

  it('箭头按线段距命中（阈值 max(8, linePx*2)）', () => {
    const arrow: ArrowCmd = {
      kind: 'arrow',
      from: { x: 100, y: 100 },
      to: { x: 300, y: 100 },
      color: '#f00',
      lineWidth: 0.0035,
    }
    expect(hitTest([arrow], { x: 200, y: 104 }, 800, measure80)).toBe(0) // 距线段 4 ≤ 8
    expect(hitTest([arrow], { x: 200, y: 120 }, 800, measure80)).toBeNull() // 距线段 20 > 8
  })

  it('马赛克盒内命中、盒外不命中', () => {
    const mosaic: MosaicCmd = { kind: 'mosaic', from: { x: 100, y: 100 }, to: { x: 200, y: 200 }, lineWidth: 0.0035 }
    expect(hitTest([mosaic], { x: 150, y: 150 }, 800, measure80)).toBe(0)
    expect(hitTest([mosaic], { x: 250, y: 150 }, 800, measure80)).toBeNull()
  })

  it('序号按圆命中（dist ≤ radius + 阈值）', () => {
    const num: NumberCmd = { kind: 'number', at: { x: 100, y: 100 }, index: 1, color: '#f00', fontSize: 0.05 }
    // fontPx = 40 → radius 30；点距 25 ≤ 30+8 命中，100 > 38 不命中
    expect(hitTest([num], { x: 125, y: 100 }, 800, measure80)).toBe(0)
    expect(hitTest([num], { x: 200, y: 100 }, 800, measure80)).toBeNull()
  })

  it('椭圆边框命中、内部不命中', () => {
    const ellipse: AnnotationCmd = {
      kind: 'ellipse',
      from: { x: 100, y: 100 },
      to: { x: 300, y: 200 },
      color: '#f00',
      lineWidth: 0.0035,
    }
    expect(hitTest([ellipse], { x: 200, y: 100 }, 800, measure80)).toBe(0) // 顶点
    expect(hitTest([ellipse], { x: 300, y: 150 }, 800, measure80)).toBe(0) // 右侧点
    expect(hitTest([ellipse], { x: 200, y: 150 }, 800, measure80)).toBeNull() // 内部
    expect(hitTest([ellipse], { x: 420, y: 150 }, 800, measure80)).toBeNull() // 远外
  })

  it('cmdBBox：文字盒 y ∈ [at.y - fontPx, at.y + fontPx*0.25]、宽经 measure；序号圆盒', () => {
    const text: TextCmd = { kind: 'text', at: { x: 100, y: 200 }, text: '告警', color: '#f00', fontSize: 0.05 }
    expect(cmdBBox(text, 800, measure80)).toEqual({ x: 100, y: 160, width: 80, height: 50 })

    const num: NumberCmd = { kind: 'number', at: { x: 100, y: 100 }, index: 1, color: '#f00', fontSize: 0.05 }
    expect(cmdBBox(num, 800, measure80)).toEqual({ x: 70, y: 70, width: 60, height: 60 })
  })
})

/** 记录全部绘制调用的 fake 2d context（drawCmd 渲染路径断言用；含 T4 椭圆/填充/虚线/透明度面）。 */
function createRecordingCtx() {
  const calls: string[] = []
  const fillTextLog: unknown[][] = []
  const fontLog: string[] = []
  const alphaLog: number[] = []
  const drawImageLog: unknown[][] = []
  const record = (name: string) => () => {
    calls.push(name)
  }
  return {
    calls,
    fillTextLog,
    fontLog,
    alphaLog,
    drawImageLog,
    clearRect: record('clearRect'),
    drawImage: (...args: unknown[]) => {
      calls.push('drawImage')
      drawImageLog.push(args)
    },
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
    set strokeStyle(_v: string) {},
    set fillStyle(_v: string) {},
    set lineWidth(_v: number) {},
    set lineCap(_v: CanvasLineCap) {},
    set font(v: string) {
      fontLog.push(v)
    },
    set globalAlpha(v: number) {
      alphaLog.push(v)
    },
    set imageSmoothingEnabled(_v: boolean) {},
    set textAlign(_v: CanvasTextAlign) {},
    set textBaseline(_v: CanvasTextBaseline) {},
  }
}

describe('drawCmd 渲染路径（T4 新工具）', () => {
  const ctx = createRecordingCtx()
  const asCtx = ctx as unknown as CanvasRenderingContext2D
  /** drawCmd 经 document.createElement('canvas') 创建的离屏画布（尺寸断言用）。 */
  const offscreens: HTMLCanvasElement[] = []

  beforeEach(() => {
    ctx.calls.length = 0
    ctx.fillTextLog.length = 0
    ctx.fontLog.length = 0
    ctx.alphaLog.length = 0
    ctx.drawImageLog.length = 0
    offscreens.length = 0
    // happy-dom 无真实 2d 实现：getContext 桩把所有画布（含离屏 canvas）指向同一记录器
    ;(HTMLCanvasElement.prototype as unknown as { getContext: unknown }).getContext = vi.fn(
      function (this: HTMLCanvasElement, kind: string) {
        if (kind === '2d') offscreens.push(this)
        return kind === '2d' ? asCtx : null
      },
    )
  })

  it('ellipse：中心式 ctx.ellipse（rx/ry 半宽高）+ stroke', () => {
    const ellipse: EllipseCmd = {
      kind: 'ellipse',
      from: { x: 0, y: 0 },
      to: { x: 200, y: 100 },
      color: '#f00',
      lineWidth: 0.0035,
    }
    drawCmd(asCtx, ellipse, 800, null)
    expect(ctx.calls).toContain('ellipse')
    expect(ctx.calls).toContain('stroke')
  })

  it('highlight：save → globalAlpha 0.35 → stroke → restore', () => {
    const highlight: HighlightCmd = {
      kind: 'highlight',
      points: [{ x: 10, y: 10 }, { x: 60, y: 30 }],
      color: '#ffcc00',
      lineWidth: 0.0035,
    }
    drawCmd(asCtx, highlight, 800, null)
    expect(ctx.calls).toContain('save')
    expect(ctx.calls).toContain('restore')
    expect(ctx.alphaLog).toContain(0.35)
    expect(ctx.calls).toContain('stroke')
    expect(ctx.calls.indexOf('save')).toBeLessThan(ctx.calls.indexOf('stroke'))
    expect(ctx.calls.indexOf('restore')).toBeGreaterThan(ctx.calls.indexOf('stroke'))
  })

  it('number：实心圆（arc + fill）+ 白色序号（bold fillText(index)）', () => {
    const number: NumberCmd = { kind: 'number', at: { x: 100, y: 100 }, index: 2, color: '#f00', fontSize: 0.045 }
    drawCmd(asCtx, number, 800, null)
    expect(ctx.calls).toContain('arc')
    expect(ctx.calls).toContain('fill')
    expect(ctx.calls.indexOf('arc')).toBeLessThan(ctx.calls.indexOf('fill'))
    expect(ctx.fillTextLog.some((a) => a[0] === '2')).toBe(true)
    expect(ctx.fontLog.some((v) => v.startsWith('bold '))).toBe(true)
  })

  it('mosaic 无底图静默返回；宽高 <1 不画；有底图经离屏 canvas 缩-放（drawImage ≥ 2）', () => {
    const mosaic: MosaicCmd = {
      kind: 'mosaic',
      from: { x: 10, y: 20 },
      to: { x: 110, y: 80 },
      lineWidth: 0.0035,
    }
    // 无底图：静默不画（不得抛错）
    expect(() => drawCmd(asCtx, mosaic, 800, null)).not.toThrow()
    expect(ctx.calls).toHaveLength(0)
    expect(offscreens).toHaveLength(0)

    // 宽高 <1：静默返回（不建离屏 canvas）
    drawCmd(asCtx, { ...mosaic, to: { x: 10.5, y: 80 } }, 800, {} as HTMLImageElement)
    expect(ctx.calls).toHaveLength(0)
    expect(offscreens).toHaveLength(0)

    // 有底图：离屏缩小 1 次 + 贴回 1 次
    drawCmd(asCtx, mosaic, 800, {} as HTMLImageElement)
    expect(ctx.drawImageLog.length).toBeGreaterThanOrEqual(2)
    expect(ctx.calls.filter((c) => c === 'drawImage').length).toBeGreaterThanOrEqual(2)
    expect(offscreens).toHaveLength(1)
  })

  it('mosaic 块尺寸随 lineWidth：块大 → 离屏尺寸小', () => {
    const base = {} as HTMLImageElement
    const thin: MosaicCmd = { kind: 'mosaic', from: { x: 0, y: 0 }, to: { x: 200, y: 200 }, lineWidth: 0.001 }
    const thick: MosaicCmd = { kind: 'mosaic', from: { x: 0, y: 0 }, to: { x: 200, y: 200 }, lineWidth: 0.012 }
    drawCmd(asCtx, thin, 800, base) // block = max(4, 0.8) = 4
    const thinWidth = offscreens[0].width
    ctx.calls.length = 0
    offscreens.length = 0
    drawCmd(asCtx, thick, 800, base) // block = 9.6
    const thickWidth = offscreens[0].width
    expect(thickWidth).toBeLessThan(thinWidth)
  })
})