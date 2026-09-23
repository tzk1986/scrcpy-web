import { describe, expect, it } from 'vitest'
import {
  computeFallbackThresholds,
  evaluateH264Fallback,
  evaluateH264Recovery,
  FALLBACK_THRESHOLDS,
  RECOVERY_THRESHOLDS,
  type RecoverySample,
} from './videoFallback'

const T = FALLBACK_THRESHOLDS

function base() {
  return {
    state: 'configuring' as const,
    frameCount: 0,
    lastFrameTime: 0,
    startedAt: 0,
    now: 0,
  }
}

describe('evaluateH264Fallback', () => {
  it('error 状态立即回退（不看时间）', () => {
    const r = evaluateH264Fallback({ ...base(), state: 'error', now: 100 })
    expect(r.fallback).toBe(true)
    expect(r.reason).toBe('error')
  })

  it('configuring 未超时不回退', () => {
    const r = evaluateH264Fallback({ ...base(), now: T.NO_STREAM_MS - 1000 })
    expect(r.fallback).toBe(false)
    expect(r.reason).toBeNull()
  })

  it('configuring 无流超过阈值 → no-stream', () => {
    const r = evaluateH264Fallback({ ...base(), now: T.NO_STREAM_MS + 1 })
    expect(r.fallback).toBe(true)
    expect(r.reason).toBe('no-stream')
  })

  it('已收到帧但仍 configuring → 走 stall 而非 no-stream', () => {
    // 帧到达但从未成功解码出 output（RK3288 部分场景）
    const r = evaluateH264Fallback({
      ...base(), state: 'configuring',
      frameCount: 21, lastFrameTime: 5000, now: 5000 + T.STALL_MS + 1,
    })
    expect(r.fallback).toBe(true)
    expect(r.reason).toBe('stalled')
  })

  it('streaming 帧持续到达不回退', () => {
    const r = evaluateH264Fallback({
      ...base(), state: 'streaming',
      frameCount: 100, lastFrameTime: 10_000, now: 10_000 + 100,
    })
    expect(r.fallback).toBe(false)
    expect(r.reason).toBeNull()
  })

  it('streaming 帧停止超阈值 → stalled（修复现状盲区）', () => {
    const r = evaluateH264Fallback({
      ...base(), state: 'streaming',
      frameCount: 21, lastFrameTime: 10_000, now: 10_000 + T.STALL_MS + 500,
    })
    expect(r.fallback).toBe(true)
    expect(r.reason).toBe('stalled')
  })

  it('健康流：streaming 帧持续到达，即使超 HARD 也不回退（方案 20 呼吸循环修复）', () => {
    // 缺陷场景：h264StartedAt 起 17s 后健康流被无条件 timeout 回退，
    // 回切重置时钟形成 ~35s 呼吸循环（方案 19 真机验收 1a）
    const r = evaluateH264Fallback({
      ...base(), state: 'streaming',
      frameCount: 500, lastFrameTime: T.HARD_TIMEOUT_MS * 3,
      now: T.HARD_TIMEOUT_MS * 3 + 100,
    })
    expect(r.fallback).toBe(false)
    expect(r.reason).toBeNull()
  })

  it('streaming 帧冻结且已超 HARD → stalled 优先于 timeout', () => {
    const r = evaluateH264Fallback({
      ...base(), state: 'streaming',
      frameCount: 100, lastFrameTime: 5000, now: T.HARD_TIMEOUT_MS + 1000,
    })
    expect(r.fallback).toBe(true)
    expect(r.reason).toBe('stalled')
  })

  it('联动阈值下健康流同样不受 HARD 约束（keepalive=5000）', () => {
    const t = computeFallbackThresholds(5000)
    const r = evaluateH264Fallback({
      ...base(), state: 'streaming',
      frameCount: 300, lastFrameTime: t.HARD_TIMEOUT_MS * 2,
      now: t.HARD_TIMEOUT_MS * 2 + 100,
    }, t)
    expect(r.fallback).toBe(false)
    expect(r.reason).toBeNull()
  })

  it('streaming 但 lastFrameTime 从未更新（异常）→ 走超时兜底', () => {
    const r = evaluateH264Fallback({
      ...base(), state: 'streaming',
      frameCount: 0, lastFrameTime: 0, now: T.HARD_TIMEOUT_MS + 1,
    })
    expect(r.fallback).toBe(true)
    expect(r.reason).toBe('timeout')
  })

  it('硬性超时兜底优先于 no-stream（configuring 长时间挂起）', () => {
    const r = evaluateH264Fallback({ ...base(), now: T.HARD_TIMEOUT_MS + 1 })
    expect(r.fallback).toBe(true)
    // no-stream 阈值更小会先命中，两者都是合理回退
    expect(['no-stream', 'timeout']).toContain(r.reason)
  })

  it('stopped/idle 状态从不回退', () => {
    expect(evaluateH264Fallback({ ...base(), state: 'stopped', now: 99999 }).fallback).toBe(false)
    expect(evaluateH264Fallback({ ...base(), state: 'idle', now: 99999 }).fallback).toBe(false)
  })

  it('阈值配置：STALL 最短、HARD 最长', () => {
    expect(T.STALL_MS).toBeLessThan(T.NO_STREAM_MS)
    expect(T.NO_STREAM_MS).toBeLessThan(T.HARD_TIMEOUT_MS)
  })
})

describe('computeFallbackThresholds', () => {
  it('keepalive=0 时返回默认阈值', () => {
    expect(computeFallbackThresholds(0)).toEqual({
      STALL_MS: 3000, NO_STREAM_MS: 8000, HARD_TIMEOUT_MS: 15000,
    })
  })
  it('keepalive=5000 → 10000/12000/17000', () => {
    expect(computeFallbackThresholds(5000)).toEqual({
      STALL_MS: 10000, NO_STREAM_MS: 12000, HARD_TIMEOUT_MS: 17000,
    })
  })
  it('小 keepalive 不低于默认下限', () => {
    expect(computeFallbackThresholds(1000)).toEqual(FALLBACK_THRESHOLDS)
  })
})

describe('evaluateH264Fallback 自定义阈值', () => {
  it('静止设备在抬升的 stall 阈值下不误判', () => {
    const base = {
      state: 'streaming' as const, frameCount: 10, lastFrameTime: 0,
      startedAt: 0, now: 5000,
    }
    expect(evaluateH264Fallback({ ...base, lastFrameTime: 1000 }).fallback).toBe(true)
    const t = computeFallbackThresholds(5000)
    expect(evaluateH264Fallback({ ...base, lastFrameTime: 1000 }, t).fallback).toBe(false)
  })
})

describe('evaluateH264Recovery', () => {
  const R = RECOVERY_THRESHOLDS

  function sample(frames: number, at: number): RecoverySample {
    return { frames, at }
  }

  it('采样不足 3 个 → 不回切', () => {
    const now = 10_000
    const samples = [
      sample(R.MIN_TOTAL_FRAMES, now - 2 * R.WINDOW_MS),
      sample(R.MIN_TOTAL_FRAMES, now - R.WINDOW_MS),
    ]
    expect(evaluateH264Recovery(samples, now)).toBe(false)
  })

  it('近 3 窗总帧数低于聚合下限 → 不回切', () => {
    // 逐窗语义下该序列因中窗 3<4 被拒；聚合语义下总帧 11<12 同样被拒
    const now = 10_000
    const samples = [
      sample(4, now - 2 * R.WINDOW_MS),
      sample(3, now - R.WINDOW_MS),
      sample(4, now),
    ]
    expect(evaluateH264Recovery(samples, now)).toBe(false)
  })

  it('run4 掉窗形态 [11,10,1]：连续达标窗不足但总帧达标且末窗有帧 → 回切', () => {
    // 帧到达呈 ~3s 突发节律时 2s 窗与周期共振，逐窗判据永远不满足；
    // 聚合判据将「每 3 窗掉 1 窗」的 [11,10,1] 判为恢复（方案 22）
    const now = 10_000
    const samples = [
      sample(11, now - 2 * R.WINDOW_MS),
      sample(10, now - R.WINDOW_MS),
      sample(1, now),
    ]
    expect(evaluateH264Recovery(samples, now)).toBe(true)
  })

  it('run4 完整形态 [11,10,1,11]：第 4 窗后同样回切', () => {
    const now = 10_000
    const samples = [
      sample(11, now - 3 * R.WINDOW_MS),
      sample(10, now - 2 * R.WINDOW_MS),
      sample(1, now - R.WINDOW_MS),
      sample(11, now),
    ]
    expect(evaluateH264Recovery(samples, now)).toBe(true)
  })

  it('伪恢复（0.3fps 稀疏单帧）→ 不回切', () => {
    const now = 10_000
    expect(evaluateH264Recovery(
      [sample(0, now - 2 * R.WINDOW_MS), sample(0, now - R.WINDOW_MS), sample(1, now)],
      now,
    )).toBe(false)
    expect(evaluateH264Recovery(
      [sample(1, now - 2 * R.WINDOW_MS), sample(0, now - R.WINDOW_MS), sample(1, now)],
      now,
    )).toBe(false)
  })

  it('突发后静默 [12,0,0] → 不回切（末窗必须有帧）', () => {
    const now = 10_000
    const samples = [
      sample(12, now - 2 * R.WINDOW_MS),
      sample(0, now - R.WINDOW_MS),
      sample(0, now),
    ]
    expect(evaluateH264Recovery(samples, now)).toBe(false)
  })

  it('最新采样过期（流又停了）→ 不回切', () => {
    const now = 10_000
    const samples = [
      sample(6, now - R.FRESH_MS - R.WINDOW_MS),
      sample(6, now - R.FRESH_MS - 1),
    ]
    expect(evaluateH264Recovery(samples, now)).toBe(false)
  })

  it('近 3 窗总帧达标、末窗有帧且新鲜 → 回切', () => {
    const now = 10_000
    const samples = [
      sample(4, now - 2 * R.WINDOW_MS),
      sample(5, now - R.WINDOW_MS),
      sample(4, now),
    ]
    expect(evaluateH264Recovery(samples, now)).toBe(true)
  })

  it('早期低帧窗口被滑出窗口外 → 仍可回切', () => {
    const now = 10_000
    const samples = [
      sample(0, now - 5 * R.WINDOW_MS),
      sample(4, now - 2 * R.WINDOW_MS),
      sample(4, now - R.WINDOW_MS),
      sample(4, now),
    ]
    expect(evaluateH264Recovery(samples, now)).toBe(true)
  })
})
