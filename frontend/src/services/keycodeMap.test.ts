/**
 * keycodeMap 测试（方案 54 T2）
 * 覆盖：全表抽查（Android KeyEvent 定值）、metaState 合成、
 *       isLetterCode 判定、splitTextChunks 300 上限分段与代理对保护
 */
import { describe, expect, it } from 'vitest'
import {
  KEYCODE_BY_CODE,
  MetaState,
  isLetterCode,
  keycodeForCode,
  metaStateFromEvent,
} from './keycodeMap'
import { INJECT_TEXT_MAX_LENGTH, splitTextChunks } from './inputController'

describe('keycodeForCode 全表抽查（Android KeyEvent 定值）', () => {
  it.each([
    ['KeyA', 29],
    ['KeyZ', 54],
    ['Digit0', 7],
    ['Digit9', 16],
    ['Space', 62],
    ['Enter', 66],
    ['Backspace', 67],
    ['Quote', 75],
    ['F1', 131],
    ['F12', 142],
    ['Escape', 111],
    ['ArrowUp', 19],
    ['Home', 122],       // MOVE_HOME：浏览器 Home ≠ Android HOME(3)
    ['End', 123],
    ['ShiftLeft', 59],
    ['ControlRight', 114],
    ['MetaLeft', 117],
    ['Numpad5', 149],
    ['NumpadEnter', 160],
  ])('%s → %i', (code, expected) => {
    expect(keycodeForCode(code)).toBe(expected)
    expect(KEYCODE_BY_CODE[code]).toBe(expected)
  })

  it('表外键返回 null', () => {
    expect(keycodeForCode('AudioPlayPause')).toBeNull()
    expect(keycodeForCode('')).toBeNull()
  })
})

describe('metaStateFromEvent', () => {
  const base = { shiftKey: false, ctrlKey: false, altKey: false, metaKey: false }

  it('无修饰 → 0', () => {
    expect(metaStateFromEvent(base)).toBe(0)
  })

  it('单修饰位正确', () => {
    expect(metaStateFromEvent({ ...base, shiftKey: true })).toBe(MetaState.SHIFT_ON)
    expect(metaStateFromEvent({ ...base, altKey: true })).toBe(MetaState.ALT_ON)
    expect(metaStateFromEvent({ ...base, ctrlKey: true })).toBe(MetaState.CTRL_ON)
    expect(metaStateFromEvent({ ...base, metaKey: true })).toBe(MetaState.META_ON)
  })

  it('组合掩码按位或（ctrl+shift → 0x11）', () => {
    expect(
      metaStateFromEvent({ ...base, ctrlKey: true, shiftKey: true }),
    ).toBe(MetaState.CTRL_ON | MetaState.SHIFT_ON)
  })

  it('全修饰 → 0x10013', () => {
    expect(
      metaStateFromEvent({ shiftKey: true, ctrlKey: true, altKey: true, metaKey: true }),
    ).toBe(0x10013)
  })
})

describe('isLetterCode', () => {
  it('字母键 true，其余 false', () => {
    expect(isLetterCode('KeyQ')).toBe(true)
    expect(isLetterCode('Digit1')).toBe(false)
    expect(isLetterCode('Space')).toBe(false)
  })
})

describe('splitTextChunks（300 字符服务端上限）', () => {
  it('短文本单段', () => {
    expect(splitTextChunks('hello')).toEqual(['hello'])
  })

  it('恰好上限单段、超限分段', () => {
    const atLimit = 'a'.repeat(INJECT_TEXT_MAX_LENGTH)
    expect(splitTextChunks(atLimit)).toEqual([atLimit])
    const over = 'a'.repeat(750)
    const chunks = splitTextChunks(over)
    expect(chunks.map((c) => c.length)).toEqual([300, 300, 150])
    expect(chunks.join('')).toBe(over)
  })

  it('边界代理对不拆半', () => {
    // 高代理对恰好横跨 299/300 边界
    const text = 'a'.repeat(299) + '😀' + 'b'.repeat(100)
    const chunks = splitTextChunks(text)
    expect(chunks.join('')).toBe(text)
    for (const chunk of chunks) {
      const last = chunk.charCodeAt(chunk.length - 1)
      expect(last >= 0xd800 && last <= 0xdbff).toBe(false)  // 段尾非高代理
      const first = chunk.charCodeAt(0)
      expect(first >= 0xdc00 && first <= 0xdfff).toBe(false)  // 段首非低代理
    }
  })

  it('空串返回空数组（不发空 text 帧）', () => {
    expect(splitTextChunks('')).toEqual([])
  })
})
