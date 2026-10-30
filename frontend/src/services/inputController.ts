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
 *
 * 方案 54 新增：物理键盘镜像（handleKeyDown/Up + blur 补 up）、滚轮注入
 * （handleWheel）、IME 组合输入（composition 期抑制 keydown 防双写）、
 * 剪贴板双向（sendClipboardSet / requestClipboard + handleClipboardMessage）。
 */

import type { WebSocketService } from './websocket'
import {
  isLetterCode,
  keycodeForCode,
  metaStateFromEvent,
} from './keycodeMap'

/** INJECT_TEXT 服务端单条上限（scrcpy ControlMessageReader v5.0.1 实证，超限断控制连接） */
export const INJECT_TEXT_MAX_LENGTH = 300

/** 按 UTF-16 码元切段（与 Java String.length 同口径），代理对不拆半 */
export function splitTextChunks(
  text: string,
  limit = INJECT_TEXT_MAX_LENGTH,
): string[] {
  const chunks: string[] = []
  let rest = text
  while (rest.length > limit) {
    let cut = limit
    const prev = rest.charCodeAt(cut - 1)
    if (prev >= 0xd800 && prev <= 0xdbff) cut -= 1  // 高代理对尾部：回退一格
    chunks.push(rest.slice(0, cut))
    rest = rest.slice(cut)
  }
  if (rest) chunks.push(rest)
  return chunks
}

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
  private touchState: TouchState | null = null
  private longPressTimer: number | null = null
  /** 本次手势是否已触发长按：触发后释放不再注入 swipe（方案 51 F1-03，
   *  修复「长按后移出再释放」长按+滑动双手势并存） */
  private longPressFired = false
  private readonly LONG_PRESS_THRESHOLD = 500  // ms
  private readonly MOVE_THRESHOLD = 20         // px
  /** 是否启用输入处理（Page Visibility 隐藏时停用，方案 17 实施项 4） */
  private enabled = true
  /** 输入发送后回调（方案 27：截图模式事件驱动截屏触发点） */
  private onInputSend?: () => void
  /** 已按下待抬起的物理键（code→keycode）：blur/停用补发 up 防卡键（方案 54 G1） */
  private pressedKeycodes = new Map<string, number>()
  /** IME 组合输入进行中：期间抑制 keydown 镜像防字母双写（方案 54 G2） */
  private composing = false
  /** D2 拍板 A：字母注入路径开关（默认 keyevent；开=text 注入），持久化在 VideoPlayer 层 */
  private preferText = false
  /** clipboard_get 应答关联（同一时刻仅一个在途请求：UI 按钮触发） */
  private clipboardPending: ((text: string | null) => void) | null = null

  /**
   * @param ws - WebSocket 连接
   * @param _deviceWidth - 设备屏幕宽度（像素），保留用于向后兼容，实际坐标映射基于 canvas 尺寸
   * @param _deviceHeight - 设备屏幕高度（像素），保留用于向后兼容，实际坐标映射基于 canvas 尺寸
   */
  constructor(ws: WebSocketService, _deviceWidth: number, _deviceHeight: number) {
    this.ws = ws
  }

  /** 更新设备分辨率（保留用于向后兼容） */
  setDeviceResolution(_width: number, _height: number) {
    // 坐标映射现在基于 canvas 的实际像素尺寸，无需保存设备分辨率
  }

  /** 设置输入发送回调（每次 ws.send 输入事件后触发）。 */
  setInputHandler(handler: () => void) {
    this.onInputSend = handler
  }

  /**
   * 启用/停用输入处理（Page Visibility 降载，方案 17 实施项 4）。
   * 停用期间指针事件处理函数直接返回，并取消挂起的长按定时器；
   * 方案 54：tab 隐藏时同步补发所有在按下键的 up（防回切后卡键）。
   */
  setEnabled(value: boolean) {
    this.enabled = value
    if (!value) {
      this.cancelLongPress()
      this.releaseAllKeys()
    }
  }

  /**
   * 将浏览器坐标映射到设备坐标。
   *
   * 参考 scrcpy 源码实现（app/src/input_manager.c + screen.h）：
   * 1. 获取鼠标在 canvas 内的位置（CSS 像素）
   * 2. 考虑 HiDPI（devicePixelRatio）
   * 3. 处理视频 letterboxing（黑边）
   * 4. 基于**视频/截图的实际尺寸**映射到设备坐标
   *
   * @param clientX - 浏览器 clientX
   * @param clientY - 浏览器 clientY
   * @param canvas - canvas DOM 元素
   * @returns 设备坐标 { x, y }
   */
  mapToScreen(clientX: number, clientY: number, canvas: HTMLCanvasElement): { x: number; y: number } {
    const rect = canvas.getBoundingClientRect()

    // canvas 实际像素尺寸（考虑 HiDPI）
    // canvas.width/height 是实际像素，rect.width/height 是 CSS 像素
    const canvasPixelWidth = canvas.width
    const canvasPixelHeight = canvas.height

    // 计算视频在 canvas 中的实际显示区域（处理 letterboxing）
    // 视频纵横比基于 canvas 像素尺寸
    const videoAspect = canvasPixelWidth / canvasPixelHeight
    const containerAspect = rect.width / rect.height

    let displayWidth: number
    let displayHeight: number
    let offsetX: number
    let offsetY: number

    if (videoAspect > containerAspect) {
      // 视频宽度填满容器，上下有黑边
      displayWidth = rect.width
      displayHeight = rect.width / videoAspect
      offsetX = 0
      offsetY = (rect.height - displayHeight) / 2
    } else {
      // 视频高度填满容器，左右有黑边
      displayHeight = rect.height
      displayWidth = rect.height * videoAspect
      offsetX = (rect.width - displayWidth) / 2
      offsetY = 0
    }

    // 计算点击在视频区域内的相对位置（0-1）
    const relX = (clientX - rect.left - offsetX) / displayWidth
    const relY = (clientY - rect.top - offsetY) / displayHeight

    // 边界检查
    const clampedX = Math.max(0, Math.min(1, relX))
    const clampedY = Math.max(0, Math.min(1, relY))

    // 映射到设备坐标
    // 使用 canvas 像素尺寸作为参考（这是截图/视频帧的实际尺寸）
    return {
      x: Math.round(clampedX * canvasPixelWidth),
      y: Math.round(clampedY * canvasPixelHeight),
    }
  }

  /** 鼠标按下 */
  handleMouseDown(e: MouseEvent, canvas: HTMLCanvasElement) {
    if (!this.enabled) return
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
    this.longPressFired = false

    // 长按检测
    this.longPressTimer = window.setTimeout(() => {
      if (this.touchState && this.touchState.isTracking) {
        const dx = Math.abs(this.touchState.lastX - this.touchState.startX)
        const dy = Math.abs(this.touchState.lastY - this.touchState.startY)
        if (dx < this.MOVE_THRESHOLD && dy < this.MOVE_THRESHOLD) {
          this.longPressFired = true
          this.sendLongPress(this.touchState.startX, this.touchState.startY)
        }
      }
    }, this.LONG_PRESS_THRESHOLD)
  }

  /** 鼠标移动 */
  handleMouseMove(e: MouseEvent, canvas: HTMLCanvasElement) {
    if (!this.enabled) return
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
    if (!this.enabled) return
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
    } else if (distance >= this.MOVE_THRESHOLD && !this.longPressFired) {
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
    if (!this.enabled) return
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
    this.longPressFired = false

    this.longPressTimer = window.setTimeout(() => {
      if (this.touchState && this.touchState.isTracking) {
        const dx = Math.abs(this.touchState.lastX - this.touchState.startX)
        const dy = Math.abs(this.touchState.lastY - this.touchState.startY)
        if (dx < this.MOVE_THRESHOLD && dy < this.MOVE_THRESHOLD) {
          this.longPressFired = true
          this.sendLongPress(this.touchState.startX, this.touchState.startY)
        }
      }
    }, this.LONG_PRESS_THRESHOLD)
  }

  /** 触摸移动 */
  handleTouchMove(e: TouchEvent, canvas: HTMLCanvasElement) {
    if (!this.enabled) return
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
    if (!this.enabled) return
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
    } else if (distance >= this.MOVE_THRESHOLD && !this.longPressFired) {
      this.sendSwipe(
        this.touchState.startX,
        this.touchState.startY,
        x, y,
        duration
      )
    }

    this.touchState = null
  }

  /**
   * 发送按键事件。
   * 无 opts（4 虚拟键等旧调用）：维持 DOWN+UP 成对的旧协议格式；
   * opts.down 显式给出：单发按下/抬起，透传 repeat/meta（方案 54 G1）。
   */
  sendKey(keycode: number, opts?: { down?: boolean; repeat?: number; meta?: number }) {
    if (opts?.down === undefined) {
      this.ws.send({ action: 'key', keycode })
    } else {
      this.ws.send({
        action: 'key',
        keycode,
        down: opts.down,
        repeat: opts.repeat ?? 0,
        meta: opts.meta ?? 0,
      })
    }
    this.onInputSend?.()
  }

  /** 发送文本输入（超 300 字符自动分段，防服务端协议校验断控，方案 54 G2） */
  sendText(text: string) {
    if (!text) return
    for (const chunk of splitTextChunks(text)) {
      this.ws.send({ action: 'text', text: chunk })
    }
    this.onInputSend?.()
  }

  // -------------------------------------------------------------------------
  // 物理键盘镜像（方案 54 G1）
  // -------------------------------------------------------------------------

  /** D2：字母注入路径开关（true=字母走 text 注入，默认 false=keyevent） */
  setPreferText(value: boolean) {
    this.preferText = value
  }

  /** IME 组合输入状态（compositionstart/end 挂接） */
  setComposing(value: boolean) {
    this.composing = value
  }

  /**
   * keydown 镜像：composition 期间与 IME 处理中键（key=Process）抑制；
   * 表内键单发 down（repeat 透传，设备 sdk 模式原生长按重复）；
   * 表外键（未知组合）忽略不 preventDefault，交回浏览器。
   */
  handleKeyDown(e: KeyboardEvent) {
    if (!this.enabled) return
    if (this.composing || e.key === 'Process') return
    const code = e.code
    if (
      this.preferText && isLetterCode(code) &&
      !e.ctrlKey && !e.altKey && !e.metaKey
    ) {
      if (!e.repeat) this.sendText(e.key)
      return
    }
    const keycode = keycodeForCode(code)
    if (keycode === null) return
    this.pressedKeycodes.set(code, keycode)
    this.sendKey(keycode, {
      down: true,
      repeat: e.repeat ? 1 : 0,
      meta: metaStateFromEvent(e),
    })
  }

  /** keyup 镜像：仅补发曾按下过的键（prefer-text 路径的字母无按下态不补） */
  handleKeyUp(e: KeyboardEvent) {
    if (!this.enabled) return
    const keycode = this.pressedKeycodes.get(e.code)
    if (keycode === undefined) return
    this.pressedKeycodes.delete(e.code)
    this.sendKey(keycode, { down: false, meta: metaStateFromEvent(e) })
  }

  /** 失焦/退出捕获/隐藏：所有在按下键补发 up 防卡键 */
  releaseAllKeys() {
    for (const [code, keycode] of [...this.pressedKeycodes]) {
      this.pressedKeycodes.delete(code)
      this.sendKey(keycode, { down: false, meta: 0 })
    }
  }

  /**
   * IME 确认整段上屏（compositionend）：结束组合态并注入文本。
   * 字母双写由 composing 抑制窗避免（keydown 在 start~end 期间被忽略）。
   */
  handleCompositionEnd(text: string) {
    this.composing = false
    if (text) this.sendText(text)
  }

  // -------------------------------------------------------------------------
  // 滚轮注入（方案 54）
  // -------------------------------------------------------------------------

  /** wheel → scroll（±1 档位）；纯横向滚（如触控板）也支持 */
  handleWheel(e: WheelEvent, canvas: HTMLCanvasElement) {
    if (!this.enabled) return
    const v = Math.sign(e.deltaY)
    const h = Math.sign(e.deltaX)
    if (v === 0 && h === 0) return
    e.preventDefault?.()
    const { x, y } = this.mapToScreen(e.clientX, e.clientY, canvas)
    this.ws.send({ action: 'scroll', x, y, h_scroll: h, v_scroll: v })
    this.onInputSend?.()
  }

  // -------------------------------------------------------------------------
  // 剪贴板双向（方案 54 G3）
  // -------------------------------------------------------------------------

  /** 浏览器 → 设备：写剪贴板；paste=true 设备端直接粘贴入焦点框（中文主路径） */
  sendClipboardSet(text: string, paste = false) {
    this.ws.send({ action: 'clipboard_set', text, paste })
    this.onInputSend?.()
  }

  /** 方案 55：设备→PC 单向镜像开关（会话级、默认关；后端幂等开停轮询 task） */
  sendClipboardMirror(enabled: boolean) {
    this.ws.send({ action: 'clipboard_mirror', enabled })
  }

  /**
   * 设备 → 浏览器：拉取剪贴板（copyKey=1 先注入复制键捕获选中文本）。
   * 应答经 handleClipboardMessage 关联；后端 3s 超时/失败 resolve(null)。
   * 新请求会丢弃旧的未决请求（resolve null）。
   */
  requestClipboard(copyKey = 0): Promise<string | null> {
    return new Promise((resolve) => {
      if (this.clipboardPending) this.clipboardPending(null)
      this.clipboardPending = resolve
      this.ws.send({ action: 'clipboard_get', copy_key: copyKey })
    })
  }

  /** WS 服务端消息分发入口：消费 clipboard/clipboard_error；返回是否已处理 */
  handleClipboardMessage(msg: { type: string; text?: string }): boolean {
    if (msg.type !== 'clipboard' && msg.type !== 'clipboard_error') return false
    const resolve = this.clipboardPending
    this.clipboardPending = null
    if (resolve) resolve(msg.type === 'clipboard' ? msg.text ?? '' : null)
    return true
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
    this.onInputSend?.()
  }

  /** 发送滑动事件 */
  private sendSwipe(x1: number, y1: number, x2: number, y2: number, duration: number) {
    this.ws.send({
      action: 'swipe',
      x1, y1, x2, y2,
      duration: Math.max(duration, 100),
    })
    this.onInputSend?.()
  }

  /** 发送长按事件（后端映射为 DOWN/保持/UP，adb 回退路径用同点 swipe 模拟） */
  private sendLongPress(x: number, y: number) {
    this.ws.send({
      action: 'long_press',
      x, y,
      duration: 1000,
    })
    this.onInputSend?.()
  }
}
