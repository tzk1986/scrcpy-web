/**
 * H.264/H.265 视频流服务（WebCodecs）
 * =================================
 *
 * 通过 WebSocket 接收视频码流，使用浏览器 WebCodecs VideoDecoder 解码，
 * 渲染到 canvas 上。方案 45 T4：码流家族按 config.codec 前缀泛化
 * （avc1.* → h264；hvc1.* / hev1.* → h265），h264 路径行为不变。
 *
 * 协议：
 *   服务端 → 客户端：
 *     - 配置消息（JSON）:
 *       { "type": "config", "codec": "avc1.XXYYZZ" | "hvc1.…",
 *         "width": 1280, "height": 720,
 *         "description": "<hex SPS+PPS（h264）或 VPS+SPS+PPS（h265，方案 45 T3）>" }
 *     - 视频帧（二进制）: [8B PTS 大端无符号 µs][Annex B NAL 单元]
 *       （方案 32：PTS 为设备单调时钟原值，喂入 EncodedVideoChunk.timestamp）
 *     - 错误（JSON）: { "type": "error", "message": "..." }
 *
 *   客户端 → 服务端：JSON 输入消息
 *
 * 解码流程：
 *   1. 收到 config 消息 → 按家族从 hex description 提取参数集
 *      （h264：SPS/PPS，NAL type 7/8；h265：VPS/SPS/PPS，NAL type 32/33/34）
 *   2. 构建对应配置记录 description（h264：avcC；h265：hvcC，ISO 14496-15）
 *   3. 创建 VideoDecoder 实例
 *   4. 收到二进制帧 → Annex B 转长度前缀格式 → EncodedVideoChunk → decode()
 *   5. VideoDecoder output → VideoFrame → drawImage(canvas)
 */

import type { WebSocketService } from './websocket'
import type { CodecFamily } from './h264NalUtils'
import {
  extractNalus,
  scanFrame,
  frameToAvcc,
  shouldDropFrame,
  MAX_DECODE_QUEUE,
} from './h264NalUtils'

/** 32 位整数按位整体反转（HEVC compat flags：SPS 序 → hvcC/codec 串序，与后端 T3 同变换） */
function reverseBits32(value: number): number {
  let v = value >>> 0
  v = ((v >>> 1) & 0x55555555) | ((v & 0x55555555) << 1)
  v = ((v >>> 2) & 0x33333333) | ((v & 0x33333333) << 2)
  v = ((v >>> 4) & 0x0f0f0f0f) | ((v & 0x0f0f0f0f) << 4)
  v = ((v >>> 8) & 0x00ff00ff) | ((v & 0x00ff00ff) << 8)
  return ((v >>> 16) | (v << 16)) >>> 0
}

/**
 * 构建 HEVCDecoderConfigurationRecord（hvcC，用于 VideoDecoder.configure
 * 的 description；方案 45 T4）。
 *
 * 布局依据 ISO/IEC 14496-15 §8.3.3.1.2（2022 版 §8.3.2.1.2）：
 *   23B 固定头 + 3 个 NAL 数组（VPS/SPS/PPS，array_completeness=1）：
 *   [0]     configurationVersion = 1
 *   [1]     profile_space(2)|tier_flag(1)|profile_idc(5) —— 直取 SPS payload b0
 *   [2..5]  general_profile_compatibility_flags —— SPS payload b1..b4 的 32 位
 *           按位整体反转（SPS 按 flag j MSB-first 写，记录 bit31 ↔ j=0；
 *           与后端 codec 串 compat 字段同变换）
 *   [6..11] general_constraint_indicator_flags —— SPS payload b5..b10 直拷
 *   [12]    general_level_idc —— SPS payload b11
 *   [13..14] 1111 + min_spatial_segmentation_idc = 0（未指定）
 *   [15]    111111 + parallelismType = 0（未指定）
 *   [16]    111111 + chromaFormat = 0（未指定）
 *   [17]    11111 + bitDepthLumaMinus8 = 0（未指定）
 *   [18]    11111 + bitDepthChromaMinus8 = 0（未指定）
 *   [19..20] avgFrameRate = 0（未指定）
 *   [21]    constantFrameRate=0|numTemporalLayers=1|temporalIdNested=1|lengthSizeMinusOne=3
 *   [22]    numOfArrays = 3
 *   数组区  tag(0xA0/0xA1/0xA2 = complete|type 32/33/34) + numNalus(2B)=1
 *           + nalUnitLength(2B) + NALU（含 2B HEVC NAL 头）
 *
 * chromaFormat/bitDepth/min_spatial_segmentation 等取 0（规范语义「未指定」）：
 * 这些是信息性字段，解码器（Chromium）从数组区 SPS 自行解析真实值；
 * 派生它们需完整 RBSP 位读取（防伪起始码 + Exp-Golomb + PTL 子层循环），
 * 收益不抵复杂度与出错面（R5 裁定的保守默认）。
 *
 * 入参为去起始码、含 2B NAL 头的 VPS/SPS/PPS（extractNalus 的 data 段）。
 * SPS payload 不足 12B（profile/level 段缺失）时返回空（调用方按失败处理，
 * 与 buildAvccDescription 的 SPS 过短语义一致）。
 */
export function buildHvcCDescription(
  vps: Uint8Array, sps: Uint8Array, pps: Uint8Array,
): Uint8Array<ArrayBuffer> {
  const NAL_HEADER_LEN = 2  // HEVC NAL 头 2B（h264 为 1B）
  if (sps.length < NAL_HEADER_LEN + 12 ||
      vps.length <= NAL_HEADER_LEN || pps.length <= NAL_HEADER_LEN) {
    console.error('[H264] HEVC param sets too short for hvcC:',
      { vps: vps.length, sps: sps.length, pps: pps.length })
    return new Uint8Array(0)
  }

  const arrays: Array<[tag: number, nal: Uint8Array]> = [
    [0xa0, vps],   // array_completeness=1 | reserved=0 | NAL type 32 (VPS)
    [0xa1, sps],   // NAL type 33 (SPS)
    [0xa2, pps],   // NAL type 34 (PPS)
  ]
  let total = 23
  for (const [, nal] of arrays) total += 5 + nal.length
  const out = new Uint8Array(total)
  const view = new DataView(out.buffer)

  const p = NAL_HEADER_LEN  // SPS payload 起点
  out[0] = 1                                        // configurationVersion
  out[1] = sps[p]                                   // profile_space|tier|profile_idc
  const compat = reverseBits32(
    ((sps[p + 1] << 24) | (sps[p + 2] << 16) | (sps[p + 3] << 8) | sps[p + 4]) >>> 0,
  )
  view.setUint32(2, compat)                         // compat flags（位反转序）
  out.set(sps.subarray(p + 5, p + 11), 6)           // constraint indicator flags
  out[12] = sps[p + 11]                             // general_level_idc
  out[13] = 0xf0                                    // 1111 + min_spatial_segmentation=0
  out[15] = 0xfc                                    // 111111 + parallelismType=0
  out[16] = 0xfc                                    // 111111 + chromaFormat=0
  out[17] = 0xf8                                    // 11111 + bitDepthLumaMinus8=0
  out[18] = 0xf8                                    // 11111 + bitDepthChromaMinus8=0
  // [19..20] avgFrameRate=0（Uint8Array 初始化即为 0）
  out[21] = 0x0f                                    // cfr=0|layers=1|nested=1|lenSizeMinusOne=3
  out[22] = arrays.length                           // numOfArrays = 3

  let off = 23
  for (const [tag, nal] of arrays) {
    out[off] = tag
    view.setUint16(off + 1, 1)                      // numNalus = 1
    view.setUint16(off + 3, nal.length)             // nalUnitLength（含 NAL 头）
    out.set(nal, off + 5)
    off += 5 + nal.length
  }
  return out
}

export type H264StreamState = 'idle' | 'configuring' | 'streaming' | 'error' | 'stopped'

export interface H264StreamOptions {
  /**
   * 解码硬件加速偏好（实验开关，方案 17 实施项 4）。
   * .18（Rockchip）花屏/解码报错时可传 'prefer-software'，
   * 验证是否能规避 Chromium 硬解与 Rockchip 码流的兼容问题。
   * 不传则使用浏览器默认。
   */
  hardwareAcceleration?: HardwareAcceleration
}

export interface H264StreamStats {
  frameCount: number
  fps: number
  lastFrameTime: number
  state: H264StreamState
  error: string | null
  width: number
  height: number
  /** 因解码队列积压被丢弃的 delta 帧数 */
  droppedFrames: number
}

export class H264VideoStream {
  private ws: WebSocketService
  private canvas: HTMLCanvasElement
  private decoder: VideoDecoder | null = null
  private _state: H264StreamState = 'idle'
  private _frameCount = 0
  private _droppedFrames = 0
  private _lastFrameTime = 0
  private _fps = 0
  private _fpsCounter = 0
  private _fpsTimer: number | null = null
  private _error: string | null = null
  private _width = 0
  private _height = 0
  private onStateChange?: (state: H264StreamState) => void
  private onStatsUpdate?: (stats: H264StreamStats) => void
  // 保存最近的参数集，用于重新初始化解码器（h265 另有 VPS，方案 45 T4）
  private spsData: Uint8Array | null = null
  private ppsData: Uint8Array | null = null
  private vpsData: Uint8Array | null = null
  // 码流家族：由 config.codec 前缀判定，驱动 NAL type 语义与关键帧判定（方案 45 T4）
  private codecFamily: CodecFamily = 'h264'
  // 服务端自适应码率重启的宽限截止时间戳（暂停回退 watchdog 与 stats 上报）
  private _restartGraceUntil = 0
  // 截图回退期间进入 suspend：保留 ws 消息处理器、只计数不解码，
  // 作为「码流是否恢复」的探测器；resume() 退出
  private _suspended = false
  // 追赶状态（方案 31）：丢帧后参考链已断，锁定丢弃 delta 直到下一 IDR
  // （自包含的干净恢复点），期间请求服务端催发 IDR 缩短恢复窗口
  private _awaitingIdr = false
  // warn 限速（方案 31）：未就绪/尺寸错配为状态性问题，仅在进入异常态时 warn 一次
  private _notReadyWarned = false
  private _sizeMismatchWarned = false
  private _probeFrameCount = 0
  private _probeLastFrameTime = 0
  // 最近一次 config 的解析结果（suspend 期间新到的 config 暂存于此，resume 时复用）
  private _lastCodec: string | null = null
  private _lastAvccDesc: BufferSource | null = null
  private _lastWidth = 0
  private _lastHeight = 0
  // 后端 RESET_VIDEO 保活间隔（config.idle_reset_seconds×1000，0 表示关闭/未知）
  private _keepaliveMs = 0
  // 解码硬件加速偏好（实验开关，方案 17 实施项 4）
  private _hardwareAcceleration?: HardwareAcceleration

  constructor(ws: WebSocketService, canvas: HTMLCanvasElement, options?: H264StreamOptions) {
    this.ws = ws
    this.canvas = canvas
    this._hardwareAcceleration = options?.hardwareAcceleration
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
      droppedFrames: this._droppedFrames,
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
   * 导出渲染 canvas 当前帧为 PNG Blob（方案 40 A）。
   * 渲染帧方向与视频流一致（天然正），无需 rotation 判据。
   * 截图零额外画布、零新依赖；toBlob 回调为空时 reject。
   */
  captureFrame(): Promise<Blob> {
    return new Promise((resolve, reject) => {
      this.canvas.toBlob((blob) => {
        if (blob) {
          resolve(blob)
        } else {
          reject(new Error('canvas.toBlob 返回空数据'))
        }
      }, 'image/png')
    })
  }

  /** 码率重启宽限截止时间戳（ms）。未处于宽限期为 0。 */
  get restartGraceUntil(): number {
    return this._restartGraceUntil
  }

  /** 后端 RESET_VIDEO 保活间隔（ms），0 表示未知/关闭（供回退阈值联动） */
  get keepaliveIntervalMs(): number {
    return this._keepaliveMs
  }

  /** 是否处于 suspend（截图回退探测）状态 */
  get suspended(): boolean {
    return this._suspended
  }

  /**
   * suspend 期间经 ws 到达的帧探测计数。
   * frameCount 为累计值，调用方自行计算窗口差值。
   */
  get probeStats(): { frameCount: number; lastFrameTime: number } {
    return { frameCount: this._probeFrameCount, lastFrameTime: this._probeLastFrameTime }
  }

  /**
   * 启动视频流。
   * 注册 WebSocket 消息处理器，等待 config 消息后初始化解码器。
   */
  start() {
    if (this._state === 'streaming' || this._state === 'configuring') return

    // 复位探测态：suspend 后未经 resume/stop 直接 start 时，若留下 _suspended=true，
    // 后续 config 只暂存、帧只计数不解码 → 永久黑屏（方案 51 F1-01）
    this._suspended = false
    this._state = 'configuring'
    this._error = null
    this._frameCount = 0
    this._droppedFrames = 0
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
    // binaryType=arraybuffer 下 JSON 控制消息一律走 string 通道，
    // 二进制帧为 ArrayBuffer；Blob 分支仅兜底 binaryType 未生效的异常环境
    this.ws.setMessageHandler((data: unknown) => {
      if (data instanceof ArrayBuffer) {
        this.handleBinaryFrame(new Uint8Array(data))
      } else if (typeof data === 'string') {
        try {
          const msg = JSON.parse(data)
          this.handleJsonMessage(msg)
        } catch {
          console.warn('[H264] Failed to parse text message:', data)
        }
      } else if (data instanceof Blob) {
        data.arrayBuffer().then(buf => {
          this.handleBinaryFrame(new Uint8Array(buf))
        })
      } else {
        console.warn('[H264] Unknown message type:', typeof data, data)
      }
    })

    // WS 瞬断重连后自动恢复（方案 19 终审修复）：重连成功后服务端新会话
    // 会重发 config，但前端若停在 suspend（截图回退）态，config 只暂存
    // 不重建解码器——必须 resume 退出探测态并 request_keyframe 才能回切
    // H264 推送，否则空闲设备瞬断后永久降级为只读截图。
    // resume() 自带 _suspended 守卫：非挂起时为空操作。
    this.ws.setReconnectHandler(() => this.resume())

    console.log('[H264] Stream started, waiting for config...')
  }

  /** 停止视频流，释放解码器资源 */
  stop() {
    this._suspended = false
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
    this.vpsData = null
    this.codecFamily = 'h264'
    this._state = 'stopped'
    this.onStateChange?.(this._state)
  }

  /**
   * 暂停解码进入探测模式（截图回退时调用）。
   *
   * 与 stop() 的区别：保留 ws 消息处理器与 SPS/PPS/config 暂存，
   * 后续到达的二进制帧只计数不解码（probeStats），供回切判定使用。
   */
  suspend() {
    if (this._suspended) return
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
    this._suspended = true
    this._state = 'stopped'
    this.onStateChange?.(this._state)
  }

  /**
   * 退出探测模式恢复解码（码流恢复回切时调用）。
   * 若 suspend 期间收到过新 config，直接用它重建解码器；
   * 否则使用 suspend 前最后一次 config 的解析结果。
   */
  resume() {
    if (!this._suspended) return
    this._suspended = false
    // 回切即新接收端加入：请求服务端立即产出 IDR，消灭 configuring 长等待
    this.ws.send({ op: 'request_keyframe' })
    this._error = null
    this._frameCount = 0
    this._droppedFrames = 0
    this._fpsCounter = 0
    this._lastFrameTime = 0
    this._probeFrameCount = 0
    this._probeLastFrameTime = 0
    this._restartGraceUntil = 0
    this._state = 'configuring'
    if (this._fpsTimer === null) {
      this._fpsTimer = window.setInterval(() => {
        this._fps = this._fpsCounter
        this._fpsCounter = 0
        this.onStatsUpdate?.(this.stats)
      }, 1000)
    }
    this.onStateChange?.(this._state)
    if (this._lastCodec && this._lastAvccDesc) {
      this.initDecoder(this._lastCodec, this._lastAvccDesc, this._lastWidth, this._lastHeight)
    }
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
      // 码流家族判定（方案 45 T4）：hvc1./hev1. 前缀 → h265，其余（含缺省
      // avc1.42E01E）→ h264，h264 分支与泛化前逐行等价
      this.codecFamily = codec.startsWith('hvc1.') || codec.startsWith('hev1.') ? 'h265' : 'h264'

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

      // 解析 hex 格式的参数集（Annex B 格式，带起始码；h264 为 SPS+PPS，
      // h265 为 VPS+SPS+PPS，方案 45 T3）
      const annexBData = this.hexToUint8Array(descriptionHex)
      console.log('[H264] AnnexB data hex (first 40 bytes):',
        Array.from(annexBData.slice(0, 40)).map(b => b.toString(16).padStart(2, '0')).join(' '))

      // 按家族提取参数集 NAL 单元（h264：type 7/8 存 raw 含起始码——沿用原
      // 语义供 buildAvccDescription 去码；h265：type 32/33/34 存 data 去起始码
      // 含 2B NAL 头——buildHvcCDescription 直接消费）
      const nalus = extractNalus(annexBData)
      console.log('[H264] Extracted NALUs:', nalus.length)
      for (const nalu of nalus) {
        const naluType = this.codecFamily === 'h265'
          ? ((nalu.data[0] as number) >> 1) & 0x3f
          : nalu.data[0] & 0x1F
        console.log('[H264] NALU type:', naluType,
          'length (with NAL header):', nalu.data.length,
          'first bytes:', Array.from(nalu.data.slice(0, 6)).map(b => b.toString(16).padStart(2, '0')).join(' '))
        if (this.codecFamily === 'h265') {
          if (naluType === 32) {
            // VPS
            this.vpsData = nalu.data  // 去起始码、含 NAL 头
          } else if (naluType === 33) {
            // SPS
            this.spsData = nalu.data
          } else if (naluType === 34) {
            // PPS
            this.ppsData = nalu.data
          }
        } else if (naluType === 7) {
          // SPS
          this.spsData = nalu.raw  // 包含起始码
        } else if (naluType === 8) {
          // PPS
          this.ppsData = nalu.raw  // 包含起始码
        }
      }

      let description: BufferSource
      if (this.codecFamily === 'h265') {
        if (!this.vpsData || !this.spsData || !this.ppsData) {
          this.setError(`Failed to extract VPS/SPS/PPS from description. VPS: ${!!this.vpsData}, SPS: ${!!this.spsData}, PPS: ${!!this.ppsData}`)
          return
        }
        // 创建 hvcC 格式的 description（HEVCDecoderConfigurationRecord）
        const hvcC = buildHvcCDescription(this.vpsData, this.spsData, this.ppsData)
        if (hvcC.byteLength === 0) {
          this.setError('Failed to build hvcC description')
          return
        }
        console.log('[H264] hvcC description hex:',
          Array.from(hvcC).map(b => b.toString(16).padStart(2, '0')).join(' '))
        console.log('[H264] hvcC description size:', hvcC.byteLength)
        description = hvcC
      } else {
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
        description = avccDescription
      }

      // 关键：收到 config 后立即设置 canvas 绘制缓冲区尺寸为设备分辨率
      // CSS 尺寸由 object-fit:contain 控制，自适应容器并保持比例。
      // InputController 的坐标映射基于 canvas.width/height（绘制缓冲区）和
      // getBoundingClientRect()（CSS 盒），自动处理 letterboxing。
      if (width > 0 && height > 0) {
        this.canvas.width = width
        this.canvas.height = height
        this._width = width
        this._height = height
        console.log('[H264] Canvas buffer initialized to device resolution:', width, 'x', height)
      }

      // 暂存 config 解析结果（suspend 期间仅暂存；resume 时复用重建解码器）
      this._lastCodec = codec
      this._lastAvccDesc = description
      this._lastWidth = width
      this._lastHeight = height
      // NaN 守卫（终审 Minor #4）：非有限数（缺失/非法字段）归 0
      const keepaliveSec = Number(msg.idle_reset_seconds ?? 0)
      this._keepaliveMs = (Number.isFinite(keepaliveSec) ? keepaliveSec : 0) * 1000

      if (this._suspended) {
        console.log('[H264] Config stored while suspended, decoder init deferred')
        return
      }

      // 初始化 VideoDecoder
      this.initDecoder(codec, description, width, height)

    } else if (type === 'restarting') {
      // 服务端自适应码率重启预告：1-3s 黑屏属预期，宽限期内
      // 暂停回退 watchdog 与 stats 上报（重启后 config 会再次到达并重建解码器）
      this._restartGraceUntil = Date.now() + 15_000
      console.log('[H264] Stream restarting for bitrate switch, grace until', this._restartGraceUntil)
    } else if (type === 'stream_ended') {
      // 后端编码器/流已结束：立即报错走回退链，不再等 watchdog（方案 19 实施项 5）
      this.setError('视频流已结束')
    } else if (type === 'error') {
      this.setError(msg.message as string || 'Unknown server error')
    }
  }

  /** 处理二进制视频帧 */
  private handleBinaryFrame(data: Uint8Array) {
    // suspend（截图回退）期间不解码，仅记录帧到达供回切判定
    if (this._suspended) {
      this._probeFrameCount++
      this._probeLastFrameTime = Date.now()
      return
    }

    if (!this.decoder || this.decoder.state !== 'configured') {
      // 状态性问题：进入未就绪态后仅首次 warn，避免长窗口每帧风暴（方案 31）
      if (!this._notReadyWarned) {
        this._notReadyWarned = true
        console.warn('[H264] Frame dropped: decoder not ready, state:', this.decoder?.state,
          'decoder exists:', !!this.decoder)
      }
      return
    }

    this._frameCount++

    // 线上格式（方案 32）：[8B PTS 大端无符号，设备单调时钟 µs][Annex B 载荷]。
    // PTS 原值喂入 EncodedVideoChunk.timestamp（同单位 µs，无需换算）
    const pts = Number(new DataView(data.buffer, data.byteOffset, 8).getBigUint64(0))
    const payload = data.subarray(8)

    // 零拷贝单遍扫描（方案 26）：扫描与关键帧判定合一，AVCC 组装单次分配；
    // 关键帧语义按家族判定（h264 IDR=5；h265 IRAP 16..21，方案 45 T4）
    const scan = scanFrame(payload, this.codecFamily)

    if (this._frameCount <= 5) {
      console.log('[H264] Frame', this._frameCount, ':',
        'raw size:', payload.length,
        'isKeyFrame:', scan.isKey,
        'NALUs:', scan.nalus.map(n => `type=${this.naluTypeOf(payload[n.offset])},len=${n.length}`))
    }

    // 丢帧策略（方案 31）：
    // - 正常态：队列积压丢 delta 并进入追赶——被丢帧未提交，后续 delta 的
    //   参考链已断，若沿水位反复起停会花屏闪动
    // - 追赶态：所有 delta 一律丢弃（不判水位），直到 IDR 到达（自包含，
    //   是参考链唯一干净恢复点）
    if (this._awaitingIdr) {
      if (!scan.isKey) {
        this._droppedFrames++
        return
      }
      this._awaitingIdr = false
    } else if (shouldDropFrame(this.decoder.decodeQueueSize, scan.isKey)) {
      this._droppedFrames++
      this._awaitingIdr = true
      // 催 IDR：把恢复窗口从设备 IDR 间隔（10-57s）压缩到催帧往返（≤~1s）
      this.ws.send({ op: 'request_keyframe' })
      return
    }

    // 积压跨越一个 IDR 仍超阈值：重建解码器，以当前关键帧恢复
    // （对应 ws-scrcpy「I 帧到达时清空积压帧跳新帧」的做法）
    if (scan.isKey && this.decoder.decodeQueueSize >= MAX_DECODE_QUEUE) {
      console.warn('[H264] Decode queue overflow across IDR, rebuilding decoder. queue:',
        this.decoder.decodeQueueSize)
      if (this._lastCodec && this._lastAvccDesc) {
        this.initDecoder(this._lastCodec, this._lastAvccDesc, this._lastWidth, this._lastHeight)
      }
    }

    // 将 Annex B 帧转换为 AVCC 格式
    const avccData = frameToAvcc(payload, scan)

    const chunk = new EncodedVideoChunk({
      type: scan.isKey ? 'key' : 'delta',
      timestamp: pts,  // 设备 PTS 原值（µs）——VideoFrame.timestamp 随之正确
      data: avccData,
    })

    try {
      this.decoder.decode(chunk)
      this._fpsCounter++
      this._lastFrameTime = Date.now()
    } catch (e) {
      console.error('[H264] Decode failed for frame', this._frameCount, ':', e)
    }
  }

  /** 初始化 VideoDecoder（description 为 avcC/hvcC 配置记录，方案 45 T4 泛化为 BufferSource） */
  private initDecoder(codec: string, description: BufferSource, width: number, height: number) {
    // 每条重建路径（config 重发 / resume / IDR 重建）都回到干净初始态：
    // 新解码器不含旧参考链，追赶锁定随之解除（方案 31）
    this._awaitingIdr = false

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
        // canvas 尺寸已在 config 到达时设为设备分辨率（deviceW x deviceH）。
        // max_size=0 保证帧尺寸 == 设备分辨率，因此此处不再调整 canvas。
        // 若帧尺寸与 canvas 不同（异常情况），仅记录警告，不改变 canvas（保持坐标映射正确）。
        if (this.canvas.width !== frame.displayWidth ||
            this.canvas.height !== frame.displayHeight) {
          // 状态性问题：错配期间仅首次 warn；恢复匹配后重置（方案 31）
          if (!this._sizeMismatchWarned) {
            this._sizeMismatchWarned = true
            console.warn('[H264] Frame size mismatch with canvas!',
              'canvas:', this.canvas.width, 'x', this.canvas.height,
              'frame:', frame.displayWidth, 'x', frame.displayHeight,
              '- click mapping may be inaccurate')
          }
        } else {
          this._sizeMismatchWarned = false
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
      const config: VideoDecoderConfig = {
        codec: codec,
        description: description,
        optimizeForLatency: true,
      }
      // 实验开关：仅在显式指定时传入（方案 17 实施项 4），默认走浏览器选择
      if (this._hardwareAcceleration) {
        config.hardwareAcceleration = this._hardwareAcceleration
      }
      this.decoder.configure(config)
      console.log('[H264] Decoder state after configure:', this.decoder.state,
        this._hardwareAcceleration ? `(hardwareAcceleration: ${this._hardwareAcceleration})` : '')
      if (this.decoder.state !== 'configured') {
        this.setError(`VideoDecoder configure failed, state: ${this.decoder.state}`)
      } else {
        // 恢复 configured：未就绪 warn 限速复位（方案 31）
        this._notReadyWarned = false
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

  /** 按当前家族解出 NAL 头字节中的 NAL type（仅日志用途；方案 45 T4） */
  private naluTypeOf(header: number): number {
    return this.codecFamily === 'h265' ? (header >> 1) & 0x3f : header & 0x1f
  }

  /** 将 hex 字符串转为 Uint8Array */
  private hexToUint8Array(hex: string): Uint8Array {
    const bytes = new Uint8Array(hex.length / 2)
    for (let i = 0; i < hex.length; i += 2) {
      bytes[i / 2] = parseInt(hex.substring(i, i + 2), 16)
    }
    return bytes
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
}
