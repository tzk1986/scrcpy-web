/**
 * InputController 方案 54 扩展测试
 * =================================
 *
 * 覆盖：
 *   - sendKey 向后兼容（无 opts 旧格式）与 down/repeat/meta 扩展
 *   - sendText 300 字符分段与空串不发帧
 *   - handleKeyDown/Up 镜像：meta 合成、repeat 透传、表外键忽略、
 *     IME 抑制（Process 键 / composing）、compositionend 整段注入
 *   - 停用/失焦补发 up 防卡键
 *   - D2 prefer-text 开关（字母走 text，修饰组合仍 keycode）
 *   - handleWheel ±1 档位
 *   - 剪贴板：sendClipboardSet 帧格式、requestClipboard 应答关联/丢弃
 */
import { describe, expect, it, vi } from 'vitest'
import { InputController, KeyCode } from './inputController'

function makeWs() {
  return { send: vi.fn() } as unknown as ConstructorParameters<typeof InputController>[0]
}

function makeCanvas() {
  return {
    width: 100,
    height: 100,
    getBoundingClientRect: () => ({ left: 0, top: 0, width: 100, height: 100 }),
  } as unknown as HTMLCanvasElement
}

function makeKeyEvent(
  code: string,
  overrides: Partial<{
    key: string
    repeat: boolean
    shiftKey: boolean
    ctrlKey: boolean
    altKey: boolean
    metaKey: boolean
  }> = {},
) {
  return {
    code,
    key: 'x',
    repeat: false,
    shiftKey: false,
    ctrlKey: false,
    altKey: false,
    metaKey: false,
    ...overrides,
  } as unknown as KeyboardEvent
}

function makeWheelEvent(deltaX: number, deltaY: number, clientX = 50, clientY = 50) {
  return {
    clientX,
    clientY,
    deltaX,
    deltaY,
    preventDefault: vi.fn(),
  } as unknown as WheelEvent
}

describe('方案54 sendKey 向后兼容与扩展', () => {
  it('无 opts：维持旧格式 {action:key, keycode}（4 虚拟键零改动）', () => {
    const ws = makeWs()
    const ic = new InputController(ws, 100, 100)
    ic.sendKey(KeyCode.BACK)
    expect(ws.send).toHaveBeenCalledWith({ action: 'key', keycode: 4 })
  })

  it('down=true 单发按下，repeat/meta 透传（缺省 0）', () => {
    const ws = makeWs()
    const ic = new InputController(ws, 100, 100)
    ic.sendKey(29, { down: true })
    ic.sendKey(29, { down: true, repeat: 1, meta: 1 })
    expect(ws.send).toHaveBeenNthCalledWith(
      1, { action: 'key', keycode: 29, down: true, repeat: 0, meta: 0 })
    expect(ws.send).toHaveBeenNthCalledWith(
      2, { action: 'key', keycode: 29, down: true, repeat: 1, meta: 1 })
  })

  it('sendText 超 300 自动分段', () => {
    const ws = makeWs()
    const ic = new InputController(ws, 100, 100)
    ic.sendText('a'.repeat(601))
    expect(ws.send).toHaveBeenCalledTimes(3)
    const texts = (ws.send as ReturnType<typeof vi.fn>).mock.calls.map(
      ([m]) => (m as { text: string }).text,
    )
    expect(texts.map((t) => t.length)).toEqual([300, 300, 1])
  })

  it('sendText 空串不发帧', () => {
    const ws = makeWs()
    const ic = new InputController(ws, 100, 100)
    ic.sendText('')
    expect(ws.send).not.toHaveBeenCalled()
  })
})

describe('方案54 handleKeyDown/Up 镜像', () => {
  it('字母按下：单发 down + meta 合成 + repeat 透传', () => {
    const ws = makeWs()
    const ic = new InputController(ws, 100, 100)
    ic.handleKeyDown(makeKeyEvent('KeyA', { shiftKey: true, repeat: true }))
    expect(ws.send).toHaveBeenCalledWith(
      { action: 'key', keycode: 29, down: true, repeat: 1, meta: 1 })
  })

  it('抬起：曾按下过的键单发 up', () => {
    const ws = makeWs()
    const ic = new InputController(ws, 100, 100)
    ic.handleKeyDown(makeKeyEvent('KeyA'))
    ic.handleKeyUp(makeKeyEvent('KeyA'))
    expect(ws.send).toHaveBeenLastCalledWith(
      { action: 'key', keycode: 29, down: false, repeat: 0, meta: 0 })
  })

  it('未按下过的键 keyup 忽略（prefer-text 字母抬起不补 up）', () => {
    const ws = makeWs()
    const ic = new InputController(ws, 100, 100)
    ic.handleKeyUp(makeKeyEvent('KeyA'))
    expect(ws.send).not.toHaveBeenCalled()
  })

  it('表外键忽略', () => {
    const ws = makeWs()
    const ic = new InputController(ws, 100, 100)
    ic.handleKeyDown(makeKeyEvent('AudioPlayPause'))
    expect(ws.send).not.toHaveBeenCalled()
  })

  it('IME 抑制：Process 键与 composing 期间 keydown 不镜像', () => {
    const ws = makeWs()
    const ic = new InputController(ws, 100, 100)
    ic.handleKeyDown(makeKeyEvent('KeyA', { key: 'Process' }))
    ic.setComposing(true)
    ic.handleKeyDown(makeKeyEvent('KeyA'))
    expect(ws.send).not.toHaveBeenCalled()
  })

  it('compositionend：结束组合态并整段注入文本', () => {
    const ws = makeWs()
    const ic = new InputController(ws, 100, 100)
    ic.setComposing(true)
    ic.handleCompositionEnd('你好世界')
    expect(ws.send).toHaveBeenCalledWith({ action: 'text', text: '你好世界' })
    ic.handleKeyDown(makeKeyEvent('KeyA'))  // 组合已结束，恢复镜像
    expect(ws.send).toHaveBeenCalledTimes(2)
  })

  it('停用（tab 隐藏）：补发所有在按下键 up 且忽略后续 keydown', () => {
    const ws = makeWs()
    const ic = new InputController(ws, 100, 100)
    ic.handleKeyDown(makeKeyEvent('KeyA'))
    ic.handleKeyDown(makeKeyEvent('ShiftLeft'))
    ;(ws.send as ReturnType<typeof vi.fn>).mockClear()
    ic.setEnabled(false)
    expect(ws.send).toHaveBeenCalledWith(
      { action: 'key', keycode: 29, down: false, repeat: 0, meta: 0 })
    expect(ws.send).toHaveBeenCalledWith(
      { action: 'key', keycode: 59, down: false, repeat: 0, meta: 0 })
    ic.handleKeyDown(makeKeyEvent('KeyB'))
    expect(ws.send).toHaveBeenCalledTimes(2)  // 停用期无新帧
  })

  it('releaseAllKeys 重复触发安全（无残键不抛错）', () => {
    const ic = new InputController(makeWs(), 100, 100)
    ic.handleKeyDown(makeKeyEvent('KeyA'))
    ic.releaseAllKeys()
    ic.releaseAllKeys()
  })
})

describe('方案54 D2 prefer-text 开关', () => {
  it('默认关闭：字母走 keyevent', () => {
    const ws = makeWs()
    const ic = new InputController(ws, 100, 100)
    ic.handleKeyDown(makeKeyEvent('KeyA', { key: 'a' }))
    expect(ws.send).toHaveBeenCalledWith(
      expect.objectContaining({ action: 'key', keycode: 29 }))
  })

  it('开启后字母走 text 注入（repeat 抑制；修饰组合仍 keycode）', () => {
    const ws = makeWs()
    const ic = new InputController(ws, 100, 100)
    ic.setPreferText(true)
    ic.handleKeyDown(makeKeyEvent('KeyA', { key: 'a' }))
    expect(ws.send).toHaveBeenCalledWith({ action: 'text', text: 'a' })
    ic.handleKeyDown(makeKeyEvent('KeyB', { key: 'b', repeat: true }))
    expect(ws.send).toHaveBeenCalledTimes(1)  // 重复字母不重发
    ;(ws.send as ReturnType<typeof vi.fn>).mockClear()
    ic.handleKeyDown(makeKeyEvent('KeyC', { key: 'c', ctrlKey: true }))
    expect(ws.send).toHaveBeenCalledWith(
      expect.objectContaining({ action: 'key', keycode: 31 }))  // Ctrl+C 仍 keycode
  })
})

describe('方案54 handleWheel', () => {
  it('纵向滚：sign 档位 + 设备坐标 + preventDefault', () => {
    const ws = makeWs()
    const ic = new InputController(ws, 100, 100)
    const e = makeWheelEvent(0, 120)
    ic.handleWheel(e, makeCanvas())
    expect(ws.send).toHaveBeenCalledWith(
      { action: 'scroll', x: 50, y: 50, h_scroll: 0, v_scroll: 1 })
    expect(e.preventDefault).toHaveBeenCalled()
  })

  it('向上+横向：±1 符号正确', () => {
    const ws = makeWs()
    const ic = new InputController(ws, 100, 100)
    ic.handleWheel(makeWheelEvent(-50, -100), makeCanvas())
    expect(ws.send).toHaveBeenCalledWith(
      { action: 'scroll', x: 50, y: 50, h_scroll: -1, v_scroll: -1 })
  })

  it('零增量不发帧；停用不发', () => {
    const ws = makeWs()
    const ic = new InputController(ws, 100, 100)
    ic.handleWheel(makeWheelEvent(0, 0), makeCanvas())
    expect(ws.send).not.toHaveBeenCalled()
    ic.handleWheel(makeWheelEvent(0, 120), makeCanvas())
    ic.setEnabled(false)
    ic.handleWheel(makeWheelEvent(0, 120), makeCanvas())
    expect(ws.send).toHaveBeenCalledTimes(1)
  })
})

describe('方案54 剪贴板', () => {
  it('sendClipboardSet 帧格式与 paste 缺省', () => {
    const ws = makeWs()
    const ic = new InputController(ws, 100, 100)
    ic.sendClipboardSet('中文文本', true)
    ic.sendClipboardSet('x')
    expect(ws.send).toHaveBeenNthCalledWith(
      1, { action: 'clipboard_set', text: '中文文本', paste: true })
    expect(ws.send).toHaveBeenNthCalledWith(
      2, { action: 'clipboard_set', text: 'x', paste: false })
  })

  it('requestClipboard：clipboard 应答 resolve 文本，copy_key 透传', async () => {
    const ws = makeWs()
    const ic = new InputController(ws, 100, 100)
    const p = ic.requestClipboard(1)
    expect(ws.send).toHaveBeenCalledWith({ action: 'clipboard_get', copy_key: 1 })
    expect(ic.handleClipboardMessage({ type: 'clipboard', text: '内容' })).toBe(true)
    await expect(p).resolves.toBe('内容')
  })

  it('clipboard_error → null；非剪贴板消息返回 false 不消费', async () => {
    const ws = makeWs()
    const ic = new InputController(ws, 100, 100)
    const p = ic.requestClipboard()
    expect(ic.handleClipboardMessage({ type: 'stream_ended' })).toBe(false)
    expect(ic.handleClipboardMessage({ type: 'clipboard_error' })).toBe(true)
    await expect(p).resolves.toBeNull()
  })

  it('新请求丢弃旧未决（旧 resolve null）', async () => {
    const ws = makeWs()
    const ic = new InputController(ws, 100, 100)
    const stale = ic.requestClipboard()
    const fresh = ic.requestClipboard()
    await expect(stale).resolves.toBeNull()
    ic.handleClipboardMessage({ type: 'clipboard', text: 'new' })
    await expect(fresh).resolves.toBe('new')
  })

  it('无在途请求时应答被安全消费', () => {
    const ic = new InputController(makeWs(), 100, 100)
    expect(() =>
      ic.handleClipboardMessage({ type: 'clipboard', text: 'orphan' })).not.toThrow()
  })
})
