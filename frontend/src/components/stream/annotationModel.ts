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