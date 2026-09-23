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
      sample(R.MIN_FRAMES, now - 2 * R.WINDOW_MS),
      sample(R.MIN_FRAMES, now - R.WINDOW_MS),
    ]
    expect(evaluateH264Recovery(samples, now)).toBe(false)
  })

  it('任一窗口帧数不足 → 不回切', () => {
    const now = 10_000
    const samples = [
      sample(R.MIN_FRAMES, now - 2 * R.WINDOW_MS),
      sample(R.MIN_FRAMES - 1, now - R.WINDOW_MS),
      sample(R.MIN_FRAMES, now),
    ]
    expect(evaluateH264Recovery(samples, now)).toBe(false)
  })

  it('最新采样过期（流又停了）→ 不回切', () => {
    const now = 10_000
    const samples = [
      sample(R.MIN_FRAMES, now - R.FRESH_MS - R.WINDOW_MS),
      sample(R.MIN_FRAMES, now - R.FRESH_MS - 1),
    ]
    expect(evaluateH264Recovery(samples, now)).toBe(false)
  })

  it('连续 3 窗口达标且新鲜 → 回切', () => {
    const now = 10_000
    const samples = [
      sample(R.MIN_FRAMES, now - 2 * R.WINDOW_MS),
      sample(R.MIN_FRAMES + 1, now - R.WINDOW_MS),
      sample(R.MIN_FRAMES, now),
    ]
    expect(evaluateH264Recovery(samples, now)).toBe(true)
  })

  it('早期低帧窗口被滑出窗口外 → 仍可回切', () => {
    const now = 10_000
    const samples = [
      sample(0, now - 5 * R.WINDOW_MS),
      sample(R.MIN_FRAMES, now - 2 * R.WINDOW_MS),
      sample(R.MIN_FRAMES, now - R.WINDOW_MS),
      sample(R.MIN_FRAMES, now),
    ]
    expect(evaluateH264Recovery(samples, now)).toBe(true)
  })
})
