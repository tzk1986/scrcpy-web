/**
 * webm 短片段导出工具（方案 37 B6）
 * =================================
 *
 * 用原生 API 把录屏产物 <video> 的前 min(时长, 上限) 秒导出为 webm
 * 短片段（captureStream + MediaRecorder，零新依赖）。时长钳制与 fps
 * 抽帧控体积（spec §B2：≤10s、fps 10）。
 *
 * 依赖浏览器的 MediaRecorder 与 HTMLVideoElement.captureStream（Chrome
 * 专属，符合项目「仅支持 Chrome」约束）。不支持的浏览器不硬抛：组件侧
 * 用 isWebmRecorderSupported() 禁用按钮降级，导出本身拒绝并提示。
 *
 * MIME 选择：video/webm;codecs=vp9 优先，video/webm 兜底——
 * Windows Chrome 的 MediaRecorder 对 mp4 支持不可靠，故不用 video/mp4，
 * 导出文件名固定 clip.webm。
 */

/** vp9 编码 webm（优先）。 */
const MIME_VP9 = 'video/webm;codecs=vp9'
/** 通用 webm（兜底）。 */
const MIME_WEBM = 'video/webm'

/** 不支持的统一错误文案（组件侧据此提示）。 */
const UNSUPPORTED_MSG = '浏览器不支持 webm 录制导出'

/**
 * captureStream 类型补声明：TS lib.dom 仅含 HTMLCanvasElement.captureStream，
 * HTMLVideoElement 的同名方法（Media Capture from DOM Elements）未收录。
 */
type StreamCapturableVideo = HTMLVideoElement & {
  captureStream?: (frameRequestRate?: number) => MediaStream | null
}

/**
 * 选择可用的 webm 录制 MIME：vp9 优先、webm 兜底，全部不可用返回 null。
 */
function pickSupportedMime(): string | null {
  if (typeof MediaRecorder === 'undefined') return null
  try {
    if (typeof MediaRecorder.isTypeSupported !== 'function') return null
    if (MediaRecorder.isTypeSupported(MIME_VP9)) return MIME_VP9
    if (MediaRecorder.isTypeSupported(MIME_WEBM)) return MIME_WEBM
  } catch {
    return null // isTypeSupported 异常视为不支持（降级防御）
  }
  return null
}

/**
 * 当前浏览器是否支持 webm 录制导出（供组件启用/禁用按钮，不硬抛）。
 */
export function isWebmRecorderSupported(): boolean {
  return pickSupportedMime() !== null
}

/**
 * 导出 video 前 min(视频时长, maxSeconds) 秒为 webm Blob。
 *
 * 流程：captureStream(fps) → MediaRecorder 录音 → 从头 play() 后
 * start()，到时 stop() + pause()，currentTime 还原，onstop 时以
 * 收集到的 chunks 组装 Blob resolve。
 *
 * @param video 录屏产物 <video> 元素
 * @param maxSeconds 片段时长上限（默认 10s，控体积）
 * @param fps captureStream 抽帧率（默认 10，控体积）
 * @returns 解析为 webm Blob 的 Promise
 * @throws 浏览器不支持（无可用 MIME / 无 captureStream / 返回空流）时拒绝
 * @throws 视频时长 NaN 或非正时拒绝
 */
export async function exportWebmClip(
  video: HTMLVideoElement,
  maxSeconds = 10,
  fps = 10
): Promise<Blob> {
  const mimeType = pickSupportedMime()
  if (!mimeType) throw new Error(UNSUPPORTED_MSG)

  const captureStream = (video as StreamCapturableVideo).captureStream
  if (typeof captureStream !== 'function') throw new Error(UNSUPPORTED_MSG)
  const stream = captureStream.call(video, fps)
  if (!stream) throw new Error(UNSUPPORTED_MSG)

  const duration = video.duration
  if (!Number.isFinite(duration) || duration <= 0) {
    throw new Error('视频时长无效，无法导出片段')
  }
  const actualSeconds = Math.min(duration, maxSeconds)

  const recorder = new MediaRecorder(stream, { mimeType })
  const chunks: Blob[] = []
  recorder.ondataavailable = (event: BlobEvent) => {
    if (event.data && event.data.size > 0) chunks.push(event.data)
  }

  // onstop 时以收集到的 chunks 组装产物（含 codecs 的 MIME 作为 Blob type）
  const stopped = new Promise<Blob>((resolve, reject) => {
    recorder.onstop = () => resolve(new Blob(chunks, { type: mimeType }))
    recorder.onerror = () => reject(new Error('MediaRecorder 录制失败'))
  })

  const originTime = video.currentTime
  let stopTimer: ReturnType<typeof setTimeout> | null = null
  try {
    video.currentTime = 0
    await video.play()
    recorder.start()
    await new Promise<void>((resolve) => {
      // min(视频时长, maxSeconds) 秒后收尾（时长钳制控体积）
      stopTimer = setTimeout(resolve, actualSeconds * 1000)
    })
    recorder.stop()
    video.pause()
    video.currentTime = originTime
  } catch (err) {
    if (stopTimer !== null) clearTimeout(stopTimer)
    // 先给 stopped 挂观察者再 throw：中途出错时 recorder.stop()/onerror 可能
    // 已让 stopped 以 reject 落定，而下方 throw 使 return stopped 永远不
    // 执行，reject 无人处理成 unhandled rejection（方案 51 F1-10）
    stopped.catch(() => {})
    try {
      recorder.stop() // 录制中途出错也收尾（已停止时抛 InvalidState，忽略）
    } catch {
      // recorder 可能未启动或已自动停止
    }
    try {
      video.pause()
      video.currentTime = originTime
    } catch {
      // 还原失败不掩盖原错误
    }
    throw err
  }
  return stopped
}