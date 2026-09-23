/**
 * H264VideoStream 行为测试
 * =========================
 *
 * 覆盖：
 *   - hardwareAcceleration 选项（方案 17 实施项 4）
 *   - start/stop/suspend/resume 生命周期与 getter
 *   - config / restarting / error 消息处理与全部错误分支
 *   - 二进制帧：丢帧、关键帧重建解码器、AVCC 转换、decode 异常吞掉
 *   - 解码器 output/error 回调与 canvas 绘制、streaming 状态流转
 */
import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest'
import { H264VideoStream } from './h264VideoStream'
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

/** 4 字节起始码的 IDR 关键帧（NAL type 5）与 delta 帧（NAL type 1）。 */
const KEY_FRAME = new Uint8Array([0, 0, 0, 1, 0x65, 0x11, 0x22, 0x33]).buffer
const DELTA_FRAME = new Uint8Array([0, 0, 0, 1, 0x41, 0x99, 0x88]).buffer

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

  setMessageHandler(handler: (data: unknown) => void) {
    this.handler = handler
    this.handlerSetCount++
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

  it('start 将内部 WebSocket 的 binaryType 设为 arraybuffer', () => {
    const ws = new FakeWs()
    ws.ws = { binaryType: 'blob' } as unknown as WebSocket
    const stream = makeStream(ws)

    stream.start()

    expect(ws.ws.binaryType).toBe('arraybuffer')
    expect(stream.state).toBe('configuring')
    expect(logSpy).toHaveBeenCalledWith('[H264] WebSocket binaryType set to arraybuffer')
  })

  it('重复 start 不再重复注册消息处理器', () => {
    const ws = new FakeWs()
    const stream = makeStream(ws)

    stream.start()
    stream.start()

    expect(ws.handlerSetCount).toBe(1)
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
      '[H264] Frame dropped: decoder not ready, state:', undefined, 'decoder exists:', false,
    )
    expect(stream.stats.frameCount).toBe(0)
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

describe('解码器回调与渲染', () => {
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