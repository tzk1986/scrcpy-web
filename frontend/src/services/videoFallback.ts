/**
 * H.264 视频流回退判据（纯函数）
 * ==============================
 *
 * 某些设备（典型如 Rockchip RK3288）的硬件 OMX 编码器会在编码数秒后
 * 崩溃：TCP socket 保持打开、不报错，但码流停止。浏览器需凭统计信号
 * 提前感知并回退到截图模式。
 *
 * 四类信号（按优先级）：
 *   error    — 解码器/服务端已报错，立即回退
 *   stalled  — 已收到过帧但码流冻结超过 STALL_MS（覆盖 RK3288 出帧后崩溃）
 *   timeout  — 硬兜底，无论如何不超过 HARD_TIMEOUT_MS 仍未出画面
 *   no-stream— 始终未收到任何帧，连接建立超过 NO_STREAM_MS
 *
 * 与旧实现（单一 15s setTimeout 且进入 streaming 即取消）相比，
 * 新增 stalled 感知修复了「出过前几帧后编码器崩溃 → 画面永久冻结」的盲区。
 */

export type FallbackReason = 'error' | 'stalled' | 'no-stream' | 'timeout'

export interface FallbackInput {
  /** H264VideoStream.state */
  state: 'idle' | 'configuring' | 'streaming' | 'error' | 'stopped'
  /** 已提交的编码帧数 */
  frameCount: number
  /** 最近一次收到编码帧的时间戳（Date.now()），0 表示从未收到 */
  lastFrameTime: number
  /** H264 模式启动时间戳（Date.now()） */
  startedAt: number
  /** 当前时间戳（Date.now()），由调用方注入以便测试 */
  now: number
}

export interface FallbackResult {
  fallback: boolean
  reason: FallbackReason | null
}

export const FALLBACK_THRESHOLDS = {
  STALL_MS: 3000,          // 码流冻结判定（出过帧后停止）
  NO_STREAM_MS: 8000,      // 始终无帧判定
  HARD_TIMEOUT_MS: 15000,  // 硬性兜底
}

export interface FallbackThresholds {
  STALL_MS: number
  NO_STREAM_MS: number
  HARD_TIMEOUT_MS: number
}

/**
 * 与后端空闲保活联动的回退阈值（方案 19 实施项 2a）。
 * keepaliveMs 为后端 RESET_VIDEO 保活间隔（config.idle_reset_seconds×1000）；
 * 静止设备在保活下仍有周期性 IDR，STALL 取 2× 间隔，再叠加
 * 2s/5s 余量保证 NO_STREAM < HARD 单调。keepaliveMs=0（保活关闭）回默认。
 */
export function computeFallbackThresholds(keepaliveMs: number): FallbackThresholds {
  const STALL_MS = Math.max(3000, keepaliveMs * 2)
  const NO_STREAM_MS = Math.max(8000, STALL_MS + 2000)
  const HARD_TIMEOUT_MS = Math.max(15000, NO_STREAM_MS + 5000)
  if (keepaliveMs <= 0) {
    return { STALL_MS: 3000, NO_STREAM_MS: 8000, HARD_TIMEOUT_MS: 15000 }
  }
  return { STALL_MS, NO_STREAM_MS, HARD_TIMEOUT_MS }
}

const NO_FALLBACK: FallbackResult = { fallback: false, reason: null }

/**
 * 截图模式下的一次 H264 帧探测采样。
 * frames 为该窗口（WINDOW_MS）内经 WebSocket 到达的视频帧数，
 * at 为采样结束时间戳（Date.now()）。
 */
export interface RecoverySample {
  frames: number
  at: number
}

export const RECOVERY_THRESHOLDS = {
  WINDOWS: 3,
  MIN_TOTAL_FRAMES: 12,   // 近 WINDOWS 窗（6s）总帧数下限 = 2fps × 6s
  WINDOW_MS: 2000,
  FRESH_MS: 3000,
}

/**
 * 判定截图模式期间 H264 码流是否已恢复，可以回切。
 *
 * 设备端编码器（如 Rockchip）可能卡死后自行恢复：ws 仍未断、后端持续
 * 转发帧，但前端已回退截图模式。截图模式下 h264 实例转入 suspend（只
 * 计数不解码），每次采样记录窗口内帧数。
 *
 * 判据为「窗口聚合 + 末窗非零」而非逐窗严格（方案 22）：设备端帧到达
 * 呈突发-静默节律（实测 ~3s 一突发），2s 探针窗与突发周期成整数比时
 * 「逐窗 ≥4」会与出帧周期共振、结构性不可满足（相位不利时永不回切）。
 * 聚合为近 WINDOWS 窗总帧数 ≥ MIN_TOTAL_FRAMES（保留 2fps 均值语义，
 * 排除 0.x fps 的伪恢复）且末窗 ≥1 帧（拒绝「突发后静默」）且最新采样
 * 在 FRESH_MS 内，才允许回切——防止「恢复几秒又卡死」的来回横跳。
 */
export function evaluateH264Recovery(samples: RecoverySample[], now: number): boolean {
  if (samples.length < RECOVERY_THRESHOLDS.WINDOWS) return false

  const recent = samples.slice(-RECOVERY_THRESHOLDS.WINDOWS)
  const latest = recent[recent.length - 1]
  if (now - latest.at > RECOVERY_THRESHOLDS.FRESH_MS) return false
  if (latest.frames < 1) return false

  return recent.reduce((sum, s) => sum + s.frames, 0) >= RECOVERY_THRESHOLDS.MIN_TOTAL_FRAMES
}

export function evaluateH264Fallback(
  input: FallbackInput,
  thresholds: FallbackThresholds = FALLBACK_THRESHOLDS,
): FallbackResult {
  const { state, lastFrameTime, startedAt, now } = input

  if (state === 'error') return { fallback: true, reason: 'error' }
  if (state === 'stopped' || state === 'idle') return NO_FALLBACK

  // 收到过帧后码流冻结：不论 configuring 还是 streaming 都应回退
  if (lastFrameTime > 0 && now - lastFrameTime >= thresholds.STALL_MS) {
    return { fallback: true, reason: 'stalled' }
  }

  // 硬兜底：仍未出画面（configuring 挂起 / streaming 异常从未出帧）。
  // 出过帧后的冻结与卡死已由 stalled 分支全面覆盖（STALL_MS <
  // HARD_TIMEOUT_MS 恒成立），健康流不受 HARD 时钟约束——此前无条件
  // 触发导致健康流 17s 后必回退的呼吸循环（方案 20）
  if (
    (state !== 'streaming' || lastFrameTime === 0) &&
    now - startedAt >= thresholds.HARD_TIMEOUT_MS
  ) {
    return { fallback: true, reason: 'timeout' }
  }

  // 始终未出画面
  if (state !== 'streaming' && now - startedAt >= thresholds.NO_STREAM_MS) {
    return { fallback: true, reason: 'no-stream' }
  }

  return NO_FALLBACK
}
