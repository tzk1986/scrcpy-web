/**
 * 输入控制器
 * ============
 *
 * 处理浏览器端输入事件（鼠标/触摸），将其映射到设备坐标，
 * 并通过 WebSocket 发送给后端。
 *
 * 坐标映射：
 *   浏览器坐标 (clientX, clientY) → 设备坐标 (deviceX, deviceY)
 *   通过 canvas 元素与设备分辨率的比例进行转换。
 *
 * 手势识别：
 *   - 点击（tap）：按下 < 200ms 且移动距离 < 20px
 *   - 滑动（swipe）：按下后移动超过 20px
 *   - 长按（long_press）：按下 > 500ms 且移动距离 < 20px
 *
 * 使用方式：
 *   const controller = new InputController(ws, 1080, 1920)
 *   controller.handleMouseDown(event, canvasElement)
 */

import type { WebSocketService } from './websocket'

/** Android 常用按键码 */
export const KeyCode = {
  HOME: 3,
  BACK: 4,
  MENU: 82,
  POWER: 26,
  VOLUME_UP: 24,
  VOLUME_DOWN: 25,
  ENTER: 66,
  DEL: 67,       // 退格
  ESCAPE: 111,
  APP_SWITCH: 187,
} as const

/** 触摸状态 */
interface TouchState {
  startX: number
  startY: number
  startTime: number
  lastX: number
  lastY: number
  isTracking: boolean
}

export class InputController {
  private ws: WebSocketService
  private deviceWidth: number
  private deviceHeight: number
  private touchState: TouchState | null = null
  private longPressTimer: number | null = null
  private readonly LONG_PRESS_THRESHOLD = 500  // ms
  private readonly MOVE_THRESHOLD = 20         // px

  /**
   * @param ws - WebSocket 连接
   * @param deviceWidth - 设备屏幕宽度（像素）
   * @param deviceHeight - 设备屏幕高度（像素）
   */
  constructor(ws: WebSocketService, deviceWidth: number, deviceHeight: number) {
    this.ws = ws
    this.deviceWidth = deviceWidth
    this.deviceHeight = deviceHeight
  }

  /** 更新设备分辨率（从设备信息获取后调用） */
  setDeviceResolution(width: number, height: number) {
    this.deviceWidth = width
    this.deviceHeight = height
  }

  /**
   * 将浏览器坐标映射到设备坐标。
   *
   * @param clientX - 浏览器 clientX
   * @param clientY - 浏览器 clientY
   * @param canvas - canvas DOM 元素
   * @returns 设备坐标 { x, y }
   */
  mapToScreen(clientX: number, clientY: number, canvas: HTMLCanvasElement): { x: number; y: number } {
    const rect = canvas.getBoundingClientRect()
    // canvas 显示尺寸可能与实际尺寸不同（CSS 缩放）
    // 需要计算显示区域中视频的实际位置
    const canvasAspect = canvas.width / canvas.height
    const containerAspect = rect.width / rect.height

    let videoWidth: number
    let videoHeight: number
    let offsetX: number
    let offsetY: number

    if (canvasAspect > containerAspect) {
      // 视频宽度填满容器，上下有黑边
      videoWidth = rect.width
      videoHeight = rect.width / canvasAspect
      offsetX = 0
      offsetY = (rect.height - videoHeight) / 2
    } else {
      // 视频高度填满容器，左右有黑边
      videoHeight = rect.height
      videoWidth = rect.height * canvasAspect
      offsetX = (rect.width - videoWidth) / 2
      offsetY = 0
    }

    // 计算点击在视频区域内的相对位置
    const relX = (clientX - rect.left - offsetX) / videoWidth
    const relY = (clientY - rect.top - offsetY) / videoHeight

    // 映射到设备坐标
    return {
      x: Math.round(relX * this.deviceWidth),
      y: Math.round(relY * this.deviceHeight),
    }
  }

  /** 鼠标按下 */
  handleMouseDown(e: MouseEvent, canvas: HTMLCanvasElement) {
    e.preventDefault()
    const { x, y } = this.mapToScreen(e.clientX, e.clientY, canvas)

    this.touchState = {
      startX: x,
      startY: y,
      startTime: Date.now(),
      lastX: x,
      lastY: y,
      isTracking: true,
    }

    // 长按检测
    this.longPressTimer = window.setTimeout(() => {
      if (this.touchState && this.touchState.isTracking) {
        const dx = Math.abs(this.touchState.lastX - this.touchState.startX)
        const dy = Math.abs(this.touchState.lastY - this.touchState.startY)
        if (dx < this.MOVE_THRESHOLD && dy < this.MOVE_THRESHOLD) {
          this.sendLongPress(this.touchState.startX, this.touchState.startY)
        }
      }
    }, this.LONG_PRESS_THRESHOLD)
  }

  /** 鼠标移动 */
  handleMouseMove(e: MouseEvent, canvas: HTMLCanvasElement) {
    if (!this.touchState || !this.touchState.isTracking) return
    e.preventDefault()

    const { x, y } = this.mapToScreen(e.clientX, e.clientY, canvas)
    const dx = Math.abs(x - this.touchState.startX)
    const dy = Math.abs(y - this.touchState.startY)

    // 超过移动阈值，取消长按检测
    if (dx > this.MOVE_THRESHOLD || dy > this.MOVE_THRESHOLD) {
      this.cancelLongPress()
    }

    this.touchState.lastX = x
    this.touchState.lastY = y
  }

  /** 鼠标释放 */
  handleMouseUp(e: MouseEvent, canvas: HTMLCanvasElement) {
    if (!this.touchState || !this.touchState.isTracking) return
    e.preventDefault()

    this.cancelLongPress()

    const { x, y } = this.mapToScreen(e.clientX, e.clientY, canvas)
    const duration = Date.now() - this.touchState.startTime
    const dx = Math.abs(x - this.touchState.startX)
    const dy = Math.abs(y - this.touchState.startY)
    const distance = Math.sqrt(dx * dx + dy * dy)

    if (duration < this.LONG_PRESS_THRESHOLD && distance < this.MOVE_THRESHOLD) {
      // 点击
      this.sendTap(this.touchState.startX, this.touchState.startY)
    } else if (distance >= this.MOVE_THRESHOLD) {
      // 滑动
      this.sendSwipe(
        this.touchState.startX,
        this.touchState.startY,
        x, y,
        duration
      )
    }

    this.touchState = null
  }

  /** 触摸开始 */
  handleTouchStart(e: TouchEvent, canvas: HTMLCanvasElement) {
    e.preventDefault()
    if (e.touches.length !== 1) return

    const touch = e.touches[0]
    const { x, y } = this.mapToScreen(touch.clientX, touch.clientY, canvas)

    this.touchState = {
      startX: x,
      startY: y,
      startTime: Date.now(),
      lastX: x,
      lastY: y,
      isTracking: true,
    }

    this.longPressTimer = window.setTimeout(() => {
      if (this.touchState && this.touchState.isTracking) {
        const dx = Math.abs(this.touchState.lastX - this.touchState.startX)
        const dy = Math.abs(this.touchState.lastY - this.touchState.startY)
        if (dx < this.MOVE_THRESHOLD && dy < this.MOVE_THRESHOLD) {
          this.sendLongPress(this.touchState.startX, this.touchState.startY)
        }
      }
    }, this.LONG_PRESS_THRESHOLD)
  }

  /** 触摸移动 */
  handleTouchMove(e: TouchEvent, canvas: HTMLCanvasElement) {
    if (!this.touchState || !this.touchState.isTracking) return
    e.preventDefault()
    if (e.touches.length !== 1) return

    const touch = e.touches[0]
    const { x, y } = this.mapToScreen(touch.clientX, touch.clientY, canvas)
    const dx = Math.abs(x - this.touchState.startX)
    const dy = Math.abs(y - this.touchState.startY)

    if (dx > this.MOVE_THRESHOLD || dy > this.MOVE_THRESHOLD) {
      this.cancelLongPress()
    }

    this.touchState.lastX = x
    this.touchState.lastY = y
  }

  /** 触摸结束 */
  handleTouchEnd(e: TouchEvent, canvas: HTMLCanvasElement) {
    if (!this.touchState || !this.touchState.isTracking) return
    e.preventDefault()

    this.cancelLongPress()

    const touch = e.changedTouches[0]
    const { x, y } = this.mapToScreen(touch.clientX, touch.clientY, canvas)
    const duration = Date.now() - this.touchState.startTime
    const dx = Math.abs(x - this.touchState.startX)
    const dy = Math.abs(y - this.touchState.startY)
    const distance = Math.sqrt(dx * dx + dy * dy)

    if (duration < this.LONG_PRESS_THRESHOLD && distance < this.MOVE_THRESHOLD) {
      this.sendTap(this.touchState.startX, this.touchState.startY)
    } else if (distance >= this.MOVE_THRESHOLD) {
      this.sendSwipe(
        this.touchState.startX,
        this.touchState.startY,
        x, y,
        duration
      )
    }

    this.touchState = null
  }

  /** 发送按键事件 */
  sendKey(keycode: number) {
    this.ws.send({ action: 'key', keycode })
  }

  /** 发送文本输入 */
  sendText(text: string) {
    this.ws.send({ action: 'text', text })
  }

  /** 取消长按检测 */
  private cancelLongPress() {
    if (this.longPressTimer !== null) {
      clearTimeout(this.longPressTimer)
      this.longPressTimer = null
    }
  }

  /** 发送点击事件 */
  private sendTap(x: number, y: number) {
    this.ws.send({ action: 'touch', x, y })
  }

  /** 发送滑动事件 */
  private sendSwipe(x1: number, y1: number, x2: number, y2: number, duration: number) {
    this.ws.send({
      action: 'swipe',
      x1, y1, x2, y2,
      duration: Math.max(duration, 100),
    })
  }

  /** 发送长按事件（通过长时间滑动模拟） */
  private sendLongPress(x: number, y: number) {
    this.ws.send({
      action: 'swipe',
      x1: x, y1: y, x2: x, y2: y,
      duration: 1000,
    })
  }
}
