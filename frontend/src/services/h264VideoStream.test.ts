/**
 * H264VideoStream hardwareAcceleration 选项测试（方案 17 实施项 4）
 * ===================================================================
 *
 * 覆盖：
 *   - 显式传入 'prefer-software' 时 configure 携带该偏好
 *   - 未传入时 configure 不含 hardwareAcceleration 键（走浏览器默认）
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
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

class FakeVideoDecoder {
  static instances: FakeVideoDecoder[] = []
  state = 'unconfigured'
  config: VideoDecoderConfig | null = null

  constructor(_init: unknown) {
    FakeVideoDecoder.instances.push(this)
  }
  configure(config: VideoDecoderConfig) {
    this.config = config
    this.state = 'configured'
  }
  close() {
    this.state = 'closed'
  }
}

class FakeWs {
  handler: ((data: unknown) => void) | null = null
  ws: WebSocket | null = null

  setMessageHandler(handler: (data: unknown) => void) {
    this.handler = handler
  }
}

function makeCanvas() {
  const canvas = document.createElement('canvas')
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({
    drawImage: vi.fn(),
  } as unknown as CanvasRenderingContext2D)
  return canvas
}

/** 启动流并喂入 config 消息，返回创建出的 FakeVideoDecoder。 */
function startWithConfig(stream: H264VideoStream, ws: FakeWs): FakeVideoDecoder {
  stream.start()
  ws.handler!(CONFIG_MSG)
  const dec = FakeVideoDecoder.instances.at(-1)
  if (!dec) throw new Error('VideoDecoder not created')
  return dec
}

beforeEach(() => {
  FakeVideoDecoder.instances = []
  vi.stubGlobal('VideoDecoder', FakeVideoDecoder)
  vi.stubGlobal('EncodedVideoChunk', class {})
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