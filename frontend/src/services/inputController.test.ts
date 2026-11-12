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
  // 交叉保留 Mock 类型（mockClear 等断言辅助），同时满足 InputController 构造参数
  return { send: vi.fn() } as unknown as ConstructorParameters<typeof InputController>[0] & {
    send: ReturnType<typeof vi.fn>
  }
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

describe('方案56 手动旋转坐标反变换', () => {
  // 原生帧 200×100；旋转态渲染盒宽高比恒等于原生（无内部 letterbox），
  // 故 getBoundingClientRect 即显示区，越界钳制到四角后反旋回原生坐标。
  const rectL = { left: 0, top: 0, width: 100, height: 200 } // 90/270：宽高对调
  const rectP = { left: 0, top: 0, width: 200, height: 100 } // 180：方向不变

  it('rotation=0 与三参调用完全一致（回归护栏）', () => {
    const ic = new InputController(makeWs(), 200, 100)
    const canvas = makeCanvasWith(200, 100, { left: 0, top: 0, width: 200, height: 100 })
    expect(ic.mapToScreen(50, 25, canvas, 0)).toEqual(ic.mapToScreen(50, 25, canvas))
  })

  it('90°：顺时针旋转，屏幕左上落回原生左下', () => {
    const ic = new InputController(makeWs(), 200, 100)
    const canvas = makeCanvasWith(200, 100, rectL)
    expect(ic.mapToScreen(0, 0, canvas, 90)).toEqual({ x: 0, y: 100 })
    expect(ic.mapToScreen(100, 0, canvas, 90)).toEqual({ x: 0, y: 0 })
    expect(ic.mapToScreen(0, 200, canvas, 90)).toEqual({ x: 200, y: 100 })
    expect(ic.mapToScreen(100, 200, canvas, 90)).toEqual({ x: 200, y: 0 })
    expect(ic.mapToScreen(50, 100, canvas, 90)).toEqual({ x: 100, y: 50 })
  })

  it('180°：中心对称翻转', () => {
    const ic = new InputController(makeWs(), 200, 100)
    const canvas = makeCanvasWith(200, 100, rectP)
    expect(ic.mapToScreen(0, 0, canvas, 180)).toEqual({ x: 200, y: 100 })
    expect(ic.mapToScreen(200, 100, canvas, 180)).toEqual({ x: 0, y: 0 })
    expect(ic.mapToScreen(100, 50, canvas, 180)).toEqual({ x: 100, y: 50 })
  })

  it('270°：逆时针旋转，屏幕左上落回原生右上', () => {
    const ic = new InputController(makeWs(), 200, 100)
    const canvas = makeCanvasWith(200, 100, rectL)
    expect(ic.mapToScreen(0, 0, canvas, 270)).toEqual({ x: 200, y: 0 })
    expect(ic.mapToScreen(100, 0, canvas, 270)).toEqual({ x: 200, y: 100 })
    expect(ic.mapToScreen(0, 200, canvas, 270)).toEqual({ x: 0, y: 0 })
    expect(ic.mapToScreen(100, 200, canvas, 270)).toEqual({ x: 0, y: 100 })
  })

  it('setDisplayRotation 后三参调用走旋转分支（默认值来源）', () => {
    const ic = new InputController(makeWs(), 200, 100)
    const canvas = makeCanvasWith(200, 100, rectL)
    ic.setDisplayRotation(90)
    expect(ic.mapToScreen(0, 0, canvas)).toEqual({ x: 0, y: 100 })
    ic.setDisplayRotation(0)
    expect(ic.mapToScreen(50, 50, canvas)).toEqual(ic.mapToScreen(50, 50, canvas, 0))
  })

  it('旋转态越界钳制到原生四角', () => {
    const ic = new InputController(makeWs(), 200, 100)
    const canvas = makeCanvasWith(200, 100, rectL)
    expect(ic.mapToScreen(-10, -10, canvas, 90)).toEqual({ x: 0, y: 100 })
    expect(ic.mapToScreen(110, 210, canvas, 90)).toEqual({ x: 200, y: 0 })
  })

  it('handleMouseDown 透传 displayRotation 到 tap 坐标', () => {
    const ws = makeWs()
    const ic = new InputController(ws, 200, 100)
    const canvas = makeCanvasWith(200, 100, rectL)
    ic.setDisplayRotation(90)
    ic.handleMouseDown(makeMouseEvent(0, 0), canvas)
    ic.handleMouseUp(makeMouseEvent(0, 0), canvas)
    expect(ws.send).toHaveBeenCalledWith({ action: 'touch', x: 0, y: 100 })
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

  it('长按 500ms 发送 long_press（duration 1000ms）', () => {
    vi.useFakeTimers()
    const ws = makeWs()
    const ic = new InputController(ws, 100, 100)

    ic.handleMouseDown(makeMouseEvent(50, 50), makeCanvas())
    vi.advanceTimersByTime(600)

    expect(ws.send).toHaveBeenCalledWith({
      action: 'long_press', x: 50, y: 50, duration: 1000,
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

  it('长按触发后再移动释放不再注入 swipe：单手势（方案 51 F1-03）', () => {
    vi.useFakeTimers()
    const ws = makeWs()
    const ic = new InputController(ws, 100, 100)
    const canvas = makeCanvas()

    ic.handleMouseDown(makeMouseEvent(50, 50), canvas)
    vi.advanceTimersByTime(600) // 触发长按
    ic.handleMouseMove(makeMouseEvent(90, 50), canvas)
    ic.handleMouseUp(makeMouseEvent(90, 50), canvas)

    expect(ws.send).toHaveBeenCalledTimes(1)
    expect(ws.send).toHaveBeenCalledWith({
      action: 'long_press', x: 50, y: 50, duration: 1000,
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

  it('触摸长按 500ms 发送 long_press', () => {
    vi.useFakeTimers()
    const ws = makeWs()
    const ic = new InputController(ws, 100, 100)

    ic.handleTouchStart(makeTouchEvent([[50, 50]]), makeCanvas())
    vi.advanceTimersByTime(600)

    expect(ws.send).toHaveBeenCalledWith({
      action: 'long_press', x: 50, y: 50, duration: 1000,
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

  it('触摸长按触发后再移动释放不再注入 swipe（方案 51 F1-03）', () => {
    vi.useFakeTimers()
    const ws = makeWs()
    const ic = new InputController(ws, 100, 100)
    const canvas = makeCanvas()

    ic.handleTouchStart(makeTouchEvent([[50, 50]]), canvas)
    vi.advanceTimersByTime(600)
    ic.handleTouchMove(makeTouchEvent([[90, 50]]), canvas)
    ic.handleTouchEnd(makeTouchEvent([], [[90, 50]]), canvas)

    expect(ws.send).toHaveBeenCalledTimes(1)
    expect(ws.send).toHaveBeenCalledWith({
      action: 'long_press', x: 50, y: 50, duration: 1000,
    })
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

describe('输入发送回调（setInputHandler，方案 27）', () => {
  it('按键与文本发送后触发回调', () => {
    const ws = makeWs()
    const ic = new InputController(ws, 100, 100)
    const handler = vi.fn()
    ic.setInputHandler(handler)

    ic.sendKey(KeyCode.HOME)
    ic.sendText('hi')

    expect(handler).toHaveBeenCalledTimes(2)
    expect(ws.send).toHaveBeenCalledTimes(2)
  })

  it('点击/滑动/长按手势发送后触发回调', () => {
    vi.useFakeTimers()
    const ws = makeWs()
    const ic = new InputController(ws, 100, 100)
    const handler = vi.fn()
    ic.setInputHandler(handler)
    const canvas = makeCanvas()

    ic.handleMouseDown(makeMouseEvent(50, 50), canvas)
    ic.handleMouseUp(makeMouseEvent(50, 50), canvas) // tap
    expect(handler).toHaveBeenCalledTimes(1)

    ic.handleMouseDown(makeMouseEvent(50, 50), canvas)
    vi.advanceTimersByTime(50)
    ic.handleMouseMove(makeMouseEvent(80, 50), canvas)
    ic.handleMouseUp(makeMouseEvent(80, 50), canvas) // swipe
    expect(handler).toHaveBeenCalledTimes(2)

    ic.handleMouseDown(makeMouseEvent(50, 50), canvas)
    vi.advanceTimersByTime(600) // long_press
    expect(handler).toHaveBeenCalledTimes(3)

    vi.useRealTimers()
  })

  it('未设置 handler 时各输入不抛错', () => {
    const ic = new InputController(makeWs(), 100, 100)
    expect(() => ic.sendKey(KeyCode.BACK)).not.toThrow()
    expect(() => ic.sendText('x')).not.toThrow()
  })
})

// ---------------------------------------------------------------------------
// 方案 58 批5-3：D-19 零尺寸守卫 / D-07 滚轮旋转反变换 / D-08 剪贴板超时
// ---------------------------------------------------------------------------

describe('方案58 D-19 mapToScreen 零尺寸守卫', () => {
  it('CSS 宽为 0：返回 null（旋转分支同样守卫）', () => {
    const ic = new InputController(makeWs(), 100, 100)
    const canvas = makeCanvasWith(100, 100, { left: 0, top: 0, width: 0, height: 100 })
    expect(ic.mapToScreen(50, 50, canvas)).toBeNull()
    expect(ic.mapToScreen(50, 50, canvas, 90)).toBeNull()
  })

  it('CSS 高为 0：返回 null', () => {
    const ic = new InputController(makeWs(), 100, 100)
    const canvas = makeCanvasWith(100, 100, { left: 0, top: 0, width: 100, height: 0 })
    expect(ic.mapToScreen(50, 50, canvas)).toBeNull()
  })

  it('display 为 none 时按下/释放不产生任何注入', () => {
    const ws = makeWs()
    const ic = new InputController(ws, 100, 100)
    const canvas = makeCanvasWith(100, 100, { left: 0, top: 0, width: 0, height: 0 })
    ic.handleMouseDown(makeMouseEvent(50, 50), canvas)
    ic.handleMouseUp(makeMouseEvent(50, 50), canvas)
    expect(ws.send).not.toHaveBeenCalled()
  })

  it('零尺寸画布上滚轮不注入', () => {
    const ws = makeWs()
    const ic = new InputController(ws, 100, 100)
    const canvas = makeCanvasWith(100, 100, { left: 0, top: 0, width: 0, height: 0 })
    const wheel = {
      deltaX: 0, deltaY: 120, clientX: 50, clientY: 50, preventDefault: vi.fn(),
    } as unknown as WheelEvent
    ic.handleWheel(wheel, canvas)
    expect(ws.send).not.toHaveBeenCalled()
  })

  it('零像素画布（width 属性为 0）也守卫：videoAspect 为 NaN', () => {
    const ic = new InputController(makeWs(), 100, 100)
    const canvas = makeCanvasWith(0, 0, { left: 0, top: 0, width: 100, height: 100 })
    expect(ic.mapToScreen(50, 50, canvas)).toBeNull()
  })
})

describe('方案58 D-07 滚轮旋转反变换', () => {
  function makeWheel(deltaX: number, deltaY: number) {
    return {
      deltaX, deltaY, clientX: 50, clientY: 50, preventDefault: vi.fn(),
    } as unknown as WheelEvent
  }

  /** 中心点在任何旋转角下都映射回像素中心（100x100 rect=100x100），便于只断言轴向。 */
  function centered(rotation: number) {
    const ws = makeWs()
    const ic = new InputController(ws, 100, 100)
    ic.setDisplayRotation(rotation)
    return { ws, ic }
  }

  it('rotation=0：纵向滚轮 → v_scroll，横向 → h_scroll（现状护栏）', () => {
    const { ws, ic } = centered(0)
    ic.handleWheel(makeWheel(0, 120), makeCanvas())
    expect(ws.send).toHaveBeenLastCalledWith({ action: 'scroll', x: 50, y: 50, h_scroll: 0, v_scroll: 1 })
    ws.send.mockClear()
    ic.handleWheel(makeWheel(120, 0), makeCanvas())
    expect(ws.send).toHaveBeenLastCalledWith({ action: 'scroll', x: 50, y: 50, h_scroll: 1, v_scroll: 0 })
  })

  it('rotation=90：屏幕向下滚动落为原生帧 +x 方向（h_scroll=1）', () => {
    const { ws, ic } = centered(90)
    ic.handleWheel(makeWheel(0, 120), makeCanvas())
    expect(ws.send).toHaveBeenLastCalledWith({ action: 'scroll', x: 50, y: 50, h_scroll: 1, v_scroll: 0 })
  })

  it('rotation=90：屏幕向右滚动落为原生帧 -y 方向（v_scroll=-1）', () => {
    const { ws, ic } = centered(90)
    ic.handleWheel(makeWheel(120, 0), makeCanvas())
    expect(ws.send).toHaveBeenLastCalledWith({ action: 'scroll', x: 50, y: 50, h_scroll: 0, v_scroll: -1 })
  })

  it('rotation=180：纵向滚轮反号', () => {
    const { ws, ic } = centered(180)
    ic.handleWheel(makeWheel(0, 120), makeCanvas())
    expect(ws.send).toHaveBeenLastCalledWith({ action: 'scroll', x: 50, y: 50, h_scroll: 0, v_scroll: -1 })
    ws.send.mockClear()
    ic.handleWheel(makeWheel(120, 0), makeCanvas())
    expect(ws.send).toHaveBeenLastCalledWith({ action: 'scroll', x: 50, y: 50, h_scroll: -1, v_scroll: 0 })
  })

  it('rotation=270：屏幕向下滚动落为原生帧 -x 方向（h_scroll=-1）', () => {
    const { ws, ic } = centered(270)
    ic.handleWheel(makeWheel(0, 120), makeCanvas())
    expect(ws.send).toHaveBeenLastCalledWith({ action: 'scroll', x: 50, y: 50, h_scroll: -1, v_scroll: 0 })
  })
})

describe('方案58 D-08 剪贴板请求超时', () => {
  it('5s 无应答 resolve null，不再挂死', async () => {
    vi.useFakeTimers()
    const ws = makeWs()
    const ic = new InputController(ws, 100, 100)
    const p = ic.requestClipboard()
    vi.advanceTimersByTime(4000)
    let done = false
    p.then(() => { done = true })
    await Promise.resolve()
    expect(done).toBe(false)
    vi.advanceTimersByTime(1000)
    await vi.advanceTimersByTimeAsync(0)
    expect(await p).toBe(null)
  })

  it('超时前正常应答 resolve 文本，定时器被清除', async () => {
    vi.useFakeTimers()
    const ws = makeWs()
    const ic = new InputController(ws, 100, 100)
    const p = ic.requestClipboard(1)
    expect(ic.handleClipboardMessage({ type: 'clipboard', text: 'abc' })).toBe(true)
    expect(await p).toBe('abc')
    // 应答后推进超过 5s：已决断的 promise 不受影响，且无未清理定时器抛错
    vi.advanceTimersByTime(6000)
    expect(await p).toBe('abc')
  })

  it('超时后才到达的应答被丢弃（返回已处理但不再 resolve）', async () => {
    vi.useFakeTimers()
    const ws = makeWs()
    const ic = new InputController(ws, 100, 100)
    const p = ic.requestClipboard()
    vi.advanceTimersByTime(5000)
    await vi.advanceTimersByTimeAsync(0)
    expect(await p).toBe(null)
    expect(ic.handleClipboardMessage({ type: 'clipboard', text: 'late' })).toBe(true)
  })

  it('新请求丢弃旧未决请求时同时清除旧定时器', async () => {
    vi.useFakeTimers()
    const ws = makeWs()
    const ic = new InputController(ws, 100, 100)
    const stale = ic.requestClipboard()
    const fresh = ic.requestClipboard()
    expect(await stale).toBe(null)
    vi.advanceTimersByTime(5000)
    await vi.advanceTimersByTimeAsync(0)
    expect(await fresh).toBe(null)
  })

  // 方案 58 F-05：clipboard_error 的 error 字段透出（API.md 承诺的失败区分度可用）
  it('clipboard_error 带 error 记入 clipboardError 供分级提示；超时与新请求复位该字段', async () => {
    vi.useFakeTimers()
    const ws = makeWs()
    const ic = new InputController(ws, 100, 100)
    expect(ic.clipboardError).toBeNull()

    const p = ic.requestClipboard()
    ic.handleClipboardMessage({ type: 'clipboard_error', error: 'unavailable_or_timeout' })
    expect(await p).toBe(null)
    expect(ic.clipboardError).toBe('unavailable_or_timeout')

    // 超时路径无 error 回执 → 字段复位（调用方回落「应答超时」文案）
    ic.requestClipboard()
    expect(ic.clipboardError).toBeNull()
    vi.advanceTimersByTime(5000)
    await vi.advanceTimersByTimeAsync(0)

    // 正常应答也复位
    const p2 = ic.requestClipboard()
    ic.handleClipboardMessage({ type: 'clipboard', text: 'ok' })
    expect(await p2).toBe('ok')
    expect(ic.clipboardError).toBeNull()
  })
})