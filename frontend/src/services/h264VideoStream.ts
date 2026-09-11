/**
 * H.264 视频流服务（WebCodecs）
 * =================================
 *
 * 通过 WebSocket 接收 H.264 码流，使用浏览器 WebCodecs VideoDecoder 解码，
 * 渲染到 canvas 上。
 *
 * 协议：
 *   服务端 → 客户端：
 *     - 配置消息（JSON）:
 *       { "type": "config", "codec": "avc1.42E01E",
 *         "width": 1080, "height": 1920, "description": "<hex SPS+PPS>" }
 *     - 视频帧（二进制）: H.264 NAL 单元（Annex B 格式，带起始码）
 *     - 错误（JSON）: { "type": "error", "message": "..." }
 *
 *   客户端 → 服务端：JSON 输入消息
 *     { "action": "touch", "x": 100, "y": 200 }
 *     { "action": "swipe", "x1": ..., "y1": ..., "x2": ..., "y2": ..., "duration": 300 }
 *     { "action": "key", "keycode": 4 }
 *     { "action": "text", "text": "hello" }
 *
 * 解码流程：
 *   1. 收到 config 消息 → 从 hex description 提取 SPS/PPS
 *   2. 转换为 AVCC 格式（avcC box）
 *   3. 创建 VideoDecoder 实例
 *   4. 收到二进制帧 → Annex B 转 AVCC → EncodedVideoChunk → decode()
 *   5. VideoDecoder output → VideoFrame → drawImage(canvas)
 */

import type { WebSocketService } from './websocket'

export type H264StreamState = 'idle' | 'configuring' | 'streaming' | 'error' | 'stopped'

export interface H264StreamStats {
  frameCount: number
  fps: number
  lastFrameTime: number
  state: H264StreamState
  error: string | null
  width: number
  height: number
}

export class H264VideoStream {
  private ws: WebSocketService
  private canvas: HTMLCanvasElement
  private decoder: VideoDecoder | null = null
  private _state: H264StreamState = 'idle'
  private _frameCount = 0
  private _lastFrameTime = 0
  private _fps = 0
  private _fpsCounter = 0
  private _fpsTimer: number | null = null
  private _error: string | null = null
  private _width = 0
  private _height = 0
  private onStateChange?: (state: H264StreamState) => void
  private onStatsUpdate?: (stats: H264StreamStats) => void
  // 保存最近的 SPS/PPS，用于重新初始化解码器（如需要）
  private spsData: Uint8Array | null = null
  private ppsData: Uint8Array | null = null

  constructor(ws: WebSocketService, canvas: HTMLCanvasElement) {
    this.ws = ws
    this.canvas = canvas
  }

  /** 当前状态 */
  get state(): H264StreamState {
    return this._state
  }

  /** 获取统计信息 */
  get stats(): H264StreamStats {
    return {
      frameCount: this._frameCount,
      fps: this._fps,
      lastFrameTime: this._lastFrameTime,
      state: this._state,
      error: this._error,
      width: this._width,
      height: this._height,
    }
  }

  /** 注册状态变化回调 */
  setStateChangeHandler(handler: (state: H264StreamState) => void) {
    this.onStateChange = handler
  }

  /** 注册统计信息更新回调 */
  setStatsUpdateHandler(handler: (stats: H264StreamStats) => void) {
    this.onStatsUpdate = handler
  }

  /**
   * 启动视频流。
   * 注册 WebSocket 消息处理器，等待 config 消息后初始化解码器。
   */
  start() {
    if (this._state === 'streaming' || this._state === 'configuring') return

    this._state = 'configuring'
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

    // 注册消息处理器
    this.ws.setMessageHandler((data: unknown) => {
      console.log('[H264] Message received:', typeof data, data instanceof ArrayBuffer ? `${(data as ArrayBuffer).byteLength} bytes` : data)
      if (data instanceof ArrayBuffer) {
        this.handleBinaryFrame(new Uint8Array(data))
      } else if (typeof data === 'string') {
        try {
          const msg = JSON.parse(data)
          console.log('[H264] JSON message:', msg.type, msg)
          this.handleJsonMessage(msg)
        } catch {
          console.warn('[H264] Failed to parse WebSocket message:', data)
        }
      } else if (data instanceof Blob) {
        // 如果 WebSocket 的 binaryType 不是 arraybuffer，会收到 Blob
        data.arrayBuffer().then(buf => {
          this.handleBinaryFrame(new Uint8Array(buf))
        })
      }
    })

    // 确保 WebSocket 的 binaryType 是 arraybuffer
    this.setBinaryType()
  }

  /** 停止视频流，释放解码器资源 */
  stop() {
    if (this._fpsTimer !== null) {
      clearInterval(this._fpsTimer)
      this._fpsTimer = null
    }

    if (this.decoder) {
      try {
        this.decoder.close()
      } catch {
        // 忽略关闭错误
      }
      this.decoder = null
    }

    this.spsData = null
    this.ppsData = null
    this._state = 'stopped'
    this.onStateChange?.(this._state)
  }

  /** 设置 WebSocket 的 binaryType 为 arraybuffer */
  private setBinaryType() {
    // WebSocketService 内部持有 WebSocket 实例
    // 通过类型断言访问内部 ws
    const wsInternal = (this.ws as unknown as { ws: WebSocket | null }).ws
    if (wsInternal) {
      wsInternal.binaryType = 'arraybuffer'
    }
  }

  /** 处理 JSON 控制消息 */
  private handleJsonMessage(msg: Record<string, unknown>) {
    const type = msg.type as string

    if (type === 'config') {
      const codec = msg.codec as string || 'avc1.42E01E'
      const descriptionHex = msg.description as string

      console.log('[H264] Config received:', { codec, descriptionLength: descriptionHex?.length })

      if (!descriptionHex) {
        this.setError('Missing codec description in config')
        return
      }

      // 解析 hex 格式的 SPS+PPS（Annex B 格式，带起始码）
      const annexBData = this.hexToUint8Array(descriptionHex)
      console.log('[H264] AnnexB data length:', annexBData.length, 'bytes')

      // 提取 SPS 和 PPS NAL 单元
      const nalus = this.extractNalus(annexBData)
      console.log('[H264] Extracted NALUs:', nalus.length)
      for (const nalu of nalus) {
        const naluType = nalu.data[0] & 0x1F
        console.log('[H264] NALU type:', naluType, 'length:', nalu.data.length)
        if (naluType === 7) {
          // SPS
          this.spsData = nalu.raw  // 包含起始码
        } else if (naluType === 8) {
          // PPS
          this.ppsData = nalu.raw  // 包含起始码
        }
      }

      if (!this.spsData || !this.ppsData) {
        this.setError(`Failed to extract SPS/PPS from description. SPS: ${!!this.spsData}, PPS: ${!!this.ppsData}`)
        return
      }

      // 创建 avcC 格式的 description
      const avccDescription = this.buildAvccDescription(this.spsData, this.ppsData)
      console.log('[H264] AVCC description size:', avccDescription.byteLength)

      // 初始化 VideoDecoder
      this.initDecoder(codec, avccDescription)

    } else if (type === 'error') {
      this.setError(msg.message as string || 'Unknown server error')
    }
  }

  /** 处理二进制视频帧 */
  private handleBinaryFrame(data: Uint8Array) {
    // 先尝试解析为 JSON（config 消息可能被作为 ArrayBuffer 接收）
    try {
      const text = new TextDecoder().decode(data)
      const msg = JSON.parse(text)
      if (msg.type === 'config' || msg.type === 'error') {
        console.log('[H264] JSON message from binary:', msg.type)
        this.handleJsonMessage(msg)
        return
      }
    } catch {
      // 不是 JSON，当作二进制帧处理
    }

    if (!this.decoder || this.decoder.state !== 'configured') {
      console.log('[H264] Frame dropped: decoder not ready, state:', this.decoder?.state)
      return
    }
    console.log('[H264] Decoding frame:', data.length, 'bytes')

    // 将 Annex B 帧转换为 AVCC 格式
    const avccData = this.annexBToAvcc(data)

    // 创建 EncodedVideoChunk
    // 判断是否为关键帧：第一个 NAL 的类型为 IDR (5)
    const isKeyFrame = this.isKeyFrame(data)

    const chunk = new EncodedVideoChunk({
      type: isKeyFrame ? 'key' : 'delta',
      timestamp: 0,  // 服务端未提供 PTS，用 0 让解码器自动处理
      data: avccData,
    })

    try {
      this.decoder.decode(chunk)
      this._frameCount++
      this._fpsCounter++
      this._lastFrameTime = Date.now()
    } catch (e) {
      console.error('Failed to decode frame:', e)
    }
  }

  /** 初始化 VideoDecoder */
  private initDecoder(codec: string, description: ArrayBuffer) {
    // 关闭旧的解码器
    if (this.decoder) {
      try {
        this.decoder.close()
      } catch {
        // 忽略
      }
    }

    const ctx = this.canvas.getContext('2d')
    if (!ctx) {
      this.setError('Failed to get canvas 2d context')
      return
    }

    this.decoder = new VideoDecoder({
      output: (frame: VideoFrame) => {
        // 更新 canvas 尺寸（首次或变化时）
        if (this.canvas.width !== frame.displayWidth ||
            this.canvas.height !== frame.displayHeight) {
          this.canvas.width = frame.displayWidth
          this.canvas.height = frame.displayHeight
          this._width = frame.displayWidth
          this._height = frame.displayHeight
          // 设置 CSS aspect-ratio 保持显示比例
          this.canvas.style.aspectRatio = `${frame.displayWidth} / ${frame.displayHeight}`
        }
        ctx.drawImage(frame, 0, 0)
        frame.close()

        // 首次输出帧时标记为 streaming
        if (this._state !== 'streaming') {
          this._state = 'streaming'
          this.onStateChange?.(this._state)
        }
      },
      error: (e: DOMException) => {
        console.error('VideoDecoder error:', e)
        this.setError(`Decoder error: ${e.message}`)
      },
    })

    console.log('[H264] Configuring decoder:', codec)
    this.decoder.configure({
      codec: codec,
      description: description,
      optimizeForLatency: true,
    })
    console.log('[H264] Decoder state after configure:', this.decoder.state)
  }

  /** 设置错误状态 */
  private setError(message: string) {
    this._error = message
    this._state = 'error'
    this.onStateChange?.(this._state)
  }

  // ===== Annex B / AVCC 转换 =====

  /** 将 hex 字符串转为 Uint8Array */
  private hexToUint8Array(hex: string): Uint8Array {
    const bytes = new Uint8Array(hex.length / 2)
    for (let i = 0; i < hex.length; i += 2) {
      bytes[i / 2] = parseInt(hex.substring(i, i + 2), 16)
    }
    return bytes
  }

  /** 从 Annex B 字节流中提取所有 NAL 单元 */
  private extractNalus(data: Uint8Array): Array<{ raw: Uint8Array; data: Uint8Array }> {
    const nalus: Array<{ raw: Uint8Array; data: Uint8Array }> = []
    let i = 0

    while (i < data.length - 3) {
      // 查找起始码
      let startLen = 0
      if (data[i] === 0 && data[i + 1] === 0 && data[i + 2] === 1) {
        startLen = 3
      } else if (i < data.length - 3 && data[i] === 0 && data[i + 1] === 0 &&
                 data[i + 2] === 0 && data[i + 3] === 1) {
        startLen = 4
      }

      if (startLen === 0) {
        i++
        continue
      }

      const naluStart = i + startLen
      // 查找下一个起始码
      let naluEnd = data.length
      for (let j = naluStart + 1; j < data.length - 2; j++) {
        if (data[j] === 0 && data[j + 1] === 0) {
          if (data[j + 2] === 1 || (j < data.length - 3 && data[j + 2] === 0 && data[j + 3] === 1)) {
            naluEnd = j
            break
          }
        }
      }

      nalus.push({
        raw: data.slice(i, naluEnd),       // 包含起始码
        data: data.slice(naluStart, naluEnd), // 不含起始码
      })

      i = naluEnd
    }

    return nalus
  }

  /**
   * 构建 AVCC avcC 格式的 description（用于 VideoDecoder.configure）。
   *
   * avcC 格式：
   *   version(1) + profile(1) + compatibility(1) + level(1)
   *   + lengthSizeMinusOne(1) + SPS count + SPS data
   *   + PPS count + PPS data
   */
  private buildAvccDescription(spsWithStartCode: Uint8Array, ppsWithStartCode: Uint8Array): ArrayBuffer {
    // 去掉起始码，获取纯 NALU 数据
    const sps = this.stripStartCode(spsWithStartCode)
    const pps = this.stripStartCode(ppsWithStartCode)

    // 从 SPS 提取 profile/level（SPS 第 1 字节: profile_idc, 第 2 字节: constraint flags, 第 3 字节: level_idc）
    const profileIdc = sps[0]
    const compatibility = sps[1]
    const levelIdc = sps[2]

    // 计算总大小
    const totalSize = 6 + 2 + sps.length + 2 + pps.length
    const buffer = new ArrayBuffer(totalSize)
    const view = new DataView(buffer)
    const bytes = new Uint8Array(buffer)

    let offset = 0

    // version (1 byte)
    view.setUint8(offset++, 1)
    // profile (1 byte)
    view.setUint8(offset++, profileIdc)
    // compatibility (1 byte)
    view.setUint8(offset++, compatibility)
    // level (1 byte)
    view.setUint8(offset++, levelIdc)
    // lengthSizeMinusOne (1 byte): NAL 长度前缀为 4 字节 → 值 = 3
    view.setUint8(offset++, 0xFF)
    // numSPS (1 byte)
    view.setUint8(offset++, 0xE1)
    // SPS length (2 bytes)
    view.setUint16(offset, sps.length)
    offset += 2
    // SPS data
    bytes.set(sps, offset)
    offset += sps.length
    // numPPS (1 byte)
    view.setUint8(offset++, 1)
    // PPS length (2 bytes)
    view.setUint16(offset, pps.length)
    offset += 2
    // PPS data
    bytes.set(pps, offset)

    return buffer
  }

  /** 去掉 NALU 的起始码（3 字节或 4 字节） */
  private stripStartCode(nalu: Uint8Array): Uint8Array {
    if (nalu.length >= 4 && nalu[0] === 0 && nalu[1] === 0 && nalu[2] === 0 && nalu[3] === 1) {
      return nalu.slice(4)
    }
    if (nalu.length >= 3 && nalu[0] === 0 && nalu[1] === 0 && nalu[2] === 1) {
      return nalu.slice(3)
    }
    return nalu
  }

  /**
   * 将 Annex B 帧转换为 AVCC 格式。
   * Annex B: [start_code] NALU [start_code] NALU ...
   * AVCC:    [4-byte length] NALU [4-byte length] NALU ...
   */
  private annexBToAvcc(annexB: Uint8Array): ArrayBuffer {
    const nalus = this.extractNalus(annexB)
    if (nalus.length === 0) {
      return new ArrayBuffer(0)
    }

    // 计算总大小：每个 NALU 加 4 字节长度前缀
    let totalSize = 0
    for (const nalu of nalus) {
      totalSize += 4 + nalu.data.length
    }

    const buffer = new ArrayBuffer(totalSize)
    const view = new DataView(buffer)
    const bytes = new Uint8Array(buffer)
    let offset = 0

    for (const nalu of nalus) {
      view.setUint32(offset, nalu.data.length)
      offset += 4
      bytes.set(nalu.data, offset)
      offset += nalu.data.length
    }

    return buffer
  }

  /** 判断 Annex B 帧是否为关键帧（包含 IDR NALU） */
  private isKeyFrame(annexB: Uint8Array): boolean {
    const nalus = this.extractNalus(annexB)
    for (const nalu of nalus) {
      const naluType = nalu.data[0] & 0x1F
      if (naluType === 5) {
        // IDR
        return true
      }
    }
    return false
  }
}
