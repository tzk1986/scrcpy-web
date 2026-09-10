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

    // 立即获取第一帧
    await this.captureFrame()

    // 启动周期性截屏
    this.timer = window.setInterval(() => {
      if (this._state === 'streaming') {
        this.captureFrame()
      }
    }, this.refreshInterval)
  }

  /** 停止视频流 */
  stop() {
    if (this.timer !== null) {
      clearInterval(this.timer)
      this.timer = null
    }
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
      const blob = await api.screenshot(this.deviceId)
      this.renderBlob(blob)

      this._frameCount++
      this._fpsCounter++
      this._lastFrameTime = Date.now()
      this._error = null
    } catch (e) {
      this._error = (e as Error).message
      this._state = 'error'
      this.onStateChange?.(this._state)
    }
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
