import { describe, expect, it } from 'vitest'

import { createEpochGuard } from './fetchGuard'

describe('createEpochGuard', () => {
  it('begin 递增令牌，仅最新轮 isCurrent', () => {
    const g = createEpochGuard()
    const a = g.begin()
    const b = g.begin()
    expect(g.isCurrent(a)).toBe(false)
    expect(g.isCurrent(b)).toBe(true)
    expect(g.value()).toBe(2)
  })

  it('未经 begin 发放的令牌一律判过期', () => {
    const g = createEpochGuard()
    expect(g.isCurrent(1)).toBe(false)
    expect(g.isCurrent(99)).toBe(false)
  })

  it('迟到响应场景：第二轮进行中第一轮返回被丢弃', () => {
    const g = createEpochGuard()
    const first = g.begin()
    const second = g.begin()
    // 模拟第一轮响应迟到
    expect(g.isCurrent(first)).toBe(false)
    expect(g.isCurrent(second)).toBe(true)
  })
})
