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
 *       { "type": "config", "codec": "avc1.XXYYZZ",
 *         "width": 1280, "height": 720, "description": "<hex SPS+PPS>" }
 *     - 视频帧（二进制）: H.264 NAL 单元（Annex B 格式，带起始码）
 *     - 错误（JSON）: { "type": "error", "message": "..." }
 *
 *   客户端 → 服务端：JSON 输入消息
 *
 * 解码流程：
 *   1. 收到 config 消息 → 从 hex description 提取 SPS/PPS
 *   2. 构建 AVCC 格式（avcC box）description
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
  private _decodedCount = 0
  private _lastFrameTime = 0
  private _fps = 0
  private _fpsCounter = 0
  private _fpsTimer: number | null = null
  private _error: string | null = null
  private _width = 0
  private _height = 0
  private onStateChange?: (state: H264StreamState) => void
  private onStatsUpdate?: (stats: H264StreamStats) => void
  // 保存最近的 SPS/PPS，用于重新初始化解码器
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
    this._decodedCount = 0
    this._fpsCounter = 0
    this.onStateChange?.(this._state)

    // FPS 计算定时器（每秒更新一次）
    this._fpsTimer = window.setInterval(() => {
      this._fps = this._fpsCounter
      this._fpsCounter = 0
      this.onStatsUpdate?.(this.stats)
    }, 1000)

    // 重要：先设置 binaryType，再注册消息处理器
    // 确保二进制数据以 ArrayBuffer 形式接收
    this.setBinaryType()

    // 注册消息处理器
    this.ws.setMessageHandler((data: unknown) => {
      if (data instanceof ArrayBuffer) {
        console.log('[H264] Binary frame received:', data.byteLength, 'bytes')
        this.handleBinaryFrame(new Uint8Array(data))
      } else if (typeof data === 'string') {
        console.log('[H264] Text message received:', data.substring(0, 200))
        try {
          const msg = JSON.parse(data)
          this.handleJsonMessage(msg)
        } catch {
          console.warn('[H264] Failed to parse text message:', data)
        }
      } else if (data instanceof Blob) {
        console.log('[H264] Blob frame received:', data.size, 'bytes')
        data.arrayBuffer().then(buf => {
          this.handleBinaryFrame(new Uint8Array(buf))
        })
      } else {
        console.warn('[H264] Unknown message type:', typeof data, data)
      }
    })

    console.log('[H264] Stream started, waiting for config...')
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
    const wsInternal = (this.ws as unknown as { ws: WebSocket | null }).ws
    if (wsInternal) {
      wsInternal.binaryType = 'arraybuffer'
      console.log('[H264] WebSocket binaryType set to arraybuffer')
    } else {
      console.warn('[H264] WebSocket not available, binaryType not set')
    }
  }

  /** 处理 JSON 控制消息 */
  private handleJsonMessage(msg: Record<string, unknown>) {
    const type = msg.type as string
    console.log('[H264] JSON message type:', type, msg)

    if (type === 'config') {
      const codec = msg.codec as string || 'avc1.42E01E'
      const descriptionHex = msg.description as string
      const width = msg.width as number || 0
      const height = msg.height as number || 0

      console.log('[H264] Config received:', {
        codec,
        descriptionLength: descriptionHex?.length,
        width,
        height,
      })

      if (!descriptionHex) {
        this.setError('Missing codec description in config')
        return
      }

      // 解析 hex 格式的 SPS+PPS（Annex B 格式，带起始码）
      const annexBData = this.hexToUint8Array(descriptionHex)
      console.log('[H264] AnnexB data hex (first 40 bytes):',
        Array.from(annexBData.slice(0, 40)).map(b => b.toString(16).padStart(2, '0')).join(' '))

      // 提取 SPS 和 PPS NAL 单元
      const nalus = this.extractNalus(annexBData)
      console.log('[H264] Extracted NALUs:', nalus.length)
      for (const nalu of nalus) {
        const naluType = nalu.data[0] & 0x1F
        console.log('[H264] NALU type:', naluType,
          'length (with NAL header):', nalu.data.length,
          'first bytes:', Array.from(nalu.data.slice(0, 6)).map(b => b.toString(16).padStart(2, '0')).join(' '))
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
      if (avccDescription.byteLength === 0) {
        this.setError('Failed to build AVCC description')
        return
      }
      console.log('[H264] AVCC description hex:',
        Array.from(new Uint8Array(avccDescription)).map(b => b.toString(16).padStart(2, '0')).join(' '))
      console.log('[H264] AVCC description size:', avccDescription.byteLength)

      // 关键：收到 config 后立即设置 canvas 尺寸为设备分辨率
      // 这样 InputController 的坐标映射从一开始就使用正确的设备坐标
      // 而不是默认的 1080x1920（方向可能错误）
      if (width > 0 && height > 0) {
        this.canvas.width = width
        this.canvas.height = height
        this.canvas.style.aspectRatio = `${width} / ${height}`
        this._width = width
        this._height = height
        console.log('[H264] Canvas initialized to device resolution:', width, 'x', height)
      }

      // 初始化 VideoDecoder
      this.initDecoder(codec, avccDescription, width, height)

    } else if (type === 'error') {
      this.setError(msg.message as string || 'Unknown server error')
    }
  }

  /** 处理二进制视频帧 */
  private handleBinaryFrame(data: Uint8Array) {
    // 先尝试解析为 JSON（config 消息可能被作为 ArrayBuffer 接收）
    try {
      const text = new TextDecoder().decode(data.slice(0, 100))
      if (text.startsWith('{')) {
        const msg = JSON.parse(text)
        if (msg.type === 'config' || msg.type === 'error') {
          console.log('[H264] JSON message from binary:', msg.type)
          this.handleJsonMessage(msg)
          return
        }
      }
    } catch {
      // 不是 JSON，当作二进制帧处理
    }

    if (!this.decoder || this.decoder.state !== 'configured') {
      console.warn('[H264] Frame dropped: decoder not ready, state:', this.decoder?.state,
        'decoder exists:', !!this.decoder)
      return
    }

    this._frameCount++

    // 将 Annex B 帧转换为 AVCC 格式
    const avccData = this.annexBToAvcc(data)

    // 判断是否为关键帧：第一个 NAL 的类型为 IDR (5)
    const isKeyFrame = this.isKeyFrame(data)

    if (this._frameCount <= 3) {
      const nalus = this.extractNalus(data)
      console.log('[H264] Frame', this._frameCount, ':',
        'raw size:', data.length,
        'avcc size:', avccData.byteLength,
        'isKeyFrame:', isKeyFrame,
        'NALUs:', nalus.map(n => `type=${n.data[0] & 0x1F},len=${n.data.length}`))
    }

    const chunk = new EncodedVideoChunk({
      type: isKeyFrame ? 'key' : 'delta',
      timestamp: 0,  // 服务端未提供 PTS，用 0 让解码器自动处理
      data: avccData,
    })

    try {
      this.decoder.decode(chunk)
      this._decodedCount++
      this._fpsCounter++
      this._lastFrameTime = Date.now()
    } catch (e) {
      console.error('[H264] Decode failed for frame', this._frameCount, ':', e)
    }
  }

  /** 初始化 VideoDecoder */
  private initDecoder(codec: string, description: ArrayBuffer, width: number, height: number) {
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

    console.log('[H264] Creating VideoDecoder with codec:', codec,
      'description size:', description.byteLength,
      'expected dimensions:', width, 'x', height)

    this.decoder = new VideoDecoder({
      output: (frame: VideoFrame) => {
        console.log('[H264] Decoder output frame:',
          frame.displayWidth, 'x', frame.displayHeight,
          'timestamp:', frame.timestamp,
          'decoded count:', this._decodedCount)

        // canvas 尺寸已在 config 到达时设为设备分辨率（deviceW x deviceH）。
        // max_size=0 保证帧尺寸 == 设备分辨率，因此此处不再调整 canvas。
        // 若帧尺寸与 canvas 不同（异常情况），仅记录警告，不改变 canvas（保持坐标映射正确）。
        if (this.canvas.width !== frame.displayWidth ||
            this.canvas.height !== frame.displayHeight) {
          console.warn('[H264] Frame size mismatch with canvas!',
            'canvas:', this.canvas.width, 'x', this.canvas.height,
            'frame:', frame.displayWidth, 'x', frame.displayHeight,
            '- click mapping may be inaccurate')
        }
        ctx.drawImage(frame, 0, 0, this.canvas.width, this.canvas.height)
        frame.close()

        // 首次输出帧时标记为 streaming
        if (this._state !== 'streaming') {
          this._state = 'streaming'
          this.onStateChange?.(this._state)
        }
      },
      error: (e: DOMException) => {
        console.error('[H264] VideoDecoder error:', e.name, e.message)
        this.setError(`Decoder error: ${e.name}: ${e.message}`)
      },
    })

    try {
      this.decoder.configure({
        codec: codec,
        description: description,
        optimizeForLatency: true,
      })
      console.log('[H264] Decoder state after configure:', this.decoder.state)
      if (this.decoder.state !== 'configured') {
        this.setError(`VideoDecoder configure failed, state: ${this.decoder.state}`)
      }
    } catch (e) {
      console.error('[H264] VideoDecoder configure threw exception:', e)
      this.setError(`VideoDecoder configure exception: ${e}`)
    }
  }

  /** 设置错误状态 */
  private setError(message: string) {
    console.error('[H264] Error:', message)
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

  /**
   * 从 Annex B 字节流中提取所有 NAL 单元。
   * 返回的每个 NALU 包含：
   *   raw: 包含起始码的完整 NALU
   *   data: 不含起始码但包含 NAL 头的 NALU（如 0x67 xx xx ... 或 0x65 xx xx ...）
   */
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
        data: data.slice(naluStart, naluEnd), // 不含起始码（含 NAL 头）
      })

      i = naluEnd
    }

    return nalus
  }

  /**
   * 去掉 NALU 的起始码（3 字节或 4 字节），返回包含 NAL 头的数据。
   * 例如：00 00 00 01 67 42 00 1e ... → 67 42 00 1e ...
   */
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
   * 构建 AVCC avcC 格式的 description（用于 VideoDecoder.configure）。
   *
   * 参考 ISO 14496-15 Section 5.3.3.1.2:
   *
   * avcC box 结构：
   *   configurationVersion (1 byte) = 1
   *   AVCProfileIndication (1 byte) = SPS 的 profile_idc
   *   profile_compatibility (1 byte) = SPS 的 constraint flags
   *   AVCLevelIndication (1 byte) = SPS 的 level_idc
   *   lengthSizeMinusOne (1 byte) = 0xFC | 3 (NAL 长度前缀 4 字节)
   *   numOfSPS (1 byte) = 0xE0 | 1
   *   SPS length (2 bytes, big-endian)
   *   SPS data (含 NAL 头，不含起始码)
   *   numOfPPS (1 byte) = 1
   *   PPS length (2 bytes, big-endian)
   *   PPS data (含 NAL 头，不含起始码)
   *
   * 注意：avcC 中的 SPS/PPS 包含 NAL 头字节（如 0x27, 0x28）！
   */
  private buildAvccDescription(spsWithStartCode: Uint8Array, ppsWithStartCode: Uint8Array): ArrayBuffer {
    // 去掉起始码，保留 NAL 头
    // SPS: 00 00 00 01 27 42 e0 1f ... → 27 42 e0 1f ...
    // PPS: 00 00 00 01 28 ce 32 48 → 28 ce 32 48
    const sps = this.stripStartCode(spsWithStartCode)
    const pps = this.stripStartCode(ppsWithStartCode)

    // 从 SPS（含 NAL 头）中提取 profile/level
    // SPS 格式：[NAL_header=0x27] [profile_idc=0x42] [constraint=0xe0] [level=0x1f] ...
    if (sps.length < 4) {
      console.error('[H264] SPS too short:', sps.length, 'bytes')
      return new ArrayBuffer(0)
    }

    const profileIdc = sps[1]  // profile_idc
    const compatibility = sps[2]  // constraint_set flags
    const levelIdc = sps[3]  // level_idc

    console.log('[H264] AVCC params:', {
      profileIdc: `0x${profileIdc.toString(16).padStart(2, '0')}`,
      compatibility: `0x${compatibility.toString(16).padStart(2, '0')}`,
      levelIdc: `0x${levelIdc.toString(16).padStart(2, '0')}`,
      spsLength: sps.length,
      ppsLength: pps.length,
      spsHex: Array.from(sps.slice(0, 10)).map(b => b.toString(16).padStart(2, '0')).join(' '),
      ppsHex: Array.from(pps.slice(0, 10)).map(b => b.toString(16).padStart(2, '0')).join(' '),
    })

    // 计算总大小（avcC box 结构）：
    // configurationVersion(1) + profile(1) + compatibility(1) + level(1)
    // + lengthSizeMinusOne(1) + numOfSPS(1) + SPS_length(2) + SPS_data + numOfPPS(1) + PPS_length(2) + PPS_data
    const totalSize = 7 + 2 + sps.length + 2 + pps.length
    const buffer = new ArrayBuffer(totalSize)
    const view = new DataView(buffer)
    const bytes = new Uint8Array(buffer)

    let offset = 0

    // configurationVersion (1 byte) = 1
    view.setUint8(offset++, 1)
    // AVCProfileIndication (1 byte)
    view.setUint8(offset++, profileIdc)
    // profile_compatibility (1 byte)
    view.setUint8(offset++, compatibility)
    // AVCLevelIndication (1 byte)
    view.setUint8(offset++, levelIdc)
    // lengthSizeMinusOne: 高6位保留位(全1) + 低2位=3 (NAL长度前缀=4字节)
    view.setUint8(offset++, 0xFC | 3)
    // numOfSPS: 高3位保留位(全1) + 低5位=1
    view.setUint8(offset++, 0xE0 | 1)
    // SPS length (2 bytes, big-endian)
    view.setUint16(offset, sps.length)
    offset += 2
    // SPS data (含 NAL 头，不含起始码)
    bytes.set(sps, offset)
    offset += sps.length
    // numOfPPS (1 byte) = 1
    view.setUint8(offset++, 1)
    // PPS length (2 bytes, big-endian)
    view.setUint16(offset, pps.length)
    offset += 2
    // PPS data (含 NAL 头，不含起始码)
    bytes.set(pps, offset)

    return buffer
  }

  /**
   * 将 Annex B 帧转换为 AVCC 格式。
   * Annex B: [start_code] NALU [start_code] NALU ...
   * AVCC:    [4-byte length] NALU [4-byte length] NALU ...
   *
   * 注意：AVCC 格式的 NALU 包含 NAL 头字节（与 avcC description 不同）。
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
