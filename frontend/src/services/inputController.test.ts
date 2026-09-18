/**
 * InputController setEnabled 行为测试（方案 17 实施项 4）
 * =========================================================
 *
 * 覆盖：
 *   - 默认启用：指针事件正常发送
 *   - setEnabled(false) 后指针事件被忽略
 *   - setEnabled(false) 取消挂起的长按定时器（防后台残留触发）
 *   - 重新启用后恢复发送
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { InputController } from './inputController'

function makeWs() {
  return { send: vi.fn() } as unknown as ConstructorParameters<typeof InputController>[0]
}

/** 100x100、无 letterbox 的假 canvas（坐标映射返回原像素坐标）。 */
function makeCanvas() {
  return {
    width: 100,
    height: 100,
    getBoundingClientRect: () => ({ left: 0, top: 0, width: 100, height: 100 }),
  } as unknown as HTMLCanvasElement
}

function makeMouseEvent(x = 50, y = 50) {
  return { clientX: x, clientY: y, preventDefault: vi.fn() } as unknown as MouseEvent
}

afterEach(() => {
  vi.useRealTimers()
})

describe('InputController.setEnabled', () => {
  it('默认启用：按下后快速释放发送 tap', () => {
    const ws = makeWs()
    const ic = new InputController(ws, 100, 100)
    const canvas = makeCanvas()

    ic.handleMouseDown(makeMouseEvent(), canvas)
    ic.handleMouseUp(makeMouseEvent(), canvas)

    expect(ws.send).toHaveBeenCalledWith({ action: 'touch', x: 50, y: 50 })
  })

  it('停用后指针事件被忽略，重新启用后恢复', () => {
    const ws = makeWs()
    const ic = new InputController(ws, 100, 100)
    const canvas = makeCanvas()

    ic.setEnabled(false)
    ic.handleMouseDown(makeMouseEvent(), canvas)
    ic.handleMouseUp(makeMouseEvent(), canvas)
    expect(ws.send).not.toHaveBeenCalled()

    ic.setEnabled(true)
    ic.handleMouseDown(makeMouseEvent(), canvas)
    ic.handleMouseUp(makeMouseEvent(), canvas)
    expect(ws.send).toHaveBeenCalledWith({ action: 'touch', x: 50, y: 50 })
  })

  it('停用时取消挂起的长按定时器', () => {
    vi.useFakeTimers()
    const ws = makeWs()
    const ic = new InputController(ws, 100, 100)

    ic.handleMouseDown(makeMouseEvent(), makeCanvas())
    ic.setEnabled(false)
    vi.advanceTimersByTime(600)

    expect(ws.send).not.toHaveBeenCalled()
  })
})