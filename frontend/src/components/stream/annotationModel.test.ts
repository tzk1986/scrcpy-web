/**
 * annotationModel 纯函数测试（方案 43 T1）
 * ======================================
 *
 * 覆盖：
 *   - 比例换算：linePx/fontPx 按图宽 ×canvasWidth 得像素
 *   - 下限钳制：超高分辨率下极小比例不消失（线 ≥1px、字号 ≥8px）
 *   - nextNumberIndex：现有 number 命令最大序号 +1（删除中间不重号）
 */
import { describe, expect, it } from 'vitest'
import { FONT_RATIO_DEFAULT, LINE_RATIO_DEFAULT, fontPx, linePx, nextNumberIndex } from './annotationModel'
import type { AnnotationCmd, NumberCmd } from './annotationModel'

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