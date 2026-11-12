/**
 * webm 短片段导出工具单测（方案 37 B6）
 * =======================================
 *
 * 环境说明：vitest 环境为 happy-dom，无原生 MediaRecorder——用最小
 * FakeMediaRecorder stubGlobal（start/stop 立即同步触发 dataavailable+
 * onstop 并收集 Blob）。video 用纯对象伪造（happy-dom 的 duration/
 * currentTime 为只读 getter，纯对象便于可控赋值）。
 *
 * 覆盖：
 *   - 3s 片段：产生非空 webm Blob（vp9 优先），captureStream 收到 fps=10，
 *     播放前归零进度、结束后还原原进度
 *   - 长视频钳制到 maxSeconds（10s）才收尾
 *   - vp9 不可用时回退 video/webm
 *   - 无可用 MIME / 无 captureStream / captureStream 返回 null →
 *     拒绝「浏览器不支持」
 *   - 时长 NaN/非正 → 拒绝「视频时长无效」
 *   - isWebmRecorderSupported：vp9/webm 任一可用即 true，全不可用 false
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { exportWebmClip, isWebmRecorderSupported } from './webmExporter'

/** FakeMediaRecorder：stop 时同步触发 dataavailable + onstop（happy-dom 无原生实现）。 */
class FakeMediaRecorder {
  static isTypeSupported = vi.fn()
  static instances: FakeMediaRecorder[] = []

  ondataavailable: ((event: BlobEvent) => void) | null = null
  onstop: (() => void) | null = null
  onerror: (() => void) | null = null

  mimeType: string
  state: string = 'inactive'

  constructor(_stream: MediaStream, options?: MediaRecorderOptions) {
    this.mimeType = options?.mimeType ?? ''
    FakeMediaRecorder.instances.push(this)
  }

  start(): void {
    this.state = 'recording'
  }

  stop(): void {
    if (this.state === 'inactive') return
    this.state = 'inactive'
    this.ondataavailable?.({ data: new Blob(['chunk-a'], { type: this.mimeType }) } as BlobEvent)
    this.ondataavailable?.({ data: new Blob(['chunk-b'], { type: this.mimeType }) } as BlobEvent)
    this.onstop?.()
  }
}

/** 可断言 captureStream/play/pause 调用的伪 video（lib.dom 未声明 captureStream）。 */
type FakeVideo = HTMLVideoElement & {
  captureStream: ReturnType<typeof vi.fn>
  play: ReturnType<typeof vi.fn>
  pause: ReturnType<typeof vi.fn>
}

interface FakeVideoOverrides {
  duration?: number
  currentTime?: number
  captureStream?: unknown
  play?: () => Promise<void>
  pause?: () => void
}

function makeFakeVideo(overrides: FakeVideoOverrides = {}): FakeVideo {
  const base = {
    duration: 10,
    currentTime: 5,
    captureStream: vi.fn(() => ({ getTracks: () => [] }) as unknown as MediaStream),
    play: vi.fn(() => Promise.resolve()),
    pause: vi.fn(),
  }
  return { ...base, ...overrides } as unknown as FakeVideo
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.stubGlobal('MediaRecorder', FakeMediaRecorder)
  FakeMediaRecorder.instances.length = 0
  FakeMediaRecorder.isTypeSupported.mockReset()
  FakeMediaRecorder.isTypeSupported.mockReturnValue(true) // 默认全部可用 → vp9 优先
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('exportWebmClip', () => {
  it('3s 片段：vp9 优先产生非空 Blob，captureStream 收到 fps 10，进度归零后还原', async () => {
    let timeAtPlay = -1
    const video = makeFakeVideo({
      duration: 3,
      play: vi.fn(() => {
        timeAtPlay = video.currentTime
        return Promise.resolve()
      }),
    })

    const blobPromise = exportWebmClip(video)
    await Promise.resolve() // 让 play() 续体执行并注册收尾定时器
    await vi.advanceTimersByTimeAsync(3_000)
    const blob = await blobPromise

    expect(blob.size).toBeGreaterThan(0)
    expect(blob.type).toBe('video/webm;codecs=vp9')
    expect(FakeMediaRecorder.instances).toHaveLength(1)
    expect(FakeMediaRecorder.instances[0].mimeType).toBe('video/webm;codecs=vp9')
    expect(video.captureStream).toHaveBeenCalledWith(10)
    expect(timeAtPlay).toBe(0) // 录制前从头播放
    expect(video.pause).toHaveBeenCalledTimes(1)
    expect(video.currentTime).toBe(5) // 结束后还原原进度
  })

  it('长视频钳制：录到 maxSeconds（默认 10s）即收尾，不等到视频时长', async () => {
    const video = makeFakeVideo({ duration: 30 })
    const p = exportWebmClip(video)

    let resolved = false
    p.then(
      () => {
        resolved = true
      },
      () => {},
    )

    await Promise.resolve()
    await vi.advanceTimersByTimeAsync(9_999)
    expect(resolved).toBe(false) // 尚未到 10s 钳制点

    await vi.advanceTimersByTimeAsync(1)
    expect(resolved).toBe(true)

    const blob = await p
    expect(blob.size).toBeGreaterThan(0)
  })

  it('vp9 不可用时回退 video/webm', async () => {
    FakeMediaRecorder.isTypeSupported.mockImplementation((t: string) => t === 'video/webm')
    const video = makeFakeVideo({ duration: 2 })

    const p = exportWebmClip(video)
    await Promise.resolve()
    await vi.advanceTimersByTimeAsync(2_000)
    const blob = await p

    expect(blob.type).toBe('video/webm')
    expect(FakeMediaRecorder.instances[0].mimeType).toBe('video/webm')
  })

  it('无可用 MIME → 拒绝「浏览器不支持」且 isWebmRecorderSupported() 为 false', async () => {
    FakeMediaRecorder.isTypeSupported.mockReturnValue(false)
    const video = makeFakeVideo()

    await expect(exportWebmClip(video)).rejects.toThrow('浏览器不支持')
    expect(isWebmRecorderSupported()).toBe(false)
  })

  it('video 无 captureStream → 拒绝「浏览器不支持」', async () => {
    const video = makeFakeVideo({ captureStream: undefined })

    await expect(exportWebmClip(video)).rejects.toThrow('浏览器不支持')
  })

  it('captureStream 返回 null → 拒绝「浏览器不支持」', async () => {
    const video = makeFakeVideo({ captureStream: vi.fn(() => null) })

    await expect(exportWebmClip(video)).rejects.toThrow('浏览器不支持')
  })

  it('时长 NaN/非正 → 拒绝「视频时长无效」', async () => {
    await expect(exportWebmClip(makeFakeVideo({ duration: Number.NaN }))).rejects.toThrow(
      '视频时长无效',
    )
    await expect(exportWebmClip(makeFakeVideo({ duration: 0 }))).rejects.toThrow('视频时长无效')
  })

  // 方案 58 D-15：captureStream tracks 停止 + onstop 落定后才 seek 复位
  it('D-15：收尾停 tracks，且进度条复位晚于 onstop（竞态钉）', async () => {
    const events: string[] = []

    // 模拟真实实现的延迟收尾：onstop 在 stop() 之后的任务里才触发，
    // 若实现先 seek 复位，录制器未读完帧时会把复位段尾帧混进产物
    class LateStopRecorder {
      static isTypeSupported = vi.fn(() => true)
      ondataavailable: ((event: BlobEvent) => void) | null = null
      onstop: (() => void) | null = null
      onerror: (() => void) | null = null
      mimeType: string
      state = 'inactive'
      constructor(_stream: MediaStream, options?: MediaRecorderOptions) {
        this.mimeType = options?.mimeType ?? ''
      }
      start(): void {
        this.state = 'recording'
      }
      stop(): void {
        if (this.state === 'inactive') return
        this.state = 'inactive'
        events.push('recorder-stop')
        setTimeout(() => {
          this.ondataavailable?.({
            data: new Blob(['c'], { type: this.mimeType }),
          } as BlobEvent)
          events.push('onstop')
          this.onstop?.()
        }, 0)
      }
    }
    vi.stubGlobal('MediaRecorder', LateStopRecorder)

    const stream = {
      getTracks: () => [{ stop: () => events.push('track-stop') }],
    } as unknown as MediaStream
    const video = makeFakeVideo({ duration: 2, captureStream: vi.fn(() => stream) })
    let ct = 5
    Object.defineProperty(video, 'currentTime', {
      get: () => ct,
      set: (v: number) => {
        events.push('seek')
        ct = v
      },
      configurable: true,
    })

    const p = exportWebmClip(video)
    await Promise.resolve()
    await vi.advanceTimersByTimeAsync(2_000)
    // 推进 recorder.stop() 内调度的延迟 onstop（setTimeout 0）
    await vi.advanceTimersByTimeAsync(10)
    await p

    expect(events[0]).toBe('seek') // 录制前从头播放
    expect(events.slice(1)).toEqual([
      'recorder-stop',
      'track-stop',
      'onstop',
      'seek', // 复位发生在 onstop 落定之后
    ])
    expect(ct).toBe(5)
  })

  it('D-15：录制失败路径也停 tracks 并还原进度', async () => {
    const trackStop = vi.fn()
    const stream = { getTracks: () => [{ stop: trackStop }] } as unknown as MediaStream
    const video = makeFakeVideo({
      captureStream: vi.fn(() => stream),
      play: vi.fn(() => Promise.reject(new Error('play blocked'))) as never,
    })

    await expect(exportWebmClip(video)).rejects.toThrow('play blocked')
    expect(trackStop).toHaveBeenCalled()
    expect(video.currentTime).toBe(5)
    expect(video.pause).toHaveBeenCalledTimes(1)
  })
})

describe('isWebmRecorderSupported', () => {
  it('vp9 或 webm 任一可用即 true，全部不可用 false', () => {
    FakeMediaRecorder.isTypeSupported.mockReturnValue(true)
    expect(isWebmRecorderSupported()).toBe(true)

    FakeMediaRecorder.isTypeSupported.mockImplementation((t: string) => t === 'video/webm')
    expect(isWebmRecorderSupported()).toBe(true)

    FakeMediaRecorder.isTypeSupported.mockReturnValue(false)
    expect(isWebmRecorderSupported()).toBe(false)
  })

  it('MediaRecorder 未定义 → false', () => {
    vi.unstubAllGlobals() // happy-dom 无原生 MediaRecorder
    expect(isWebmRecorderSupported()).toBe(false)
  })
})