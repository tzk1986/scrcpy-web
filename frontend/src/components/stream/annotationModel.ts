/**
 * 标注命令模型（方案 43 T1）
 * =========================
 *
 * 纯函数模块：类型 / 常量 / 比例换算 / 序号分配。
 * 字号（fontSize）与线宽（lineWidth）一律以「相对图宽比例」存储（如 0.045 / 0.0035），
 * 渲染时经 linePx/fontPx ×canvasWidth 换算为像素——显示与导出同源，
 * 保证文字/线宽在任意分辨率下观感一致（修复 20px 硬编码根因）。
 */
export type Tool = 'pen' | 'arrow' | 'rect' | 'ellipse' | 'highlight' | 'text' | 'number' | 'mosaic'

export interface Point { x: number; y: number }
export interface PenCmd { kind: 'pen'; points: Point[]; color: string; lineWidth: number }
export interface ArrowCmd { kind: 'arrow'; from: Point; to: Point; color: string; lineWidth: number }
export interface RectCmd { kind: 'rect'; from: Point; to: Point; color: string; lineWidth: number }
export interface EllipseCmd { kind: 'ellipse'; from: Point; to: Point; color: string; lineWidth: number }
export interface HighlightCmd { kind: 'highlight'; points: Point[]; color: string; lineWidth: number }
export interface TextCmd { kind: 'text'; at: Point; text: string; color: string; fontSize: number }
export interface NumberCmd { kind: 'number'; at: Point; index: number; color: string; fontSize: number }
export interface MosaicCmd { kind: 'mosaic'; from: Point; to: Point; lineWidth: number }
export type AnnotationCmd = PenCmd | ArrowCmd | RectCmd | EllipseCmd | HighlightCmd | TextCmd | NumberCmd | MosaicCmd

/** 工具顺序表（数字键 1-N 切工具与工具栏分组按此顺序）。 */
export const TOOL_KEYS: readonly Tool[] = [
  'pen',
  'arrow',
  'rect',
  'ellipse',
  'highlight',
  'text',
  'number',
  'mosaic',
]

export const FONT_RATIO_DEFAULT = 0.045
export const FONT_RATIO_MIN = 0.015
export const FONT_RATIO_MAX = 0.15
export const LINE_RATIO_DEFAULT = 0.0035
export const LINE_RATIO_MIN = 0.001
export const LINE_RATIO_MAX = 0.012

/** 线宽比例 → 像素（×图宽；下限 1px 防超高分辨率下线迹消失；非法输入 → 1）。 */
export function linePx(lineWidth: number, canvasWidth: number): number {
  const px = lineWidth * canvasWidth
  return Number.isFinite(px) ? Math.max(1, px) : 1
}

/** 字号比例 → 像素（×图宽；下限 8px；非法输入 → 8）。 */
export function fontPx(fontSize: number, canvasWidth: number): number {
  const px = fontSize * canvasWidth
  return Number.isFinite(px) ? Math.max(8, px) : 8
}

/** 下一个序号气泡编号：现有 number 命令最大 index +1（无则 1；删除中间不重号）。 */
export function nextNumberIndex(commands: AnnotationCmd[]): number {
  let max = 0
  for (const cmd of commands) {
    if (cmd.kind === 'number' && cmd.index > max) max = cmd.index
  }
  return max + 1
}

/** 包围盒（画布像素坐标系；选中虚线框外扩基准）。 */
export interface BBox { x: number; y: number; width: number; height: number }

/** 点到线段的距离（命中检测用；线段退化为点时就当点距）。 */
function pointSegmentDist(p: Point, a: Point, b: Point): number {
  const abx = b.x - a.x
  const aby = b.y - a.y
  const len2 = abx * abx + aby * aby
  if (len2 === 0) return Math.hypot(p.x - a.x, p.y - a.y)
  let t = ((p.x - a.x) * abx + (p.y - a.y) * aby) / len2
  t = Math.max(0, Math.min(1, t))
  return Math.hypot(p.x - (a.x + t * abx), p.y - (a.y + t * aby))
}

/** 笔划类命中容差（画布像素）：max(8, 线宽像素 ×2)。 */
function hitThreshold(lineWidth: number, canvasWidth: number): number {
  return Math.max(8, linePx(lineWidth, canvasWidth) * 2)
}

/** 点是否在矩形边框带内（内部不算——保证新图形可画在旧图形内）。 */
function pointRectBorderDist(p: Point, left: number, top: number, right: number, bottom: number): number {
  if (p.x > left && p.x < right && p.y > top && p.y < bottom) return Number.POSITIVE_INFINITY
  const dx = Math.max(left - p.x, p.x - right, 0)
  const dy = Math.max(top - p.y, p.y - bottom, 0)
  return Math.hypot(dx, dy)
}

/** 椭圆边框近似距：按归一化半径与方向半径缩放（内部不算）。 */
function pointEllipseBorderDist(p: Point, cx: number, cy: number, rx: number, ry: number): number {
  if (rx < 0.5 || ry < 0.5) return Number.POSITIVE_INFINITY
  const nx = (p.x - cx) / rx
  const ny = (p.y - cy) / ry
  const norm = Math.hypot(nx, ny)
  if (norm < 1) return Number.POSITIVE_INFINITY
  const dirRadius = norm / Math.hypot(nx / rx, ny / ry)
  return (norm - 1) * dirRadius
}

/** 命令包围盒（画布像素；text 宽经 measure 回调测量，y 为基线上下区间）。 */
export function cmdBBox(cmd: AnnotationCmd, canvasWidth: number, measure: (text: string, px: number) => number): BBox {
  switch (cmd.kind) {
    case 'pen':
    case 'highlight': {
      if (cmd.points.length === 0) return { x: 0, y: 0, width: 0, height: 0 }
      let minX = Number.POSITIVE_INFINITY
      let minY = Number.POSITIVE_INFINITY
      let maxX = Number.NEGATIVE_INFINITY
      let maxY = Number.NEGATIVE_INFINITY
      for (const p of cmd.points) {
        minX = Math.min(minX, p.x)
        minY = Math.min(minY, p.y)
        maxX = Math.max(maxX, p.x)
        maxY = Math.max(maxY, p.y)
      }
      return { x: minX, y: minY, width: maxX - minX, height: maxY - minY }
    }
    case 'arrow':
    case 'rect':
    case 'ellipse':
    case 'mosaic': {
      const x = Math.min(cmd.from.x, cmd.to.x)
      const y = Math.min(cmd.from.y, cmd.to.y)
      return { x, y, width: Math.abs(cmd.to.x - cmd.from.x), height: Math.abs(cmd.to.y - cmd.from.y) }
    }
    case 'text': {
      const px = fontPx(cmd.fontSize, canvasWidth)
      return { x: cmd.at.x, y: cmd.at.y - px, width: measure(cmd.text, px), height: px * 1.25 }
    }
    case 'number': {
      const radius = fontPx(cmd.fontSize, canvasWidth) * 0.75
      return { x: cmd.at.x - radius, y: cmd.at.y - radius, width: radius * 2, height: radius * 2 }
    }
  }
}

/**
 * 命中检测（近似命中，供选中/移动/双击编辑定位命令）：
 *   - pen/highlight：点到折线各段距离 ≤ max(8, linePx*2)
 *   - arrow：点到线段距离 ≤ 同阈值
 *   - rect/ellipse：点到边框距离 ≤ 阈值（内部不算——保证新图形可画在旧图形内）
 *   - mosaic：点在包围盒内
 *   - text：文字盒内（宽经 measure）
 *   - number：点距圆心 ≤ radius + 阈值
 * 自顶向下（数组末尾=最上层优先）返回首个命中索引；未命中 null。
 */
export function hitTest(
  commands: AnnotationCmd[],
  point: Point,
  canvasWidth: number,
  measure: (text: string, px: number) => number,
): number | null {
  for (let i = commands.length - 1; i >= 0; i -= 1) {
    const cmd = commands[i]
    let hit = false
    switch (cmd.kind) {
      case 'pen':
      case 'highlight': {
        const threshold = hitThreshold(cmd.lineWidth, canvasWidth)
        if (cmd.points.length === 1) {
          hit = Math.hypot(point.x - cmd.points[0].x, point.y - cmd.points[0].y) <= threshold
        } else {
          for (let s = 0; s < cmd.points.length - 1 && !hit; s += 1) {
            hit = pointSegmentDist(point, cmd.points[s], cmd.points[s + 1]) <= threshold
          }
        }
        break
      }
      case 'arrow':
        hit = pointSegmentDist(point, cmd.from, cmd.to) <= hitThreshold(cmd.lineWidth, canvasWidth)
        break
      case 'rect': {
        const threshold = hitThreshold(cmd.lineWidth, canvasWidth)
        hit =
          pointRectBorderDist(
            point,
            Math.min(cmd.from.x, cmd.to.x),
            Math.min(cmd.from.y, cmd.to.y),
            Math.max(cmd.from.x, cmd.to.x),
            Math.max(cmd.from.y, cmd.to.y),
          ) <= threshold
        break
      }
      case 'ellipse': {
        const threshold = hitThreshold(cmd.lineWidth, canvasWidth)
        const cx = (cmd.from.x + cmd.to.x) / 2
        const cy = (cmd.from.y + cmd.to.y) / 2
        const rx = Math.abs(cmd.to.x - cmd.from.x) / 2
        const ry = Math.abs(cmd.to.y - cmd.from.y) / 2
        hit = pointEllipseBorderDist(point, cx, cy, rx, ry) <= threshold
        break
      }
      case 'mosaic': {
        const box = cmdBBox(cmd, canvasWidth, measure)
        hit =
          point.x >= box.x && point.x <= box.x + box.width && point.y >= box.y && point.y <= box.y + box.height
        break
      }
      case 'text': {
        const box = cmdBBox(cmd, canvasWidth, measure)
        hit =
          point.x >= box.x && point.x <= box.x + box.width && point.y >= box.y && point.y <= box.y + box.height
        break
      }
      case 'number': {
        const radius = fontPx(cmd.fontSize, canvasWidth) * 0.75
        // number 无 lineWidth：阈值取固定下限 8px（hitThreshold 对 0 线宽回落 max(8, 2)）
        hit = Math.hypot(point.x - cmd.at.x, point.y - cmd.at.y) <= radius + hitThreshold(0, canvasWidth)
        break
      }
    }
    if (hit) return i
  }
  return null
}

/** 原地平移命令（dx/dy 画布像素；选中拖动实时调用）。 */
export function translateCmd(cmd: AnnotationCmd, dx: number, dy: number): void {
  switch (cmd.kind) {
    case 'pen':
    case 'highlight':
      for (const p of cmd.points) {
        p.x += dx
        p.y += dy
      }
      break
    case 'arrow':
    case 'rect':
    case 'ellipse':
    case 'mosaic':
      cmd.from.x += dx
      cmd.from.y += dy
      cmd.to.x += dx
      cmd.to.y += dy
      break
    case 'text':
    case 'number':
      cmd.at.x += dx
      cmd.at.y += dy
      break
  }
}