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

const NO_FALLBACK: FallbackResult = { fallback: false, reason: null }

export function evaluateH264Fallback(input: FallbackInput): FallbackResult {
  const { state, lastFrameTime, startedAt, now } = input

  if (state === 'error') return { fallback: true, reason: 'error' }
  if (state === 'stopped' || state === 'idle') return NO_FALLBACK

  // 收到过帧后码流冻结：不论 configuring 还是 streaming 都应回退
  if (lastFrameTime > 0 && now - lastFrameTime >= FALLBACK_THRESHOLDS.STALL_MS) {
    return { fallback: true, reason: 'stalled' }
  }

  // 硬兜底
  if (now - startedAt >= FALLBACK_THRESHOLDS.HARD_TIMEOUT_MS) {
    return { fallback: true, reason: 'timeout' }
  }

  // 始终未出画面
  if (state !== 'streaming' && now - startedAt >= FALLBACK_THRESHOLDS.NO_STREAM_MS) {
    return { fallback: true, reason: 'no-stream' }
  }

  return NO_FALLBACK
}
