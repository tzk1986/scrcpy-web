/**
 * H264VideoStream 行为测试
 * =========================
 *
 * 覆盖：
 *   - hardwareAcceleration 选项（方案 17 实施项 4）
 *   - start/stop/suspend/resume 生命周期与 getter
 *   - config / restarting / error 消息处理与全部错误分支
 *   - 二进制帧：丢帧、关键帧重建解码器、AVCC 转换、decode 异常吞掉
 *   - PTS 透传（方案 32）：8B 前缀解包 → EncodedVideoChunk.timestamp
 *   - 追赶状态机（丢到下一 IDR + 催帧）与 warn 限速（方案 31）
 *   - 解码器 output/error 回调与 canvas 绘制、streaming 状态流转
 */
import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest'
import { H264VideoStream, buildHvcCDescription } from './h264VideoStream'
import type { WebSocketService } from './websocket'

/** 最小合法 SPS+PPS（Annex B）：SPS 0x67... 5 字节 + PPS 0x68... 4 字节。 */
const CONFIG_MSG = JSON.stringify({
  type: 'config',
  codec: 'avc1.42E01E',
  width: 640,
  height: 480,
  description: '000000016742e01e890000000168ce3880',
})

/** 3 字节起始码版本的同一 config（覆盖 stripStartCode 3 字节分支）。 */
const CONFIG_MSG_3BYTE = JSON.stringify({
  type: 'config',
  codec: 'avc1.42E01E',
  width: 640,
  height: 480,
  description: '0000016742e01e8900000168ce3880',
})

/** 4 字节起始码的 IDR 关键帧（NAL type 5）与 delta 帧（NAL type 1）载荷。 */
const KEY_PAYLOAD = new Uint8Array([0, 0, 0, 1, 0x65, 0x11, 0x22, 0x33])
const DELTA_PAYLOAD = new Uint8Array([0, 0, 0, 1, 0x41, 0x99, 0x88])

/** 构造线上格式二进制帧：[8B PTS 大端无符号][Annex B 载荷]（方案 32）。 */
function frameMessage(pts: number, payload: Uint8Array): ArrayBuffer {
  const out = new Uint8Array(8 + payload.length)
  new DataView(out.buffer).setBigUint64(0, BigInt(pts))
  out.set(payload, 8)
  return out.buffer
}

/** 带 PTS 前缀的线上格式帧（服务端实际发送的形态）。 */
const KEY_FRAME = frameMessage(33_333, KEY_PAYLOAD)
const DELTA_FRAME = frameMessage(66_666, DELTA_PAYLOAD)

type DecoderInit = {
  output: (frame: VideoFrame) => void
  error: (e: DOMException) => void
}

class FakeVideoDecoder {
  static instances: FakeVideoDecoder[] = []
  /** configure 后写入的 state（null → 'configured'，用于模拟失败） */
  static configureState: string | null = null
  static throwOnConfigure = false
  static throwOnClose = false

  state = 'unconfigured'
  config: VideoDecoderConfig | null = null
  decodeQueueSize = 0
  decode = vi.fn()
  init: DecoderInit

  constructor(init: DecoderInit) {
    this.init = init
    FakeVideoDecoder.instances.push(this)
  }

  configure(config: VideoDecoderConfig) {
    if (FakeVideoDecoder.throwOnConfigure) throw new Error('configure boom')
    this.config = config
    this.state = FakeVideoDecoder.configureState ?? 'configured'
  }

  close() {
    if (FakeVideoDecoder.throwOnClose) throw new Error('close boom')
    this.state = 'closed'
  }
}

class FakeEncodedVideoChunk {
  static instances: FakeEncodedVideoChunk[] = []
  init: { type: string; timestamp: number; data: ArrayBuffer }

  constructor(init: { type: string; timestamp: number; data: ArrayBuffer }) {
    this.init = init
    FakeEncodedVideoChunk.instances.push(this)
  }
}

class FakeWs {
  handler: ((data: unknown) => void) | null = null
  ws: WebSocket | null = null
  handlerSetCount = 0
  /** 客户端发出的消息（WebSocketService.send 收到后解析记录） */
  sent: unknown[] = []
  /** start() 注册的重连成功回调 */
  reconnectHandler: (() => void) | null = null

  setMessageHandler(handler: (data: unknown) => void) {
    this.handler = handler
    this.handlerSetCount++
  }

  setReconnectHandler(handler: () => void) {
    this.reconnectHandler = handler
  }

  send(data: unknown) {
    this.sent.push(typeof data === 'string' ? JSON.parse(data as string) : data)
  }
}

const ctxDrawImage = vi.fn()

function makeCanvas() {
  const canvas = document.createElement('canvas')
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext')
    .mockReturnValue({ drawImage: ctxDrawImage } as unknown as CanvasRenderingContext2D)
  return canvas
}

function makeCanvasWithoutContext() {
  const canvas = document.createElement('canvas')
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext')
    .mockReturnValue(null as unknown as CanvasRenderingContext2D)
  return canvas
}

function makeStream(ws: FakeWs, canvas: HTMLCanvasElement = makeCanvas()) {
  return new H264VideoStream(ws as unknown as WebSocketService, canvas)
}

/** 启动流并喂入 config 消息，返回创建出的 FakeVideoDecoder。 */
function startWithConfig(stream: H264VideoStream, ws: FakeWs): FakeVideoDecoder {
  stream.start()
  ws.handler!(CONFIG_MSG)
  const dec = FakeVideoDecoder.instances.at(-1)
  if (!dec) throw new Error('VideoDecoder not created')
  return dec
}

async function flush() {
  for (let i = 0; i < 5; i++) await Promise.resolve()
}

let logSpy: MockInstance
let warnSpy: MockInstance
let errorSpy: MockInstance

beforeEach(() => {
  FakeVideoDecoder.instances = []
  FakeVideoDecoder.configureState = null
  FakeVideoDecoder.throwOnConfigure = false
  FakeVideoDecoder.throwOnClose = false
  FakeEncodedVideoChunk.instances = []
  ctxDrawImage.mockClear()
  vi.stubGlobal('VideoDecoder', FakeVideoDecoder)
  vi.stubGlobal('EncodedVideoChunk', FakeEncodedVideoChunk)
  vi.useFakeTimers()
  logSpy = vi.spyOn(console, 'log').mockImplementation(() => {})
  warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
  errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('H264VideoStream hardwareAcceleration', () => {
  it('传入 prefer-software 时 configure 携带该偏好', () => {
    const ws = new FakeWs()
    const stream = new H264VideoStream(
      ws as unknown as WebSocketService,
      makeCanvas(),
      { hardwareAcceleration: 'prefer-software' },
    )

    const dec = startWithConfig(stream, ws)
    expect(dec.config?.hardwareAcceleration).toBe('prefer-software')

    stream.stop()
  })

  it('未传入时 configure 不含 hardwareAcceleration 键', () => {
    const ws = new FakeWs()
    const stream = new H264VideoStream(ws as unknown as WebSocketService, makeCanvas())

    const dec = startWithConfig(stream, ws)
    expect(dec.config).not.toBeNull()
    expect('hardwareAcceleration' in (dec.config as object)).toBe(false)

    stream.stop()
  })
})

describe('生命周期与 getter', () => {
  it('初始状态：idle、统计零值、非 suspend、无宽限', () => {
    const stream = makeStream(new FakeWs())

    expect(stream.state).toBe('idle')
    expect(stream.stats).toEqual({
      frameCount: 0,
      fps: 0,
      lastFrameTime: 0,
      state: 'idle',
      error: null,
      width: 0,
      height: 0,
      droppedFrames: 0,
    })
    expect(stream.suspended).toBe(false)
    expect(stream.restartGraceUntil).toBe(0)
    expect(stream.keepaliveIntervalMs).toBe(0)
    expect(stream.probeStats).toEqual({ frameCount: 0, lastFrameTime: 0 })
  })

  it('重复 start 不再重复注册消息处理器', () => {
    const ws = new FakeWs()
    const stream = makeStream(ws)

    stream.start()
    stream.start()

    expect(ws.handlerSetCount).toBe(1)
  })

  it('全生命周期零 console.log（方案 51 F1-17：调试日志已清除）', () => {
    const ws = new FakeWs()
    const stream = makeStream(ws)
    const dec = startWithConfig(stream, ws)

    ws.handler!(KEY_FRAME)
    const frame = {
      displayWidth: 640, displayHeight: 480, close: vi.fn(),
    } as unknown as VideoFrame
    dec.init.output(frame)
    stream.stop()

    expect(logSpy).not.toHaveBeenCalled()
  })

  it('FPS 定时器每秒上报 stats', async () => {
    const ws = new FakeWs()
    const stream = makeStream(ws)
    const updates: unknown[] = []
    stream.setStatsUpdateHandler(s => updates.push(s))

    stream.start()
    await vi.advanceTimersByTimeAsync(1000)

    expect(updates).toHaveLength(1)
    stream.stop()
  })

  it('stop 关闭解码器并进入 stopped', () => {
    const ws = new FakeWs()
    const stream = makeStream(ws)
    const states: string[] = []
    stream.setStateChangeHandler(s => states.push(s))
    const dec = startWithConfig(stream, ws)

    stream.stop()

    expect(dec.state).toBe('closed')
    expect(stream.state).toBe('stopped')
    expect(states).toEqual(['configuring', 'stopped'])
  })

  it('stop 时解码器 close 抛异常被吞掉', () => {
    const ws = new FakeWs()
    const stream = makeStream(ws)
    startWithConfig(stream, ws)
    FakeVideoDecoder.throwOnClose = true

    expect(() => stream.stop()).not.toThrow()
    expect(stream.state).toBe('stopped')
  })

  it('未 start 直接 stop 不抛异常', () => {
    const stream = makeStream(new FakeWs())
    expect(() => stream.stop()).not.toThrow()
    expect(stream.state).toBe('stopped')
  })
})

describe('config 消息处理', () => {
  it('正常 config：建立解码器、设置 canvas 尺寸与 AVCC description', () => {
    const ws = new FakeWs()
    const canvas = makeCanvas()
    const stream = makeStream(ws, canvas)

    const dec = startWithConfig(stream, ws)

    expect(dec.config?.codec).toBe('avc1.42E01E')
    expect(dec.config?.optimizeForLatency).toBe(true)
    expect(dec.config?.description).toBeInstanceOf(ArrayBuffer)
    expect((dec.config?.description as ArrayBuffer).byteLength).toBe(20)
    expect(canvas.width).toBe(640)
    expect(canvas.height).toBe(480)
    expect(stream.stats.width).toBe(640)
    expect(stream.stats.height).toBe(480)

    stream.stop()
  })

  it('3 字节起始码的 description 同样可解析', () => {
    const ws = new FakeWs()
    const stream = makeStream(ws)
    stream.start()

    ws.handler!(CONFIG_MSG_3BYTE)

    const dec = FakeVideoDecoder.instances.at(-1)
    expect(dec?.config?.description).toBeInstanceOf(ArrayBuffer)
    expect((dec?.config?.description as ArrayBuffer).byteLength).toBe(20)
    expect(stream.state).toBe('configuring')
  })

  it('缺 description 时进入 error 状态', () => {
    const ws = new FakeWs()
    const stream = makeStream(ws)
    stream.start()

    ws.handler!(JSON.stringify({ type: 'config', codec: 'avc1.42E01E' }))

    expect(stream.state).toBe('error')
    expect(stream.stats.error).toBe('Missing codec description in config')
  })

  it('description 中无 SPS/PPS 时进入 error 状态', () => {
    const ws = new FakeWs()
    const stream = makeStream(ws)
    stream.start()

    ws.handler!(JSON.stringify({
      type: 'config', width: 640, height: 480, description: '0000000106050505',
    }))

    expect(stream.state).toBe('error')
    expect(stream.stats.error).toContain('Failed to extract SPS/PPS')
    expect(FakeVideoDecoder.instances).toHaveLength(0)
  })

  it('SPS 过短导致 AVCC 构建失败时进入 error 状态', () => {
    const ws = new FakeWs()
    const stream = makeStream(ws)
    stream.start()

    // SPS: 00 00 00 01 67 01（载荷仅 2 字节，不足 SPS 头 4 字节）
    ws.handler!(JSON.stringify({
      type: 'config', width: 640, height: 480,
      description: '0000000167010000000168ce3880',
    }))

    expect(stream.state).toBe('error')
    expect(stream.stats.error).toBe('Failed to build AVCC description')
  })

  it('width/height 为 0 时不改 canvas 尺寸，解码器仍建立', () => {
    const ws = new FakeWs()
    const canvas = makeCanvas()
    const stream = makeStream(ws, canvas)
    stream.start()

    ws.handler!(JSON.stringify({
      type: 'config', codec: 'avc1.42E01E',
      description: '000000016742e01e890000000168ce3880',
    }))

    expect(canvas.width).toBe(300) // happy-dom 默认尺寸未被改写
    expect(stream.stats.width).toBe(0)
    expect(FakeVideoDecoder.instances).toHaveLength(1)
    expect(stream.state).toBe('configuring')
  })

  it('config 携带 idle_reset_seconds 时解析保活间隔（ms）', () => {
    const ws = new FakeWs()
    const stream = makeStream(ws)
    stream.start()

    ws.handler!(JSON.stringify({
      type: 'config', codec: 'avc1.42E01E', width: 640, height: 480,
      description: '000000016742e01e890000000168ce3880',
      idle_reset_seconds: 5,
    }))

    expect(stream.keepaliveIntervalMs).toBe(5000)
    stream.stop()
  })

  it('config 缺 idle_reset_seconds（旧后端）时保活间隔为 0', () => {
    const ws = new FakeWs()
    const stream = makeStream(ws)
    startWithConfig(stream, ws)

    expect(stream.keepaliveIntervalMs).toBe(0)
    stream.stop()
  })

  it('config 携带非法 idle_reset_seconds（非有限数）时保活间隔归 0（NaN 守卫）', () => {
    const ws = new FakeWs()
    const stream = makeStream(ws)
    stream.start()

    ws.handler!(JSON.stringify({
      type: 'config', codec: 'avc1.42E01E', width: 640, height: 480,
      description: '000000016742e01e890000000168ce3880',
      idle_reset_seconds: 'not-a-number',
    }))

    expect(stream.keepaliveIntervalMs).toBe(0)
    stream.stop()
  })

  it('configure 后状态非 configured 时进入 error', () => {
    FakeVideoDecoder.configureState = 'unconfigured'
    const ws = new FakeWs()
    const stream = makeStream(ws)

    startWithConfig(stream, ws)

    expect(stream.state).toBe('error')
    expect(stream.stats.error).toContain('configure failed')
  })

  it('configure 抛异常时进入 error', () => {
    FakeVideoDecoder.throwOnConfigure = true
    const ws = new FakeWs()
    const stream = makeStream(ws)

    startWithConfig(stream, ws)

    expect(stream.state).toBe('error')
    expect(stream.stats.error).toContain('configure exception')
    expect(errorSpy).toHaveBeenCalled()
  })

  it('canvas 无 2d context 时进入 error', () => {
    const ws = new FakeWs()
    const stream = makeStream(ws, makeCanvasWithoutContext())
    stream.start()

    ws.handler!(CONFIG_MSG)

    expect(stream.state).toBe('error')
    expect(stream.stats.error).toBe('Failed to get canvas 2d context')
    expect(FakeVideoDecoder.instances).toHaveLength(0)
  })
})

describe('控制消息（restarting / error / 异常数据）', () => {
  it('restarting 设置 15s 宽限期', () => {
    const ws = new FakeWs()
    const stream = makeStream(ws)
    stream.start()

    ws.handler!(JSON.stringify({ type: 'restarting' }))

    expect(stream.restartGraceUntil).toBe(Date.now() + 15_000)
    expect(stream.state).toBe('configuring')
  })

  it('stream_ended 消息立即进入 error 状态（走回退链，方案 19 实施项 5）', () => {
    const ws = new FakeWs()
    const stream = makeStream(ws)
    const states: string[] = []
    stream.setStateChangeHandler(s => states.push(s))
    stream.start()

    ws.handler!(JSON.stringify({ type: 'stream_ended' }))

    expect(stream.state).toBe('error')
    expect(stream.stats.error).toBe('视频流已结束')
    expect(states).toEqual(['configuring', 'error'])
  })

  it('error 消息携带服务端错误信息', () => {
    const ws = new FakeWs()
    const stream = makeStream(ws)
    stream.start()

    ws.handler!(JSON.stringify({ type: 'error', message: 'server boom' }))

    expect(stream.state).toBe('error')
    expect(stream.stats.error).toBe('server boom')
  })

  it('error 消息缺 message 时使用默认文案', () => {
    const ws = new FakeWs()
    const stream = makeStream(ws)
    stream.start()

    ws.handler!(JSON.stringify({ type: 'error' }))

    expect(stream.stats.error).toBe('Unknown server error')
  })

  it('非法 JSON 文本被忽略并告警', () => {
    const ws = new FakeWs()
    const stream = makeStream(ws)
    stream.start()

    ws.handler!('{not json')

    expect(warnSpy).toHaveBeenCalledWith('[H264] Failed to parse text message:', '{not json')
    expect(stream.state).toBe('configuring')
  })

  it('未知数据类型被忽略并告警', () => {
    const ws = new FakeWs()
    const stream = makeStream(ws)
    stream.start()

    ws.handler!(12345)

    expect(warnSpy).toHaveBeenCalledWith('[H264] Unknown message type:', 'number', 12345)
    expect(stream.state).toBe('configuring')
  })

  it('Blob 消息按二进制帧处理', async () => {
    const ws = new FakeWs()
    const stream = makeStream(ws)
    const dec = startWithConfig(stream, ws)

    ws.handler!(new Blob([KEY_FRAME]))
    await flush()

    expect(dec.decode).toHaveBeenCalledTimes(1)
    stream.stop()
  })
})

describe('二进制帧处理', () => {
  it('解码器未就绪时帧被丢弃且不抛异常', () => {
    const ws = new FakeWs()
    const stream = makeStream(ws)
    stream.start()

    expect(() => ws.handler!(KEY_FRAME)).not.toThrow()

    expect(warnSpy).toHaveBeenCalledWith(
      '[H264] Frame dropped:',
      'decoder not ready (state: null, exists: false)',
      '- subsequent drops silent, summarized every 100',
    )
    expect(stream.stats.frameCount).toBe(0)
  })

  it('畸形短帧（<8B）直接丢弃并计入未就绪限速（方案 51 F1-07）', () => {
    const ws = new FakeWs()
    const stream = makeStream(ws)
    stream.start()

    expect(() => ws.handler!(new ArrayBuffer(4))).not.toThrow()

    expect(warnSpy).toHaveBeenCalledWith(
      '[H264] Frame dropped:',
      'malformed frame (byteLength=4 < 8)',
      expect.any(String),
    )
    expect(stream.stats.frameCount).toBe(0)
    stream.stop()
  })

  it('关键帧：AVCC 转换、chunk 类型与帧计数', () => {
    const ws = new FakeWs()
    const stream = makeStream(ws)
    const dec = startWithConfig(stream, ws)

    ws.handler!(KEY_FRAME)

    expect(dec.decode).toHaveBeenCalledTimes(1)
    const chunk = FakeEncodedVideoChunk.instances.at(-1)
    expect(chunk?.init.type).toBe('key')
    expect(Array.from(new Uint8Array(chunk!.init.data)))
      .toEqual([0, 0, 0, 4, 0x65, 0x11, 0x22, 0x33])
    expect(stream.stats.frameCount).toBe(1)
    expect(stream.stats.lastFrameTime).toBeGreaterThan(0)
  })

  it('PTS 透传：timestamp 取自 8B 前缀原值、载荷剥离前缀（方案 32）', () => {
    const ws = new FakeWs()
    const stream = makeStream(ws)
    startWithConfig(stream, ws)

    ws.handler!(KEY_FRAME)
    const keyChunk = FakeEncodedVideoChunk.instances.at(-1)
    expect(keyChunk?.init.timestamp).toBe(33_333)  // 非写死 0，为设备 PTS 原值

    ws.handler!(DELTA_FRAME)
    const deltaChunk = FakeEncodedVideoChunk.instances.at(-1)
    expect(deltaChunk?.init.timestamp).toBe(66_666)
    // 载荷为剥离前缀后的 Annex B → AVCC（无 8B 前缀残留）
    expect(Array.from(new Uint8Array(deltaChunk!.init.data)))
      .toEqual([0, 0, 0, 3, 0x41, 0x99, 0x88])
    expect(stream.stats.frameCount).toBe(2)
  })

  it('delta 帧在解码队列积压（>=2）时被丢弃', () => {
    const ws = new FakeWs()
    const stream = makeStream(ws)
    const dec = startWithConfig(stream, ws)
    dec.decodeQueueSize = 2

    ws.handler!(DELTA_FRAME)

    expect(dec.decode).not.toHaveBeenCalled()
    expect(stream.stats.droppedFrames).toBe(1)
    expect(stream.stats.frameCount).toBe(1)
  })

  it('关键帧到达且队列积压时重建解码器', () => {
    const ws = new FakeWs()
    const stream = makeStream(ws)
    const dec = startWithConfig(stream, ws)
    dec.decodeQueueSize = 2

    ws.handler!(KEY_FRAME)

    expect(FakeVideoDecoder.instances).toHaveLength(2)
    expect(dec.state).toBe('closed')
    const rebuilt = FakeVideoDecoder.instances.at(-1)!
    expect(rebuilt.config?.codec).toBe('avc1.42E01E')
    expect(rebuilt.decode).toHaveBeenCalledTimes(1)
    expect(stream.stats.droppedFrames).toBe(0)
  })

  it('decode 抛异常被吞掉，流不中断', () => {
    const ws = new FakeWs()
    const stream = makeStream(ws)
    const dec = startWithConfig(stream, ws)
    dec.decode.mockImplementation(() => { throw new Error('decode boom') })

    expect(() => ws.handler!(DELTA_FRAME)).not.toThrow()

    expect(errorSpy).toHaveBeenCalled()
    expect(stream.state).toBe('configuring')
    expect(stream.stats.frameCount).toBe(1)
  })
})

describe('追赶状态机（方案 31）', () => {
  it('delta 积压触发追赶：丢弃并恰催帧一次', () => {
    const ws = new FakeWs()
    const stream = makeStream(ws)
    const dec = startWithConfig(stream, ws)
    dec.decodeQueueSize = 2

    ws.handler!(DELTA_FRAME)

    expect(dec.decode).not.toHaveBeenCalled()
    expect(stream.stats.droppedFrames).toBe(1)
    expect(ws.sent).toEqual([{ op: 'request_keyframe' }])
  })

  it('追赶中队列回落仍丢弃 delta（锁定至下一 IDR，与水位行为的关键差异）', () => {
    const ws = new FakeWs()
    const stream = makeStream(ws)
    const dec = startWithConfig(stream, ws)
    dec.decodeQueueSize = 2
    ws.handler!(DELTA_FRAME) // 触发追赶

    dec.decodeQueueSize = 0 // 队列回落：现状会恢复提交，追赶态必须继续丢
    ws.handler!(DELTA_FRAME)

    expect(dec.decode).not.toHaveBeenCalled()
    expect(stream.stats.droppedFrames).toBe(2)
  })

  it('追赶中丢弃不重复催帧', () => {
    const ws = new FakeWs()
    const stream = makeStream(ws)
    const dec = startWithConfig(stream, ws)
    dec.decodeQueueSize = 2
    ws.handler!(DELTA_FRAME)

    dec.decodeQueueSize = 0
    ws.handler!(DELTA_FRAME)
    ws.handler!(DELTA_FRAME)

    expect(ws.sent).toEqual([{ op: 'request_keyframe' }])
  })

  it('追赶中 IDR 到达：提交 IDR、退出追赶，后续 delta 恢复提交', () => {
    const ws = new FakeWs()
    const stream = makeStream(ws)
    const dec = startWithConfig(stream, ws)
    dec.decodeQueueSize = 2
    ws.handler!(DELTA_FRAME) // 触发追赶

    dec.decodeQueueSize = 0
    ws.handler!(DELTA_FRAME) // 追赶中：仍丢
    ws.handler!(KEY_FRAME) // IDR 自包含：唯一干净恢复点
    expect(dec.decode).toHaveBeenCalledTimes(1)

    ws.handler!(DELTA_FRAME) // 已退出追赶：正常提交
    expect(dec.decode).toHaveBeenCalledTimes(2)
  })

  it('追赶中 IDR 且队列仍积压：走既有重建保护', () => {
    const ws = new FakeWs()
    const stream = makeStream(ws)
    const dec = startWithConfig(stream, ws)
    dec.decodeQueueSize = 2
    ws.handler!(DELTA_FRAME) // 触发追赶

    ws.handler!(KEY_FRAME) // 队列未回落 + IDR

    expect(FakeVideoDecoder.instances).toHaveLength(2)
    expect(dec.state).toBe('closed')
    const rebuilt = FakeVideoDecoder.instances.at(-1)!
    expect(rebuilt.decode).toHaveBeenCalledTimes(1)
  })

  it('config 重发（initDecoder）后追赶状态复位', () => {
    const ws = new FakeWs()
    const stream = makeStream(ws)
    const dec = startWithConfig(stream, ws)
    dec.decodeQueueSize = 2
    ws.handler!(DELTA_FRAME) // 触发追赶

    ws.handler!(CONFIG_MSG) // 重建解码器：复位点
    const rebuilt = FakeVideoDecoder.instances.at(-1)!
    expect(FakeVideoDecoder.instances).toHaveLength(2)

    ws.handler!(DELTA_FRAME) // 复位后：非追赶态正常提交
    expect(rebuilt.decode).toHaveBeenCalledTimes(1)
  })

  it('suspend/resume 重建后追赶状态复位', () => {
    const ws = new FakeWs()
    const stream = makeStream(ws)
    const dec = startWithConfig(stream, ws)
    dec.decodeQueueSize = 2
    ws.handler!(DELTA_FRAME) // 触发追赶

    stream.suspend()
    stream.resume()
    const rebuilt = FakeVideoDecoder.instances.at(-1)!

    ws.handler!(DELTA_FRAME) // 复位后正常提交
    expect(rebuilt.decode).toHaveBeenCalledTimes(1)
  })
})

describe('warn 限速（方案 31）', () => {
  // 匹配任一参数（F1-12 改造后 reason 不再位于首参）
  const countWarns = (needle: string) =>
    warnSpy.mock.calls.filter(c => c.some(a => String(a).includes(needle))).length

  it('解码器未就绪连续多帧：warn 仅 1 次', () => {
    const ws = new FakeWs()
    const stream = makeStream(ws)
    stream.start()

    ws.handler!(KEY_FRAME)
    ws.handler!(KEY_FRAME)
    ws.handler!(KEY_FRAME)

    expect(countWarns('decoder not ready')).toBe(1)
  })

  it('未就绪满 100 帧：每 WARN_SUMMARY_EVERY 条汇总 warn 一次（方案 51 F1-12）', () => {
    const ws = new FakeWs()
    const stream = makeStream(ws)
    stream.start()

    for (let i = 0; i < 99; i++) ws.handler!(KEY_FRAME) // 首次 warn 后静默
    expect(countWarns('decoder not ready')).toBe(1)

    ws.handler!(KEY_FRAME) // 第 100 条：汇总
    expect(countWarns('decoder not ready')).toBe(2)
  })

  it('恢复 configured 后再未就绪：再 warn 1 次', () => {
    const ws = new FakeWs()
    const stream = makeStream(ws)
    stream.start()

    ws.handler!(KEY_FRAME) // 未就绪：warn #1
    expect(countWarns('decoder not ready')).toBe(1)

    ws.handler!(CONFIG_MSG) // 恢复 configured：重置限速标志
    FakeVideoDecoder.instances.at(-1)!.state = 'closed' // 解码器再次失效

    ws.handler!(KEY_FRAME) // 再未就绪：warn #2
    ws.handler!(KEY_FRAME) // 限速：静默
    expect(countWarns('decoder not ready')).toBe(2)
  })

  it('尺寸错配连续多帧：warn 仅 1 次；恢复匹配后重置', () => {
    const ws = new FakeWs()
    const stream = makeStream(ws)
    const dec = startWithConfig(stream, ws)
    const bad = {
      displayWidth: 320, displayHeight: 240, close: vi.fn(),
    } as unknown as VideoFrame
    const good = {
      displayWidth: 640, displayHeight: 480, close: vi.fn(),
    } as unknown as VideoFrame

    dec.init.output(bad)
    dec.init.output(bad)
    dec.init.output(bad)
    expect(countWarns('Frame size mismatch')).toBe(1)

    dec.init.output(good) // 恢复匹配：重置
    dec.init.output(bad) // 再次错配：warn #2
    dec.init.output(bad) // 限速：静默
    expect(countWarns('Frame size mismatch')).toBe(2)
  })
})

describe('解码器回调与渲染', () => {
  it('stop 后在途输出帧直接释放且不再产生 streaming 状态（方案 51 F1-08）', () => {
    const ws = new FakeWs()
    const stream = makeStream(ws)
    const states: string[] = []
    stream.setStateChangeHandler((s) => states.push(s))
    const dec = startWithConfig(stream, ws)

    stream.stop()
    states.length = 0

    const frame = {
      displayWidth: 640, displayHeight: 480, close: vi.fn(),
    } as unknown as VideoFrame
    dec.init.output(frame)

    expect(frame.close).toHaveBeenCalledTimes(1)
    expect(states).toEqual([])
  })

  it('输出帧绘制到 canvas 并切换到 streaming（状态回调仅一次）', () => {
    const ws = new FakeWs()
    const stream = makeStream(ws)
    const states: string[] = []
    stream.setStateChangeHandler(s => states.push(s))
    const dec = startWithConfig(stream, ws)
    const frame = {
      displayWidth: 640, displayHeight: 480, close: vi.fn(),
    } as unknown as VideoFrame

    dec.init.output(frame)

    expect(ctxDrawImage).toHaveBeenCalledWith(frame, 0, 0, 640, 480)
    expect(frame.close).toHaveBeenCalledTimes(1)
    expect(stream.state).toBe('streaming')

    dec.init.output(frame)
    expect(states.filter(s => s === 'streaming')).toHaveLength(1)
    stream.stop()
  })

  it('输出帧尺寸与 canvas 不一致时仅告警，仍正常绘制', () => {
    const ws = new FakeWs()
    const stream = makeStream(ws)
    const dec = startWithConfig(stream, ws)
    const frame = {
      displayWidth: 320, displayHeight: 240, close: vi.fn(),
    } as unknown as VideoFrame

    dec.init.output(frame)

    expect(warnSpy).toHaveBeenCalled()
    expect(ctxDrawImage).toHaveBeenCalledWith(frame, 0, 0, 640, 480)
    expect(stream.state).toBe('streaming')
  })

  it('解码器 error 回调进入 error 状态', () => {
    const ws = new FakeWs()
    const stream = makeStream(ws)
    const dec = startWithConfig(stream, ws)

    dec.init.error(new DOMException('bad frame', 'EncodingError'))

    expect(stream.state).toBe('error')
    expect(stream.stats.error).toBe('Decoder error: EncodingError: bad frame')
    expect(errorSpy).toHaveBeenCalled()
  })
})

describe('suspend / resume 探测模式', () => {
  it('suspend 关闭解码器，后续帧只计数不解码（幂等）', () => {
    const ws = new FakeWs()
    const stream = makeStream(ws)
    const dec = startWithConfig(stream, ws)

    stream.suspend()

    expect(stream.suspended).toBe(true)
    expect(stream.state).toBe('stopped')
    expect(dec.state).toBe('closed')

    ws.handler!(KEY_FRAME)
    expect(stream.probeStats.frameCount).toBe(1)
    expect(stream.probeStats.lastFrameTime).toBeGreaterThan(0)
    expect(dec.decode).not.toHaveBeenCalled()

    stream.suspend() // 幂等，不重复通知
    expect(stream.suspended).toBe(true)
    expect(stream.state).toBe('stopped')
  })

  it('suspend 期间新 config 只暂存，resume 时用于重建解码器', async () => {
    const ws = new FakeWs()
    const stream = makeStream(ws)
    startWithConfig(stream, ws)
    stream.suspend()

    ws.handler!(CONFIG_MSG)
    expect(FakeVideoDecoder.instances).toHaveLength(1)

    const updates: unknown[] = []
    stream.setStatsUpdateHandler(s => updates.push(s))
    stream.resume()

    expect(stream.suspended).toBe(false)
    expect(stream.state).toBe('configuring')
    expect(FakeVideoDecoder.instances).toHaveLength(2)
    const rebuilt = FakeVideoDecoder.instances.at(-1)!
    expect(rebuilt.config?.codec).toBe('avc1.42E01E')
    expect(stream.probeStats).toEqual({ frameCount: 0, lastFrameTime: 0 })

    // resume 后帧恢复正常解码
    ws.handler!(KEY_FRAME)
    expect(rebuilt.decode).toHaveBeenCalledTimes(1)

    // resume 重建的 fps 定时器恢复上报
    await vi.advanceTimersByTimeAsync(1000)
    expect(updates).toHaveLength(1)

    stream.stop()
  })

  it('suspend 期间到达的新 config 在 resume 后生效', () => {
    const ws = new FakeWs()
    const stream = makeStream(ws)
    startWithConfig(stream, ws)
    stream.suspend()

    ws.handler!(JSON.stringify({
      type: 'config', codec: 'avc1.640028', width: 1280, height: 720,
      description: '000000016742e01e890000000168ce3880',
    }))
    stream.resume()

    const rebuilt = FakeVideoDecoder.instances.at(-1)!
    expect(rebuilt.config?.codec).toBe('avc1.640028')
    expect(stream.stats.width).toBe(1280)
    expect(stream.stats.height).toBe(720)
  })

  it('suspend 期间新 config 同样更新保活间隔', () => {
    const ws = new FakeWs()
    const stream = makeStream(ws)
    startWithConfig(stream, ws)
    stream.suspend()

    ws.handler!(JSON.stringify({
      type: 'config', codec: 'avc1.42E01E', width: 640, height: 480,
      description: '000000016742e01e890000000168ce3880',
      idle_reset_seconds: 5,
    }))

    expect(stream.keepaliveIntervalMs).toBe(5000)
    stream.stop()
  })

  it('resume 无历史 config 时不建解码器', () => {
    const ws = new FakeWs()
    const stream = makeStream(ws)
    stream.start()
    stream.suspend()

    stream.resume()

    expect(stream.suspended).toBe(false)
    expect(stream.state).toBe('configuring')
    expect(FakeVideoDecoder.instances).toHaveLength(0)
  })

  it('resume 发送 request_keyframe（每次 resume 周期恰一次）', () => {
    const ws = new FakeWs()
    const stream = makeStream(ws)
    const dec = startWithConfig(stream, ws)
    const frame = {
      displayWidth: 640, displayHeight: 480, close: vi.fn(),
    } as unknown as VideoFrame
    dec.init.output(frame) // 进入 streaming

    stream.suspend()
    stream.resume()

    expect(ws.sent).toEqual([{ op: 'request_keyframe' }])

    // 未 suspend 时 resume 早退，不重复发送
    stream.resume()
    expect(ws.sent).toEqual([{ op: 'request_keyframe' }])

    // 新一轮 suspend/resume 周期再发一次
    stream.suspend()
    stream.resume()
    expect(ws.sent).toEqual([{ op: 'request_keyframe' }, { op: 'request_keyframe' }])

    stream.stop()
  })

  it('未 suspend 时 resume 无操作', () => {
    const ws = new FakeWs()
    const stream = makeStream(ws)
    stream.start()

    stream.resume()

    expect(stream.state).toBe('configuring')
    expect(FakeVideoDecoder.instances).toHaveLength(0)
  })

  it('suspend 状态下 stop 会重置探测标记', () => {
    const ws = new FakeWs()
    const stream = makeStream(ws)
    startWithConfig(stream, ws)
    stream.suspend()

    stream.stop()

    expect(stream.suspended).toBe(false)
    expect(stream.state).toBe('stopped')
  })
})

describe('captureFrame（方案 40 A）', () => {
  it('渲染 canvas 经 toBlob 导出 PNG Blob', async () => {
    const ws = new FakeWs()
    const canvas = makeCanvas()
    const stream = makeStream(ws, canvas)
    const pngBlob = new Blob(['png'], { type: 'image/png' })
    const toBlobSpy = vi.spyOn(HTMLCanvasElement.prototype, 'toBlob')
      .mockImplementation((cb: BlobCallback) => cb(pngBlob))

    await expect(stream.captureFrame()).resolves.toBe(pngBlob)
    expect(toBlobSpy).toHaveBeenCalledWith(expect.any(Function), 'image/png')
  })

  it('toBlob 回调传 null 时 reject', async () => {
    const ws = new FakeWs()
    const stream = makeStream(ws)
    vi.spyOn(HTMLCanvasElement.prototype, 'toBlob')
      .mockImplementation((cb: BlobCallback) => cb(null))

    await expect(stream.captureFrame()).rejects.toThrow('canvas.toBlob 返回空数据')
  })
})

describe('WS 重连接线（方案 19 终审）', () => {
  it('start 注册重连回调：挂起时重连成功 → 自动 resume 并 request_keyframe', () => {
    const ws = new FakeWs()
    const stream = makeStream(ws)
    startWithConfig(stream, ws)
    expect(ws.reconnectHandler).toBeTypeOf('function')

    // WS 瞬断期间前端已回退截图（suspend）；重连成功后触发回调
    stream.suspend()
    ws.reconnectHandler!()

    expect(stream.suspended).toBe(false)
    expect(stream.state).toBe('configuring')
    expect(ws.sent).toEqual([{ op: 'request_keyframe' }])
    // resume 用缓存 config 重建解码器
    expect(FakeVideoDecoder.instances).toHaveLength(2)

    // resume 后帧恢复正常解码
    ws.handler!(KEY_FRAME)
    const rebuilt = FakeVideoDecoder.instances.at(-1)!
    expect(rebuilt.decode).toHaveBeenCalledTimes(1)

    stream.stop()
  })

  it('重连回调在非挂起态为空操作（resume 守卫），不发送多余消息', () => {
    const ws = new FakeWs()
    const stream = makeStream(ws)
    startWithConfig(stream, ws)
    expect(ws.reconnectHandler).toBeTypeOf('function')

    ws.reconnectHandler!()

    expect(stream.suspended).toBe(false)
    expect(stream.state).toBe('configuring')
    expect(ws.sent).toEqual([])
    stream.stop()
  })
})

describe('挂起态重新 start（方案 51 F1-01）', () => {
  it('suspend 后再 start 复位探测态，config 与帧恢复解码路径', () => {
    const ws = new FakeWs()
    const stream = makeStream(ws)
    startWithConfig(stream, ws)
    stream.suspend()

    stream.start()

    expect(stream.suspended).toBe(false)
    // start 后不再处于探测态：config 应直接重建解码器而非只暂存
    ws.handler!(CONFIG_MSG)
    expect(FakeVideoDecoder.instances).toHaveLength(2)
    const rebuilt = FakeVideoDecoder.instances.at(-1)!

    ws.handler!(KEY_FRAME)
    expect(rebuilt.decode).toHaveBeenCalledTimes(1)

    stream.stop()
  })
})

// ---------------------------------------------------------------------------
// h265（HEVC）解码链路泛化（方案 45 T4）
// ---------------------------------------------------------------------------

/** 后端 T3 同款特征向量（tests/unit/test_video.py HEVC_*_TEST 去起始码）：
 * SPS payload b0=0x01 → profile_space 0/profile 1；compat=0x60000000 → 位反转=6；
 * constraints 首字节 0xB0；level=120 → codec 串 hvc1.1.6.L120.B0。 */
const HEVC_VPS = new Uint8Array([0x40, 0x01, 0x0c, 0x01, 0xff, 0xff, 0x01, 0x60])
const HEVC_SPS = new Uint8Array([
  0x42, 0x01,                                  // HEVC NAL 头（type 33 SPS）
  0x01,                                        // profile_space=0, tier=0, profile_idc=1
  0x60, 0x00, 0x00, 0x00,                      // compat（SPS 序）→ 记录内位反转 = 6
  0xb0, 0x00, 0x00, 0x00, 0x00, 0x00,          // constraint indicator flags
  0x78,                                        // level_idc = 120
])
const HEVC_PPS = new Uint8Array([0x44, 0x01, 0x08, 0x00])

/** 按 ISO/IEC 14496-15 §8.3.3.1.2 HEVCDecoderConfigurationRecord 布局手算的期望字节。 */
const EXPECTED_HVCC = new Uint8Array([
  0x01,                                        // configurationVersion
  0x01,                                        // profile_space(2)|tier(1)|profile_idc(5)
  0x00, 0x00, 0x00, 0x06,                      // general_profile_compatibility_flags（位反转）
  0xb0, 0x00, 0x00, 0x00, 0x00, 0x00,          // general_constraint_indicator_flags
  0x78,                                        // general_level_idc
  0xf0, 0x00,                                  // reserved(1111) + min_spatial_segmentation_idc=0（未指定）
  0xfc,                                        // reserved(111111) + parallelismType=0（未指定）
  0xfc,                                        // reserved(111111) + chromaFormat=0（未指定）
  0xf8,                                        // reserved(11111) + bitDepthLumaMinus8=0（未指定）
  0xf8,                                        // reserved(11111) + bitDepthChromaMinus8=0（未指定）
  0x00, 0x00,                                  // avgFrameRate=0（未指定）
  0x0f,                                        // constantFrameRate=0|numTemporalLayers=1|temporalIdNested=1|lengthSizeMinusOne=3
  0x03,                                        // numOfArrays = 3（VPS/SPS/PPS）
  0xa0, 0x00, 0x01, 0x00, 0x08, ...HEVC_VPS,   // VPS 数组：tag(complete=1,type=32) numNalus=1 len=8
  0xa1, 0x00, 0x01, 0x00, 0x0e, ...HEVC_SPS,   // SPS 数组：tag(type=33) numNalus=1 len=14
  0xa2, 0x00, 0x01, 0x00, 0x04, ...HEVC_PPS,   // PPS 数组：tag(type=34) numNalus=1 len=4
])

/** Annex B 化（4 字节起始码前缀）+ hex 化，复刻后端 description 生成方式。 */
function annexBHex(...nalus: Uint8Array[]): string {
  const out: number[] = []
  for (const n of nalus) out.push(0, 0, 0, 1, ...n)
  return out.map(b => b.toString(16).padStart(2, '0')).join('')
}

const CONFIG_MSG_HEVC = JSON.stringify({
  type: 'config',
  codec: 'hvc1.1.6.L120.B0',
  width: 1080,
  height: 1920,
  description: annexBHex(HEVC_VPS, HEVC_SPS, HEVC_PPS),
})

/** HEVC IDR_W_RADL（type 19 → 头 0x26）与 TRAIL_R（type 1 → 头 0x02）帧载荷。 */
const HEVC_KEY_PAYLOAD = new Uint8Array([0, 0, 0, 1, 0x26, 0x01, 0xff])
const HEVC_DELTA_PAYLOAD = new Uint8Array([0, 0, 0, 1, 0x02, 0x01, 0xff])

describe('buildHvcCDescription（ISO 14496-15 hvcC）', () => {
  it('后端特征向量逐字节符合 hvcC 布局', () => {
    expect(buildHvcCDescription(HEVC_VPS, HEVC_SPS, HEVC_PPS)).toEqual(EXPECTED_HVCC)
  })

  it('头部关键字段（brief 钉死断言）：version=1、numOfArrays=3、byte21=0x0f', () => {
    const desc = buildHvcCDescription(HEVC_VPS, HEVC_SPS, HEVC_PPS)
    expect(desc[0]).toBe(1)
    expect(desc[22]).toBe(3)
    expect(desc[21]).toBe(0x0f)
  })

  it('数组区：tag 0xA0/0xA1/0xA2 + numNalus=1 + 2B 长度前缀 + 含 NAL 头的完整 NALU', () => {
    const desc = buildHvcCDescription(HEVC_VPS, HEVC_SPS, HEVC_PPS)
    expect(desc[23]).toBe(0xa0)
    expect(desc.slice(24, 26)).toEqual(new Uint8Array([0x00, 0x01]))
    expect(desc.slice(26, 28)).toEqual(new Uint8Array([0x00, HEVC_VPS.length]))
    expect(desc.slice(28, 28 + HEVC_VPS.length)).toEqual(HEVC_VPS)

    const spsTag = 28 + HEVC_VPS.length
    expect(desc[spsTag]).toBe(0xa1)
    expect(desc.slice(spsTag + 3, spsTag + 5))
      .toEqual(new Uint8Array([0x00, HEVC_SPS.length]))

    const ppsTag = spsTag + 5 + HEVC_SPS.length
    expect(desc[ppsTag]).toBe(0xa2)
    expect(desc.slice(ppsTag + 3, ppsTag + 5))
      .toEqual(new Uint8Array([0x00, HEVC_PPS.length]))
    expect(desc.length).toBe(ppsTag + 5 + HEVC_PPS.length)
  })

  it('SPS 去 NAL 头后不足 12B（profile/level 段缺失）返回空', () => {
    const shortSps = new Uint8Array([0x42, 0x01, 0x01, 0x02, 0x03])
    expect(buildHvcCDescription(HEVC_VPS, shortSps, HEVC_PPS).byteLength).toBe(0)
  })
})

describe('h265 config 路由（方案 45 T4）', () => {
  it('hvc1 config：VPS/SPS/PPS 按 HEVC NAL type 32/33/34 提取并构建 hvcC description', () => {
    const ws = new FakeWs()
    const canvas = makeCanvas()
    const stream = makeStream(ws, canvas)

    stream.start()
    ws.handler!(CONFIG_MSG_HEVC)

    const dec = FakeVideoDecoder.instances.at(-1)
    expect(dec?.config?.codec).toBe('hvc1.1.6.L120.B0')
    expect(dec?.config?.description).toBeInstanceOf(Uint8Array)
    expect(Array.from(dec!.config!.description as Uint8Array))
      .toEqual(Array.from(EXPECTED_HVCC))
    expect(canvas.width).toBe(1080)
    expect(canvas.height).toBe(1920)
    expect(stream.state).toBe('configuring')

    stream.stop()
  })

  it('hev1. 前缀同样路由到 h265 family', () => {
    const ws = new FakeWs()
    const stream = makeStream(ws)

    stream.start()
    ws.handler!(JSON.stringify({
      ...JSON.parse(CONFIG_MSG_HEVC),
      codec: 'hev1.1.6.L120.B0',
    }))

    const dec = FakeVideoDecoder.instances.at(-1)
    expect(dec?.config?.codec).toBe('hev1.1.6.L120.B0')
    expect(Array.from(dec!.config!.description as Uint8Array))
      .toEqual(Array.from(EXPECTED_HVCC))

    stream.stop()
  })

  it('h265 config 缺 VPS 时进入 error 且不建解码器', () => {
    const ws = new FakeWs()
    const stream = makeStream(ws)

    stream.start()
    ws.handler!(JSON.stringify({
      type: 'config',
      codec: 'hvc1.1.6.L120.B0',
      width: 1080,
      height: 1920,
      description: annexBHex(HEVC_SPS, HEVC_PPS),
    }))

    expect(stream.state).toBe('error')
    expect(stream.stats.error).toContain('Failed to extract VPS/SPS/PPS')
    expect(FakeVideoDecoder.instances).toHaveLength(0)
  })

  it('h265 SPS 过短导致 hvcC 构建失败时进入 error', () => {
    const ws = new FakeWs()
    const stream = makeStream(ws)

    stream.start()
    ws.handler!(JSON.stringify({
      type: 'config',
      codec: 'hvc1.1.6.L120.B0',
      width: 1080,
      height: 1920,
      description: annexBHex(HEVC_VPS, new Uint8Array([0x42, 0x01, 0x01, 0x02]), HEVC_PPS),
    }))

    expect(stream.state).toBe('error')
    expect(stream.stats.error).toBe('Failed to build hvcC description')
  })

  it('h265 二进制帧：IRAP 头 0x26 判 key、0x02 判 delta 并送解码', () => {
    const ws = new FakeWs()
    const stream = makeStream(ws)
    stream.start()
    ws.handler!(CONFIG_MSG_HEVC)

    ws.handler!(frameMessage(33_333, HEVC_KEY_PAYLOAD))
    expect(FakeEncodedVideoChunk.instances.at(-1)?.init.type).toBe('key')
    expect(FakeEncodedVideoChunk.instances.at(-1)?.init.timestamp).toBe(33_333)

    ws.handler!(frameMessage(66_666, HEVC_DELTA_PAYLOAD))
    expect(FakeEncodedVideoChunk.instances.at(-1)?.init.type).toBe('delta')

    stream.stop()
  })

  it('h264 会话不受影响：HEVC IRAP 头 0x26 在 h264 family 下判 delta', () => {
    const ws = new FakeWs()
    const stream = makeStream(ws)
    startWithConfig(stream, ws)

    // 0x26 & 0x1F = 6 ≠ 5 → 非 IDR（h264 语义零回归）
    ws.handler!(frameMessage(33_333, HEVC_KEY_PAYLOAD))
    expect(FakeEncodedVideoChunk.instances.at(-1)?.init.type).toBe('delta')

    ws.handler!(KEY_FRAME)
    expect(FakeEncodedVideoChunk.instances.at(-1)?.init.type).toBe('key')

    stream.stop()
  })
})