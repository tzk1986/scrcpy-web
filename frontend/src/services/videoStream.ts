/**
 * 视频流服务
 * ============
 *
 * 管理设备视频流的显示：
 *   - 通过周期性截屏实现"近实时"视频显示
 *   - 通过 WebSocket 接收/发送输入事件
 *   - 管理截屏定时器生命周期
 *
 * 当前策略：
 *   使用 adb screencap 周期性截取设备屏幕，
 *   渲染到 canvas 上。这是方案A（scrcpy.exe）的配套前端实现，
 *   提供约 2-5 FPS 的"近实时"视频显示。
 *
 * 未来升级：
 *   后续可升级为 H.264 流式传输（WebCodecs），实现真正的 30+ FPS 视频。
 *
 * 使用方式：
 *   const stream = new VideoStream(deviceId, canvas, ws)
 *   stream.start()
 *   // ... later
 *   stream.stop()
 */

import { api } from './api'
import type { WebSocketService } from './websocket'

export type VideoStreamState = 'idle' | 'streaming' | 'error' | 'stopped'

export interface VideoStreamStats {
  frameCount: number
  fps: number
  lastFrameTime: number
  state: VideoStreamState
  error: string | null
}

export class VideoStream {
  private deviceId: string
  private canvas: HTMLCanvasElement
  private timer: number | null = null
  private _state: VideoStreamState = 'idle'
  private _frameCount = 0
  private _lastFrameTime = 0
  private _fps = 0
  private _fpsCounter = 0
  private _fpsTimer: number | null = null
  private _error: string | null = null
  private refreshInterval: number
  private onStateChange?: (state: VideoStreamState) => void
  private onStatsUpdate?: (stats: VideoStreamStats) => void
  private _objectUrl: string | null = null
  /** 输入事件驱动截屏（方案 27）：截屏链路 dedounce 窗口 + 单飞标志 + 尾部补帧 */
  private readonly inputDebounceMs = 160
  private _debounceTimer: number | null = null
  private _inFlight = false
  private _pendingRefresh = false
  /** 方案 29：raw 链路不可用（校验失败/后端回退 PNG）后降级记忆，不再试 raw */
  private _rawUnsupported = false

  /**
   * @param deviceId - 设备 ADB 序列号
   * @param canvas - 用于渲染的 canvas 元素
   * @param ws - WebSocket 连接（用于输入事件）
   * @param refreshInterval - 截屏刷新间隔（毫秒），默认 1500ms（~0.7 FPS，适合当前截屏速度）
   */
  constructor(
    deviceId: string,
    canvas: HTMLCanvasElement,
    _ws: WebSocketService,
    refreshInterval = 1500
  ) {
    this.deviceId = deviceId
    this.canvas = canvas
    this.refreshInterval = refreshInterval
  }

  /** 当前状态 */
  get state(): VideoStreamState {
    return this._state
  }

  /** 获取当前统计信息 */
  get stats(): VideoStreamStats {
    return {
      frameCount: this._frameCount,
      fps: this._fps,
      lastFrameTime: this._lastFrameTime,
      state: this._state,
      error: this._error,
    }
  }

  /** 注册状态变化回调 */
  setStateChangeHandler(handler: (state: VideoStreamState) => void) {
    this.onStateChange = handler
  }

  /** 注册统计信息更新回调 */
  setStatsUpdateHandler(handler: (stats: VideoStreamStats) => void) {
    this.onStatsUpdate = handler
  }

  /** 启动视频流（开始周期性截屏） */
  async start() {
    if (this._state === 'streaming') return

    this._state = 'streaming'
    this._error = null
    this._frameCount = 0
    this._fpsCounter = 0
    this.onStateChange?.(this._state)

    // FPS 计算定时器（每秒更新一次）
    this._fpsTimer = window.setInterval(() => {
      this._fps = this._fpsCounter
      this._fpsCounter = 0
      this.onStatsUpdate?.(this.stats)
    }, 1000)

    // 立即获取第一帧（单飞入口）
    await this.doCapture()

    // 启动周期性截屏
    this.timer = window.setInterval(() => {
      if (this._state === 'streaming') {
        this.captureNow()
      }
    }, this.refreshInterval)
  }

  /**
   * 输入事件驱动的即时刷新（方案 27）。
   * debounce 合并 160ms 窗口内的连续输入；截屏进行中只标记待补，
   * 完成后自动补一张；刷新完成后周期轮询相位重新起算。
   */
  notifyInput(): void {
    if (this._state !== 'streaming') return
    if (this._debounceTimer !== null) {
      clearTimeout(this._debounceTimer)
    }
    this._debounceTimer = window.setTimeout(() => {
      this._debounceTimer = null
      this.captureNow()
    }, this.inputDebounceMs)
  }

  /** 截屏统一入口（单飞）：进行中不并发，只标记待补。 */
  private captureNow(): void {
    if (this._inFlight) {
      this._pendingRefresh = true
      return
    }
    void this.doCapture()
  }

  /** 截屏执行（含尾部补帧与相位重置）。 */
  private async doCapture(): Promise<void> {
    this._inFlight = true
    try {
      await this.captureFrame()
    } finally {
      this._inFlight = false
      if (this._pendingRefresh) {
        this._pendingRefresh = false
        void this.doCapture()
      } else if (this._state === 'streaming') {
        this.restartPollingPhase()
      }
    }
  }

  /** 周期轮询相位重置：从最近一次截屏完成时刻重新起算。 */
  private restartPollingPhase(): void {
    if (this.timer === null) return
    clearInterval(this.timer)
    this.timer = window.setInterval(() => {
      if (this._state === 'streaming') {
        this.captureNow()
      }
    }, this.refreshInterval)
  }

  /** 停止视频流 */
  stop() {
    if (this.timer !== null) {
      clearInterval(this.timer)
      this.timer = null
    }
    if (this._debounceTimer !== null) {
      clearTimeout(this._debounceTimer)
      this._debounceTimer = null
    }
    this._pendingRefresh = false
    if (this._fpsTimer !== null) {
      clearInterval(this._fpsTimer)
      this._fpsTimer = null
    }

    // 释放 ObjectURL
    if (this._objectUrl) {
      URL.revokeObjectURL(this._objectUrl)
      this._objectUrl = null
    }

    this._state = 'stopped'
    this.onStateChange?.(this._state)
  }

  /** 获取并渲染一帧 */
  private async captureFrame() {
    try {
      if (this._rawUnsupported) {
        // 降级记忆置位后：直接走既有 PNG 路径，不再试 raw
        const blob = await api.screenshot(this.deviceId)
        this.renderBlob(blob)
        this._countFrame()
        return
      }

      const { blob, format } = await api.screenshotRaw(this.deviceId)
      if (format === 'raw-rgba') {
        if (!(await this.renderRawFrame(blob))) {
          // 帧校验失败：丢弃该帧（不渲染不计数），置降级记忆，下周期起走 PNG
          this._rawUnsupported = true
          return
        }
        this._countFrame()
        return
      }
      // 后端已回退 PNG（如设备无 gzip）：既有渲染路径，并记住不再试 raw
      this.renderBlob(blob)
      this._rawUnsupported = true
      this._countFrame()
    } catch (e) {
      this._error = (e as Error).message
      this._state = 'error'
      this.onStateChange?.(this._state)
    }
  }

  /** 帧渲染成功后的统计计数与错误清除 */
  private _countFrame() {
    this._frameCount++
    this._fpsCounter++
    this._lastFrameTime = Date.now()
    this._error = null
  }

  /**
   * 解析并渲染 raw RGBA 帧（方案 29）。
   * screencap 帧头（LE uint32 序列）：w/h/fmt（+ 可选 colorspace），
   * 头长按 `byteLength - w*h*4 ∈ {12,16}` 反推（A11=16B / A7.1=12B 双态）。
   * 校验失败（fmt≠1 / 头长不符 / 尺寸越界）返回 false，不猜测式适配。
   */
  private async renderRawFrame(blob: Blob): Promise<boolean> {
    const buf = await blob.arrayBuffer()
    if (buf.byteLength < 12) return false

    const dv = new DataView(buf)
    const width = dv.getUint32(0, true)
    const height = dv.getUint32(4, true)
    const fmt = dv.getUint32(8, true)
    const headerSize = buf.byteLength - width * height * 4
    if (headerSize !== 12 && headerSize !== 16) return false
    if (fmt !== 1) return false
    if (width <= 0 || height <= 0 || width > 8192 || height > 8192) return false

    const ctx = this.canvas.getContext('2d')
    if (ctx) {
      if (this.canvas.width !== width || this.canvas.height !== height) {
        this.canvas.width = width
        this.canvas.height = height
        // 设置 CSS aspect-ratio 保持显示比例
        this.canvas.style.aspectRatio = `${width} / ${height}`
      }
      const pixels = new Uint8ClampedArray(buf, headerSize, width * height * 4)
      ctx.putImageData(new ImageData(pixels, width, height), 0, 0)
    }
    return true
  }

  /**
   * 将 Blob 渲染到 canvas。
   * 流程：Blob → ImageBitmap → canvas.drawImage
   * 使用 createImageBitmap 比 Image + ObjectURL 更高效。
   */
  private renderBlob(blob: Blob) {
    const ctx = this.canvas.getContext('2d')
    if (!ctx) return

    // 使用 createImageBitmap（比 Image 更高效，不需要 ObjectURL）
    createImageBitmap(blob).then((bitmap) => {
      // 根据图片尺寸调整 canvas（保持设备比例）
      if (this.canvas.width !== bitmap.width || this.canvas.height !== bitmap.height) {
        this.canvas.width = bitmap.width
        this.canvas.height = bitmap.height
        // 设置 CSS aspect-ratio 保持显示比例
        this.canvas.style.aspectRatio = `${bitmap.width} / ${bitmap.height}`
      }

      ctx.drawImage(bitmap, 0, 0)
      bitmap.close()
    }).catch(() => {
      // createImageBitmap 失败时降级为 Image + ObjectURL
      this.renderBlobFallback(blob)
    })
  }

  /** 降级渲染方案 */
  private renderBlobFallback(blob: Blob) {
    const ctx = this.canvas.getContext('2d')
    if (!ctx) return

    // 释放旧的 ObjectURL
    if (this._objectUrl) {
      URL.revokeObjectURL(this._objectUrl)
    }

    const url = URL.createObjectURL(blob)
    this._objectUrl = url

    const img = new Image()
    img.onload = () => {
      if (this.canvas.width !== img.width || this.canvas.height !== img.height) {
        this.canvas.width = img.width
        this.canvas.height = img.height
      }
      ctx.drawImage(img, 0, 0)
      URL.revokeObjectURL(url)
      if (this._objectUrl === url) {
        this._objectUrl = null
      }
    }
    img.src = url
  }
}
