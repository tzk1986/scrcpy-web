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

const { screenshotMock, screenshotRawMock } = vi.hoisted(() => ({
  screenshotMock: vi.fn(),
  screenshotRawMock: vi.fn(),
}))

vi.mock('./api', () => ({
  api: { screenshot: screenshotMock, screenshotRaw: screenshotRawMock },
}))

/** VideoStream 当前不使用 ws（输入事件由外部处理），传空对象即可 */
const fakeWs = {} as unknown as WebSocketService

const drawImage = vi.fn()
const putImageData = vi.fn()

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

/**
 * 截屏请求总次数（方案 29）：raw 换档后首帧走 screenshotRaw，
 * 降级后走 screenshot——既有计数断言语义不变，改为两者之和。
 */
function totalCaptures() {
  return screenshotRawMock.mock.calls.length + screenshotMock.mock.calls.length
}

/**
 * 构造 raw 帧 Blob（方案 29）：screencap 帧头（LE uint32 序列）+ RGBA 像素。
 * headerSize 12 = Android 7 旧机（w/h/fmt），16 = 新机（w/h/fmt/colorspace）。
 */
function rawFrameBlob(w: number, h: number, fmt: number, headerSize: 12 | 16) {
  const header = new Uint8Array(headerSize)
  const dv = new DataView(header.buffer)
  dv.setUint32(0, w, true)
  dv.setUint32(4, h, true)
  dv.setUint32(8, fmt, true)
  const bytes = new Uint8Array(headerSize + w * h * 4)
  bytes.set(header, 0)
  return new Blob([bytes])
}

/** 头宣称 2x2（期望 12/16B 头 + 16B 像素），实际总量 20B → 推算头长 4B，非法 */
function malformedRawBlob() {
  const bytes = new Uint8Array(20)
  const dv = new DataView(bytes.buffer)
  dv.setUint32(0, 2, true)
  dv.setUint32(4, 2, true)
  dv.setUint32(8, 1, true)
  return new Blob([bytes])
}

beforeEach(() => {
  screenshotMock.mockReset().mockResolvedValue(new Blob(['frame']))
  screenshotRawMock.mockReset().mockResolvedValue({ blob: new Blob(['frame']), format: 'png' })
  drawImage.mockClear()
  putImageData.mockClear()
  FakeImage.instances = []
  urlSeq = 0
  createObjectURL.mockReset().mockImplementation(() => `blob:mock-${++urlSeq}`)
  revokeObjectURL.mockReset()
  Object.defineProperty(URL, 'createObjectURL', { value: createObjectURL, configurable: true })
  Object.defineProperty(URL, 'revokeObjectURL', { value: revokeObjectURL, configurable: true })
  vi.stubGlobal('Image', FakeImage)
  stubBitmap()
  stubGetContext({ drawImage, putImageData } as unknown as CanvasRenderingContext2D)
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

    expect(screenshotRawMock).toHaveBeenCalledWith('dev1')
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
    expect(totalCaptures()).toBe(1)
    stream.stop()
  })

  it('按 refreshInterval 周期抓帧，stop 后停止', async () => {
    vi.useFakeTimers()
    const stream = new VideoStream('dev1', document.createElement('canvas'), fakeWs, 1500)

    await stream.start()
    expect(totalCaptures()).toBe(1)

    await vi.advanceTimersByTimeAsync(1500)
    expect(totalCaptures()).toBe(2)

    await vi.advanceTimersByTimeAsync(1500)
    expect(totalCaptures()).toBe(3)

    stream.stop()
    await vi.advanceTimersByTimeAsync(10_000)
    expect(totalCaptures()).toBe(3)
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
    screenshotRawMock.mockRejectedValueOnce(new Error('capture failed'))
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
    screenshotRawMock.mockRejectedValueOnce(new Error('boom'))
    const stream = new VideoStream('dev1', document.createElement('canvas'), fakeWs, 100)

    await stream.start()
    await vi.advanceTimersByTimeAsync(1000)

    expect(totalCaptures()).toBe(1)
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

describe('输入事件驱动刷新（notifyInput，方案 27）', () => {
  it('notifyInput 在 debounce 160ms 后主动截一张', async () => {
    vi.useFakeTimers()
    const stream = new VideoStream('dev1', document.createElement('canvas'), fakeWs, 1500)

    await stream.start()
    expect(totalCaptures()).toBe(1) // 首帧

    stream.notifyInput()
    expect(totalCaptures()).toBe(1) // debounce 未到期

    await vi.advanceTimersByTimeAsync(160)
    expect(totalCaptures()).toBe(2) // 主动截一张

    stream.stop()
  })

  it('160ms 窗口内连续输入合并为一次刷新', async () => {
    vi.useFakeTimers()
    const stream = new VideoStream('dev1', document.createElement('canvas'), fakeWs, 1500)
    await stream.start()

    stream.notifyInput()
    await vi.advanceTimersByTimeAsync(100)
    stream.notifyInput()
    await vi.advanceTimersByTimeAsync(100)
    stream.notifyInput()
    await vi.advanceTimersByTimeAsync(160) // 最后一次重置后 160ms

    expect(totalCaptures()).toBe(2) // 首帧 + 1 张合并刷新

    // debounce 已消费：相位重置后下一周期帧在 1500ms 后，短推进无新帧
    await vi.advanceTimersByTimeAsync(400)
    expect(totalCaptures()).toBe(2)

    stream.stop()
  })

  it('截屏进行中收到输入：完成后立即补一张（单飞+尾部补帧）', async () => {
    vi.useFakeTimers()
    let release!: (frame: { blob: Blob; format: 'raw-rgba' | 'png' }) => void
    screenshotRawMock.mockReset().mockImplementation(
      () =>
        new Promise<{ blob: Blob; format: 'raw-rgba' | 'png' }>(res => {
          release = res
        }),
    )
    const stream = new VideoStream('dev1', document.createElement('canvas'), fakeWs, 1500)

    const startPromise = stream.start() // 首帧挂起（不 await）
    await flush()

    stream.notifyInput()
    await vi.advanceTimersByTimeAsync(160)
    // debounce 到期：首帧仍在途 → 标记 pending，不并发发第二张
    expect(totalCaptures()).toBe(1)

    release({ blob: new Blob(['a']), format: 'png' }) // 首帧完成 → pending 触发补帧
    await flush()
    // 补帧走降级后的 api.screenshot（默认已 resolve）→ 天然完成，无 pending
    expect(totalCaptures()).toBe(2)

    await startPromise
    stream.stop()
  })

  it('事件截屏完成后轮询相位重新起算（原相位点不再截）', async () => {
    vi.useFakeTimers()
    const stream = new VideoStream('dev1', document.createElement('canvas'), fakeWs, 1500)

    await stream.start() // t=0 首帧
    await vi.advanceTimersByTimeAsync(1000) // 下一周期截原定 t=1500

    stream.notifyInput()
    await vi.advanceTimersByTimeAsync(160) // t=1160 事件截屏（第 2 张）
    expect(totalCaptures()).toBe(2)

    await vi.advanceTimersByTimeAsync(340) // t=1500：原相位点被重置，不截
    expect(totalCaptures()).toBe(2)

    await vi.advanceTimersByTimeAsync(1160) // t=2660 = 1160 + 1500 新相位
    expect(totalCaptures()).toBe(3)

    stream.stop()
  })

  it('非 streaming 状态（idle/stopped）notifyInput 不触发截屏', async () => {
    vi.useFakeTimers()
    const stream = new VideoStream('dev1', document.createElement('canvas'), fakeWs)

    stream.notifyInput() // idle
    await vi.advanceTimersByTimeAsync(1000)
    expect(totalCaptures()).toBe(0)

    await stream.start()
    stream.stop()

    stream.notifyInput() // stopped
    await vi.advanceTimersByTimeAsync(1000)
    expect(totalCaptures()).toBe(1) // 仅 start 首帧
  })

  it('stop 后 debounce 定时器被清理，不产生迟发截屏', async () => {
    vi.useFakeTimers()
    const stream = new VideoStream('dev1', document.createElement('canvas'), fakeWs, 1500)
    await stream.start()

    stream.notifyInput()
    stream.stop()
    await vi.advanceTimersByTimeAsync(1000)

    expect(totalCaptures()).toBe(1) // 仅首帧
  })
})

describe('方案 29：raw 帧渲染与降级记忆', () => {
  it('16B 头 raw 帧 → putImageData 渲染并重设 canvas 尺寸', async () => {
    screenshotRawMock.mockResolvedValue({
      blob: rawFrameBlob(2, 2, 1, 16),
      format: 'raw-rgba',
    })
    const canvas = document.createElement('canvas')
    const stream = new VideoStream('dev1', canvas, fakeWs)

    await stream.start()

    expect(screenshotMock).not.toHaveBeenCalled()
    expect(stream.stats.frameCount).toBe(1)
    expect(canvas.width).toBe(2)
    expect(canvas.height).toBe(2)
    expect(canvas.style.aspectRatio).toBe('2 / 2')
    expect(drawImage).not.toHaveBeenCalled()
    expect(putImageData).toHaveBeenCalledTimes(1)
    const [imageData, x, y] = putImageData.mock.calls[0]
    expect(imageData).toBeInstanceOf(ImageData)
    expect(imageData.width).toBe(2)
    expect(imageData.height).toBe(2)
    expect(x).toBe(0)
    expect(y).toBe(0)
    stream.stop()
  })

  it('12B 头 raw 帧（Android 7 旧机）→ 同样 putImageData 渲染', async () => {
    screenshotRawMock.mockResolvedValue({
      blob: rawFrameBlob(2, 2, 1, 12),
      format: 'raw-rgba',
    })
    const canvas = document.createElement('canvas')
    const stream = new VideoStream('dev1', canvas, fakeWs)

    await stream.start()

    expect(stream.stats.frameCount).toBe(1)
    expect(canvas.width).toBe(2)
    expect(putImageData).toHaveBeenCalledTimes(1)
    expect(putImageData.mock.calls[0][0]).toBeInstanceOf(ImageData)
    stream.stop()
  })

  it('fmt≠1 帧 → 丢弃该帧、置降级记忆，后续走 PNG 路径', async () => {
    vi.useFakeTimers()
    screenshotRawMock.mockResolvedValue({
      blob: rawFrameBlob(2, 2, 0, 16),
      format: 'raw-rgba',
    })
    const stream = new VideoStream('dev1', document.createElement('canvas'), fakeWs, 1500)

    await stream.start()

    expect(putImageData).not.toHaveBeenCalled()
    expect(drawImage).not.toHaveBeenCalled()
    expect(stream.stats.frameCount).toBe(0) // 该帧被丢弃，不计数
    expect(screenshotMock).not.toHaveBeenCalled()

    await vi.advanceTimersByTimeAsync(1500) // 下一周期：降级后走 PNG
    await flush()

    expect(screenshotRawMock).toHaveBeenCalledTimes(1) // 不再试 raw
    expect(screenshotMock).toHaveBeenCalledTimes(1)
    expect(stream.stats.frameCount).toBe(1)
    expect(drawImage).toHaveBeenCalledTimes(1)

    stream.stop()
  })

  it('头长度不在 {12,16} → 丢弃该帧并降级', async () => {
    screenshotRawMock.mockResolvedValue({
      blob: malformedRawBlob(),
      format: 'raw-rgba',
    })
    const stream = new VideoStream('dev1', document.createElement('canvas'), fakeWs)

    await stream.start()

    expect(putImageData).not.toHaveBeenCalled()
    expect(stream.stats.frameCount).toBe(0)
    stream.stop()
  })

  it('数据长度不足（连最小头都放不下）→ 丢弃该帧并降级', async () => {
    screenshotRawMock.mockResolvedValue({
      blob: new Blob([new Uint8Array(8)]),
      format: 'raw-rgba',
    })
    const stream = new VideoStream('dev1', document.createElement('canvas'), fakeWs)

    await stream.start()

    expect(putImageData).not.toHaveBeenCalled()
    expect(stream.stats.frameCount).toBe(0)
    stream.stop()
  })

  it('后端回退 png 帧 → 置降级记忆，后续直接走 api.screenshot', async () => {
    vi.useFakeTimers()
    const stream = new VideoStream('dev1', document.createElement('canvas'), fakeWs, 1500)

    await stream.start() // 默认 rawMock 返回 format:'png' → renderBlob

    expect(stream.stats.frameCount).toBe(1)
    expect(drawImage).toHaveBeenCalledTimes(1)

    await vi.advanceTimersByTimeAsync(1500) // 第二帧：降级记忆 → 旧 PNG 路径
    await flush()

    expect(screenshotRawMock).toHaveBeenCalledTimes(1)
    expect(screenshotMock).toHaveBeenCalledTimes(1)
    expect(stream.stats.frameCount).toBe(2)
    expect(drawImage).toHaveBeenCalledTimes(2)

    stream.stop()
  })
})