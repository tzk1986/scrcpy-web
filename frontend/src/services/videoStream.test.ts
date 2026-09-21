/**
 * 视频流服务测试（截屏轮询方案）
 * ==============================
 *
 * mock ./api 的 screenshot 与浏览器图像 API（createImageBitmap / Image /
 * ObjectURL），验证：
 *   - 开始/停止的状态流转与定时器生命周期
 *   - 帧计数与 FPS 统计上报
 *   - 截屏失败进入 error 状态
 *   - ImageBitmap 渲染与 Image+ObjectURL 降级渲染、URL 释放
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { VideoStream, type VideoStreamState, type VideoStreamStats } from './videoStream'
import type { WebSocketService } from './websocket'

const { screenshotMock } = vi.hoisted(() => ({ screenshotMock: vi.fn() }))

vi.mock('./api', () => ({
  api: { screenshot: screenshotMock },
}))

/** VideoStream 当前不使用 ws（输入事件由外部处理），传空对象即可 */
const fakeWs = {} as unknown as WebSocketService

const drawImage = vi.fn()

class FakeImage {
  static instances: FakeImage[] = []
  onload: (() => void) | null = null
  width = 320
  height = 240
  src = ''
  constructor() {
    FakeImage.instances.push(this)
  }
}

function stubGetContext(value: CanvasRenderingContext2D | null) {
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext')
    .mockReturnValue(value as CanvasRenderingContext2D)
}

function stubBitmap(width = 640, height = 480) {
  const close = vi.fn()
  const factory = vi.fn().mockResolvedValue({ width, height, close })
  vi.stubGlobal('createImageBitmap', factory)
  return { factory, close }
}

let urlSeq = 0
const createObjectURL = vi.fn()
const revokeObjectURL = vi.fn()

/** 等待 createImageBitmap / Blob 等 promise 链推进 */
async function flush() {
  for (let i = 0; i < 5; i++) await Promise.resolve()
}

beforeEach(() => {
  screenshotMock.mockReset().mockResolvedValue(new Blob(['frame']))
  drawImage.mockClear()
  FakeImage.instances = []
  urlSeq = 0
  createObjectURL.mockReset().mockImplementation(() => `blob:mock-${++urlSeq}`)
  revokeObjectURL.mockReset()
  Object.defineProperty(URL, 'createObjectURL', { value: createObjectURL, configurable: true })
  Object.defineProperty(URL, 'revokeObjectURL', { value: revokeObjectURL, configurable: true })
  vi.stubGlobal('Image', FakeImage)
  stubBitmap()
  stubGetContext({ drawImage } as unknown as CanvasRenderingContext2D)
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('初始状态与 getter', () => {
  it('未开始时为 idle，统计全为零值', () => {
    const stream = new VideoStream('dev1', document.createElement('canvas'), fakeWs)
    expect(stream.state).toBe('idle')
    expect(stream.stats).toEqual({
      frameCount: 0,
      fps: 0,
      lastFrameTime: 0,
      state: 'idle',
      error: null,
    })
  })
})

describe('start / stop 生命周期', () => {
  it('start 立即抓取首帧：渲染、计数、状态回调', async () => {
    const canvas = document.createElement('canvas')
    const stream = new VideoStream('dev1', canvas, fakeWs)
    const states: VideoStreamState[] = []
    const stats: VideoStreamStats[] = []
    stream.setStateChangeHandler(s => states.push(s))
    stream.setStatsUpdateHandler(s => stats.push(s))

    await stream.start()
    await flush()

    expect(screenshotMock).toHaveBeenCalledWith('dev1')
    expect(canvas.width).toBe(640)
    expect(canvas.height).toBe(480)
    expect(canvas.style.aspectRatio).toBe('640 / 480')
    expect(drawImage).toHaveBeenCalledTimes(1)
    expect(stream.stats.frameCount).toBe(1)
    expect(stream.stats.lastFrameTime).toBeGreaterThan(0)
    expect(stream.stats.state).toBe('streaming')
    expect(states).toEqual(['streaming'])

    stream.stop()
    expect(states).toEqual(['streaming', 'stopped'])
    expect(stream.state).toBe('stopped')
  })

  it('canvas 尺寸与位图一致时不重设尺寸与比例', async () => {
    const canvas = document.createElement('canvas')
    canvas.width = 640
    canvas.height = 480
    const stream = new VideoStream('dev1', canvas, fakeWs)

    await stream.start()
    await flush()

    expect(canvas.width).toBe(640)
    expect(canvas.style.aspectRatio).toBe('')
    stream.stop()
  })

  it('重复 start 直接返回（已在 streaming）', async () => {
    const stream = new VideoStream('dev1', document.createElement('canvas'), fakeWs)
    await stream.start()
    await stream.start()
    expect(screenshotMock).toHaveBeenCalledTimes(1)
    stream.stop()
  })

  it('按 refreshInterval 周期抓帧，stop 后停止', async () => {
    vi.useFakeTimers()
    const stream = new VideoStream('dev1', document.createElement('canvas'), fakeWs, 1500)

    await stream.start()
    expect(screenshotMock).toHaveBeenCalledTimes(1)

    await vi.advanceTimersByTimeAsync(1500)
    expect(screenshotMock).toHaveBeenCalledTimes(2)

    await vi.advanceTimersByTimeAsync(1500)
    expect(screenshotMock).toHaveBeenCalledTimes(3)

    stream.stop()
    await vi.advanceTimersByTimeAsync(10_000)
    expect(screenshotMock).toHaveBeenCalledTimes(3)
  })

  it('FPS 定时器每秒上报统计', async () => {
    vi.useFakeTimers()
    const stream = new VideoStream('dev1', document.createElement('canvas'), fakeWs)
    const updates: VideoStreamStats[] = []
    stream.setStatsUpdateHandler(s => updates.push(s))

    await stream.start()
    await vi.advanceTimersByTimeAsync(1000)

    expect(updates).toHaveLength(1)
    expect(updates[0].fps).toBe(1)
    expect(updates[0].frameCount).toBe(1)

    stream.stop()
  })

  it('已停止后重复 stop 不抛异常', () => {
    const stream = new VideoStream('dev1', document.createElement('canvas'), fakeWs)
    expect(() => stream.stop()).not.toThrow()
    expect(() => stream.stop()).not.toThrow()
  })
})

describe('错误处理', () => {
  it('截屏失败进入 error 状态并携带错误信息', async () => {
    screenshotMock.mockRejectedValueOnce(new Error('capture failed'))
    const stream = new VideoStream('dev1', document.createElement('canvas'), fakeWs)
    const states: VideoStreamState[] = []
    stream.setStateChangeHandler(s => states.push(s))

    await stream.start()

    expect(stream.state).toBe('error')
    expect(stream.stats.error).toBe('capture failed')
    expect(states).toEqual(['streaming', 'error'])

    stream.stop()
  })

  it('error 后周期回调不再抓帧', async () => {
    vi.useFakeTimers()
    screenshotMock.mockRejectedValueOnce(new Error('boom'))
    const stream = new VideoStream('dev1', document.createElement('canvas'), fakeWs, 100)

    await stream.start()
    await vi.advanceTimersByTimeAsync(1000)

    expect(screenshotMock).toHaveBeenCalledTimes(1)
    stream.stop()
  })

  it('canvas 2d context 不可用时跳过渲染但帧计数继续', async () => {
    stubGetContext(null)
    const stream = new VideoStream('dev1', document.createElement('canvas'), fakeWs)

    await stream.start()
    await flush()

    expect(drawImage).not.toHaveBeenCalled()
    expect(stream.stats.frameCount).toBe(1)
    stream.stop()
  })
})

describe('降级渲染（Image + ObjectURL）', () => {
  it('createImageBitmap 失败时降级，onload 后绘制并释放 URL', async () => {
    vi.stubGlobal('createImageBitmap', vi.fn().mockRejectedValue(new Error('unsupported')))
    const canvas = document.createElement('canvas')
    const stream = new VideoStream('dev1', canvas, fakeWs)

    await stream.start()
    await flush()

    expect(createObjectURL).toHaveBeenCalledTimes(1)
    const img = FakeImage.instances.at(-1)
    expect(img).toBeDefined()
    expect(img!.src).toBe('blob:mock-1')

    img!.onload!()

    expect(canvas.width).toBe(320)
    expect(canvas.height).toBe(240)
    expect(drawImage).toHaveBeenCalledWith(img, 0, 0)
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:mock-1')

    stream.stop()
  })

  it('连续降级帧会释放上一帧的 ObjectURL', async () => {
    vi.useFakeTimers()
    vi.stubGlobal('createImageBitmap', vi.fn().mockRejectedValue(new Error('unsupported')))
    const stream = new VideoStream('dev1', document.createElement('canvas'), fakeWs, 100)

    await stream.start()
    expect(createObjectURL).toHaveBeenCalledTimes(1)

    await vi.advanceTimersByTimeAsync(100)

    expect(createObjectURL).toHaveBeenCalledTimes(2)
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:mock-1')
    stream.stop()
  })

  it('stop 时释放尚未加载完成的 ObjectURL', async () => {
    vi.stubGlobal('createImageBitmap', vi.fn().mockRejectedValue(new Error('unsupported')))
    const stream = new VideoStream('dev1', document.createElement('canvas'), fakeWs)

    await stream.start()
    await flush()

    stream.stop()
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:mock-1')
  })
})