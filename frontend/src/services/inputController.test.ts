/**
 * InputController 行为测试
 * =========================
 *
 * 覆盖：
 *   - setEnabled 停用/恢复（方案 17 实施项 4）
 *   - 坐标映射 mapToScreen 的 letterbox（上下/左右黑边）与边界钳制
 *   - 鼠标手势：tap / swipe / long-press 及互相取消
 *   - 触摸手势：单指 tap/swipe/long-press、多指忽略
 *   - 按键码与文本输入
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { InputController, KeyCode } from './inputController'

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

/** 自定义像素尺寸与 CSS 盒的假 canvas（用于 letterbox / 边界映射）。 */
function makeCanvasWith(
  pixelWidth: number,
  pixelHeight: number,
  rect: { left: number; top: number; width: number; height: number },
) {
  return {
    width: pixelWidth,
    height: pixelHeight,
    getBoundingClientRect: () => rect,
  } as unknown as HTMLCanvasElement
}

function makeMouseEvent(x = 50, y = 50) {
  return { clientX: x, clientY: y, preventDefault: vi.fn() } as unknown as MouseEvent
}

function makeTouchEvent(points: Array<[number, number]>, changed: Array<[number, number]> = points) {
  const toTouch = ([x, y]: [number, number]) => ({ clientX: x, clientY: y })
  return {
    touches: points.map(toTouch),
    changedTouches: changed.map(toTouch),
    preventDefault: vi.fn(),
  } as unknown as TouchEvent
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

  it('停用后触摸事件与移动/释放同样被忽略', () => {
    const ws = makeWs()
    const ic = new InputController(ws, 100, 100)
    const canvas = makeCanvas()
    ic.setEnabled(false)

    const start = makeTouchEvent([[50, 50]])
    const move = makeTouchEvent([[80, 50]])
    const end = makeTouchEvent([], [[80, 50]])
    ic.handleTouchStart(start, canvas)
    ic.handleMouseMove(makeMouseEvent(), canvas)
    ic.handleMouseUp(makeMouseEvent(), canvas)
    ic.handleTouchMove(move, canvas)
    ic.handleTouchEnd(end, canvas)

    expect(start.preventDefault).not.toHaveBeenCalled()
    expect(move.preventDefault).not.toHaveBeenCalled()
    expect(end.preventDefault).not.toHaveBeenCalled()
    expect(ws.send).not.toHaveBeenCalled()
  })
})

describe('mapToScreen 坐标映射', () => {
  it('无 letterbox 时线性映射并四舍五入', () => {
    const ic = new InputController(makeWs(), 100, 100)
    const canvas = makeCanvas()

    expect(ic.mapToScreen(50, 50, canvas)).toEqual({ x: 50, y: 50 })
    expect(ic.mapToScreen(0, 0, canvas)).toEqual({ x: 0, y: 0 })
    expect(ic.mapToScreen(100, 100, canvas)).toEqual({ x: 100, y: 100 })
  })

  it('视频宽于容器（上下黑边）时按显示区域映射', () => {
    // canvas 200x100（宽高比 2）放入 100x100 容器：显示高度 50、上下各 25 黑边
    const ic = new InputController(makeWs(), 200, 100)
    const canvas = makeCanvasWith(200, 100, { left: 0, top: 0, width: 100, height: 100 })

    expect(ic.mapToScreen(100, 25, canvas)).toEqual({ x: 200, y: 0 })
    expect(ic.mapToScreen(0, 75, canvas)).toEqual({ x: 0, y: 100 })
  })

  it('视频高于容器（左右黑边）时按显示区域映射', () => {
    // canvas 100x200（宽高比 0.5）放入 100x100 容器：显示宽度 50、左右各 25 黑边
    const ic = new InputController(makeWs(), 100, 200)
    const canvas = makeCanvasWith(100, 200, { left: 0, top: 0, width: 100, height: 100 })

    expect(ic.mapToScreen(25, 0, canvas)).toEqual({ x: 0, y: 0 })
    expect(ic.mapToScreen(75, 100, canvas)).toEqual({ x: 100, y: 200 })
  })

  it('黑边区域外的点击被钳制到画布边界', () => {
    const ic = new InputController(makeWs(), 200, 100)
    const canvas = makeCanvasWith(200, 100, { left: 0, top: 0, width: 100, height: 100 })

    expect(ic.mapToScreen(50, 0, canvas)).toEqual({ x: 100, y: 0 })
    expect(ic.mapToScreen(50, 100, canvas)).toEqual({ x: 100, y: 100 })
  })

  it('完全越界的点击被钳制到四个角', () => {
    const ic = new InputController(makeWs(), 100, 100)
    const canvas = makeCanvas()

    expect(ic.mapToScreen(-50, -50, canvas)).toEqual({ x: 0, y: 0 })
    expect(ic.mapToScreen(150, 150, canvas)).toEqual({ x: 100, y: 100 })
  })
})

describe('鼠标手势', () => {
  it('未按下时的移动/释放不产生事件', () => {
    const ws = makeWs()
    const ic = new InputController(ws, 100, 100)
    const canvas = makeCanvas()

    const move = makeMouseEvent()
    const up = makeMouseEvent()
    ic.handleMouseMove(move, canvas)
    ic.handleMouseUp(up, canvas)

    expect(move.preventDefault).not.toHaveBeenCalled()
    expect(up.preventDefault).not.toHaveBeenCalled()
    expect(ws.send).not.toHaveBeenCalled()
  })

  it('移动超过阈值后释放发送 swipe（duration 下限 100ms）', () => {
    vi.useFakeTimers()
    const ws = makeWs()
    const ic = new InputController(ws, 100, 100)
    const canvas = makeCanvas()

    ic.handleMouseDown(makeMouseEvent(50, 50), canvas)
    vi.advanceTimersByTime(50)

    const move = makeMouseEvent(80, 50)
    ic.handleMouseMove(move, canvas)
    expect(move.preventDefault).toHaveBeenCalled()

    ic.handleMouseUp(makeMouseEvent(80, 50), canvas)
    expect(ws.send).toHaveBeenCalledWith({
      action: 'swipe', x1: 50, y1: 50, x2: 80, y2: 50, duration: 100,
    })
  })

  it('长按 500ms 发送原地 swipe（duration 1000ms）', () => {
    vi.useFakeTimers()
    const ws = makeWs()
    const ic = new InputController(ws, 100, 100)

    ic.handleMouseDown(makeMouseEvent(50, 50), makeCanvas())
    vi.advanceTimersByTime(600)

    expect(ws.send).toHaveBeenCalledWith({
      action: 'swipe', x1: 50, y1: 50, x2: 50, y2: 50, duration: 1000,
    })
  })

  it('长按触发后释放不再补发 tap', () => {
    vi.useFakeTimers()
    const ws = makeWs()
    const ic = new InputController(ws, 100, 100)
    const canvas = makeCanvas()

    ic.handleMouseDown(makeMouseEvent(50, 50), canvas)
    vi.advanceTimersByTime(600)
    ic.handleMouseUp(makeMouseEvent(50, 50), canvas)

    expect(ws.send).toHaveBeenCalledTimes(1)
  })

  it('移动超过阈值会取消挂起的长按', () => {
    vi.useFakeTimers()
    const ws = makeWs()
    const ic = new InputController(ws, 100, 100)
    const canvas = makeCanvas()

    ic.handleMouseDown(makeMouseEvent(50, 50), canvas)
    ic.handleMouseMove(makeMouseEvent(80, 50), canvas)
    vi.advanceTimersByTime(600)

    expect(ws.send).not.toHaveBeenCalled()
  })

  it('长按后进行移动拖拽释放发送 swipe（长按已取消）', () => {
    vi.useFakeTimers()
    const ws = makeWs()
    const ic = new InputController(ws, 100, 100)
    const canvas = makeCanvas()

    ic.handleMouseDown(makeMouseEvent(50, 50), canvas)
    vi.advanceTimersByTime(600) // 触发长按
    ic.handleMouseMove(makeMouseEvent(90, 50), canvas)
    ic.handleMouseUp(makeMouseEvent(90, 50), canvas)

    expect(ws.send).toHaveBeenCalledTimes(2)
    expect(ws.send).toHaveBeenLastCalledWith({
      action: 'swipe', x1: 50, y1: 50, x2: 90, y2: 50, duration: 600,
    })
  })

  it('长按阈值内的小幅抖动仍算 tap', () => {
    const ws = makeWs()
    const ic = new InputController(ws, 100, 100)
    const canvas = makeCanvas()

    ic.handleMouseDown(makeMouseEvent(50, 50), canvas)
    ic.handleMouseMove(makeMouseEvent(60, 60), canvas) // 位移 14px < 20px
    ic.handleMouseUp(makeMouseEvent(60, 60), canvas)

    expect(ws.send).toHaveBeenCalledWith({ action: 'touch', x: 50, y: 50 })
  })
})

describe('触摸手势', () => {
  it('单指快速点击发送 tap', () => {
    const ws = makeWs()
    const ic = new InputController(ws, 100, 100)
    const canvas = makeCanvas()

    ic.handleTouchStart(makeTouchEvent([[50, 50]]), canvas)
    ic.handleTouchEnd(makeTouchEvent([], [[50, 50]]), canvas)

    expect(ws.send).toHaveBeenCalledWith({ action: 'touch', x: 50, y: 50 })
  })

  it('单指滑动发送 swipe', () => {
    vi.useFakeTimers()
    const ws = makeWs()
    const ic = new InputController(ws, 100, 100)
    const canvas = makeCanvas()

    ic.handleTouchStart(makeTouchEvent([[50, 50]]), canvas)
    vi.advanceTimersByTime(30)
    ic.handleTouchMove(makeTouchEvent([[80, 50]]), canvas)
    ic.handleTouchEnd(makeTouchEvent([], [[80, 50]]), canvas)

    expect(ws.send).toHaveBeenCalledWith({
      action: 'swipe', x1: 50, y1: 50, x2: 80, y2: 50, duration: 100,
    })
  })

  it('多指按下被忽略（不建立触摸状态）', () => {
    const ws = makeWs()
    const ic = new InputController(ws, 100, 100)
    const canvas = makeCanvas()

    const start = makeTouchEvent([[10, 10], [20, 20]])
    ic.handleTouchStart(start, canvas)
    expect(start.preventDefault).toHaveBeenCalled()

    const end = makeTouchEvent([], [[10, 10]])
    ic.handleTouchEnd(end, canvas)
    expect(end.preventDefault).not.toHaveBeenCalled()
    expect(ws.send).not.toHaveBeenCalled()
  })

  it('触摸移动中多指被忽略但已阻止默认行为', () => {
    const ws = makeWs()
    const ic = new InputController(ws, 100, 100)
    const canvas = makeCanvas()

    ic.handleTouchStart(makeTouchEvent([[50, 50]]), canvas)
    const move = makeTouchEvent([[50, 50], [60, 60]])
    ic.handleTouchMove(move, canvas)
    expect(move.preventDefault).toHaveBeenCalled()

    // 位置未更新：原位释放仍是 tap
    ic.handleTouchEnd(makeTouchEvent([], [[50, 50]]), canvas)
    expect(ws.send).toHaveBeenCalledWith({ action: 'touch', x: 50, y: 50 })
  })

  it('未开始时触摸移动/结束不产生事件', () => {
    const ws = makeWs()
    const ic = new InputController(ws, 100, 100)
    const canvas = makeCanvas()

    const move = makeTouchEvent([[50, 50]])
    const end = makeTouchEvent([], [[50, 50]])
    ic.handleTouchMove(move, canvas)
    ic.handleTouchEnd(end, canvas)

    expect(move.preventDefault).not.toHaveBeenCalled()
    expect(end.preventDefault).not.toHaveBeenCalled()
    expect(ws.send).not.toHaveBeenCalled()
  })

  it('触摸长按 500ms 发送原地 swipe', () => {
    vi.useFakeTimers()
    const ws = makeWs()
    const ic = new InputController(ws, 100, 100)

    ic.handleTouchStart(makeTouchEvent([[50, 50]]), makeCanvas())
    vi.advanceTimersByTime(600)

    expect(ws.send).toHaveBeenCalledWith({
      action: 'swipe', x1: 50, y1: 50, x2: 50, y2: 50, duration: 1000,
    })
  })

  it('触摸移动超过阈值取消长按', () => {
    vi.useFakeTimers()
    const ws = makeWs()
    const ic = new InputController(ws, 100, 100)
    const canvas = makeCanvas()

    ic.handleTouchStart(makeTouchEvent([[50, 50]]), canvas)
    ic.handleTouchMove(makeTouchEvent([[80, 50]]), canvas)
    vi.advanceTimersByTime(600)

    expect(ws.send).not.toHaveBeenCalled()
  })

  it('触摸长按触发后释放不再补发 tap', () => {
    vi.useFakeTimers()
    const ws = makeWs()
    const ic = new InputController(ws, 100, 100)
    const canvas = makeCanvas()

    ic.handleTouchStart(makeTouchEvent([[50, 50]]), canvas)
    vi.advanceTimersByTime(600)
    ic.handleTouchEnd(makeTouchEvent([], [[50, 50]]), canvas)

    expect(ws.send).toHaveBeenCalledTimes(1)
  })
})

describe('按键与文本输入', () => {
  it('sendKey 发送 Android 按键码', () => {
    const ws = makeWs()
    const ic = new InputController(ws, 100, 100)

    ic.sendKey(KeyCode.HOME)

    expect(ws.send).toHaveBeenCalledWith({ action: 'key', keycode: 3 })
    expect(KeyCode.BACK).toBe(4)
    expect(KeyCode.APP_SWITCH).toBe(187)
  })

  it('sendText 发送文本', () => {
    const ws = makeWs()
    const ic = new InputController(ws, 100, 100)

    ic.sendText('hello 世界')

    expect(ws.send).toHaveBeenCalledWith({ action: 'text', text: 'hello 世界' })
  })

  it('setDeviceResolution 为兼容保留的空操作', () => {
    const ic = new InputController(makeWs(), 100, 100)
    expect(() => ic.setDeviceResolution(1080, 1920)).not.toThrow()
  })
})