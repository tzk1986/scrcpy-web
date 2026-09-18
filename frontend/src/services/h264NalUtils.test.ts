import { describe, expect, it } from 'vitest'
import {
  extractNalus,
  hasKeyFrame,
  nalusToAvcc,
  shouldDropFrame,
  MAX_DECODE_QUEUE,
} from './h264NalUtils'

/** 构造 4 字节起始码 + NAL 头 + 指定长度载荷的 NALU */
function nalu(type: number, len = 2): Uint8Array {
  return new Uint8Array([0, 0, 0, 1, type, ...new Array(len).fill(0xab)])
}

describe('extractNalus', () => {
  it('单个 NALU：raw 含起始码、data 不含', () => {
    const data = nalu(0x67)
    const nalus = extractNalus(data)
    expect(nalus).toHaveLength(1)
    expect(nalus[0].raw).toEqual(data)
    expect(Array.from(nalus[0].data)).toEqual([0x67, 0xab, 0xab])
  })

  it('多个 NALU 边界正确', () => {
    const a = nalu(7)
    const b = nalu(8)
    const nalus = extractNalus(new Uint8Array([...a, ...b]))
    expect(nalus).toHaveLength(2)
    expect(nalus[0].data[0] & 0x1f).toBe(7)
    expect(nalus[1].data[0] & 0x1f).toBe(8)
  })

  it('3 字节与 4 字节起始码混用', () => {
    const a = new Uint8Array([0, 0, 0, 1, 0x67, 0x42])  // 4 字节码 SPS
    const b = new Uint8Array([0, 0, 1, 0x68, 0xce])     // 3 字节码 PPS
    const c = nalu(5)                                    // 4 字节码 IDR
    const nalus = extractNalus(new Uint8Array([...a, ...b, ...c]))
    expect(nalus).toHaveLength(3)
    expect(nalus[0].raw).toEqual(a)
    expect(nalus[1].raw).toEqual(b)
    expect(nalus[2].data[0] & 0x1f).toBe(5)
  })

  it('3 字节码后紧跟 0x01 头部不被 4 字节码检查吞字节', () => {
    // 00 00 01 后紧跟 0x01（type=1 的合法 NAL 头）
    const b = new Uint8Array([0, 0, 1, 0x01, 0x9a])
    const nalus = extractNalus(new Uint8Array([0, 0, 0, 1, 0x67, 0x42, ...b]))
    expect(nalus).toHaveLength(2)
    expect(nalus[1].raw).toEqual(b)
    expect(nalus[1].data[0] & 0x1f).toBe(1)
  })

  it('无起始码返回空', () => {
    expect(extractNalus(new Uint8Array([0x67, 0x42, 0x00, 0x1e]))).toHaveLength(0)
  })

  it('空输入返回空', () => {
    expect(extractNalus(new Uint8Array(0))).toHaveLength(0)
  })

  it('仅起始码无载荷：raw 为起始码、data 为空', () => {
    const nalus = extractNalus(new Uint8Array([0, 0, 0, 1]))
    expect(nalus).toHaveLength(1)
    expect(nalus[0].raw).toEqual(new Uint8Array([0, 0, 0, 1]))
    expect(nalus[0].data).toHaveLength(0)
  })

  it('尾部残缺起始码（00 00）归入尾 NALU', () => {
    const a = nalu(7)
    const data = new Uint8Array([...a, 0, 0])
    const nalus = extractNalus(data)
    expect(nalus).toHaveLength(1)
    expect(nalus[0].raw).toEqual(data)
  })

  it('两起始码相邻（空 NALU）按边界切分', () => {
    const nalus = extractNalus(new Uint8Array([0, 0, 0, 1, 0, 0, 0, 1, 0x67, 0x42]))
    expect(nalus).toHaveLength(2)
    expect(nalus[0].raw).toEqual(new Uint8Array([0, 0, 0, 1]))
    expect(nalus[1].data[0] & 0x1f).toBe(7)
  })

  it('大帧线性扫描（50 万字节，O(n²) 退化会超时失败）', () => {
    const body = new Uint8Array(500_000).fill(0xab)
    const data = new Uint8Array([0, 0, 0, 1, 0x67, ...body])
    const start = Date.now()
    const nalus = extractNalus(data)
    const elapsed = Date.now() - start
    expect(nalus).toHaveLength(1)
    expect(nalus[0].data.length).toBe(body.length + 1)
    // 线性实现 <30ms；阈值 1s 仅防 O(n²) 回归
    expect(elapsed).toBeLessThan(1000)
  })
})

describe('hasKeyFrame', () => {
  it('含 IDR 返回 true', () => {
    const nalus = extractNalus(new Uint8Array([...nalu(7), ...nalu(5)]))
    expect(hasKeyFrame(nalus)).toBe(true)
  })

  it('仅 SPS/PPS/slice 返回 false', () => {
    const nalus = extractNalus(new Uint8Array([...nalu(7), ...nalu(8), ...nalu(1)]))
    expect(hasKeyFrame(nalus)).toBe(false)
  })

  it('空列表返回 false', () => {
    expect(hasKeyFrame([])).toBe(false)
  })
})

describe('nalusToAvcc', () => {
  it('每个 NALU 加 4 字节 big-endian 长度前缀', () => {
    const b = new Uint8Array([0, 0, 1, 0x68, 0xce])
    const nalus = extractNalus(new Uint8Array([...nalu(7, 3), ...b]))
    const avcc = new Uint8Array(nalusToAvcc(nalus))
    const view = new DataView(avcc.buffer)
    expect(view.getUint32(0)).toBe(4)      // 0x67 头 + 3 字节载荷
    expect(avcc[4] & 0x1f).toBe(7)
    expect(view.getUint32(8)).toBe(2)      // 0x68 头 + 0xce
    expect(avcc[12] & 0x1f).toBe(8)
    expect(avcc.byteLength).toBe(14)
  })

  it('空列表返回空 buffer', () => {
    expect(nalusToAvcc([]).byteLength).toBe(0)
  })
})

describe('shouldDropFrame', () => {
  it('队列未达阈值不丢', () => {
    expect(shouldDropFrame(MAX_DECODE_QUEUE - 1, false)).toBe(false)
  })

  it('delta 帧队列达阈值丢', () => {
    expect(shouldDropFrame(MAX_DECODE_QUEUE, false)).toBe(true)
    expect(shouldDropFrame(MAX_DECODE_QUEUE + 3, false)).toBe(true)
  })

  it('关键帧永不丢（保 key：丢 key 花屏至下一 IDR）', () => {
    expect(shouldDropFrame(MAX_DECODE_QUEUE, true)).toBe(false)
    expect(shouldDropFrame(99, true)).toBe(false)
  })

  it('阈值常量为 2', () => {
    expect(MAX_DECODE_QUEUE).toBe(2)
  })
})