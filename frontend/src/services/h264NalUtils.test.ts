import { describe, expect, it } from 'vitest'
import {
  extractNalus,
  shouldDropFrame,
  scanFrame,
  frameToAvcc,
  MAX_DECODE_QUEUE,
} from './h264NalUtils'

/** 构造 4 字节起始码 + NAL 头 + 指定长度载荷的 NALU */
function nalu(type: number, len = 2): Uint8Array {
  return new Uint8Array([0, 0, 0, 1, type, ...new Array(len).fill(0xab)])
}

/** 参照实现：extractNalus + 4B 大端长度前缀组装（旧管线语义，作对拍 oracle） */
function refAvcc(data: Uint8Array): Uint8Array {
  const payloads = extractNalus(data).map(n => n.data)
  let total = 0
  for (const p of payloads) total += 4 + p.length
  const buf = new Uint8Array(total)
  const view = new DataView(buf.buffer)
  let off = 0
  for (const p of payloads) {
    view.setUint32(off, p.length)
    off += 4
    buf.set(p, off)
    off += p.length
  }
  return buf
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

describe('scanFrame', () => {
  it('载荷区间与 extractNalus 逐字节一致（SEI + 多 slice + 3/4 码混合）', () => {
    const sei = new Uint8Array([0, 0, 1, 0x06, 0xaa, 0xbb])          // 3 字节码 SEI
    const idr = new Uint8Array([0, 0, 0, 1, 0x65, 0x11, 0x22, 0x33]) // 4 字节码 IDR
    const slice = new Uint8Array([0, 0, 0, 1, 0x41, 0x44, 0x55])     // 4 字节码第二 slice
    const data = new Uint8Array([...sei, ...idr, ...slice])

    const frame = scanFrame(data)
    const ref = extractNalus(data).map(n => Array.from(n.data))

    expect(frame.nalus.map(n =>
      Array.from(data.subarray(n.offset, n.offset + n.length)),
    )).toEqual(ref)
  })

  it('isKey：含 IDR 为 true，仅 SPS/slice 为 false', () => {
    expect(scanFrame(new Uint8Array([0, 0, 0, 1, 0x65, 0x01])).isKey).toBe(true)
    expect(scanFrame(new Uint8Array([0, 0, 1, 0x41, 0x01])).isKey).toBe(false)
    expect(scanFrame(new Uint8Array([0, 0, 0, 1, 0x67, 0x01])).isKey).toBe(false)
  })

  it('空输入与无起始码：无 NALU、isKey=false、AVCC 为空', () => {
    const empty = scanFrame(new Uint8Array(0))
    expect(empty.nalus).toHaveLength(0)
    expect(empty.isKey).toBe(false)
    expect(frameToAvcc(new Uint8Array(0), empty).byteLength).toBe(0)

    const garbage = new Uint8Array([0x67, 0x42, 0x00, 0x1e])
    expect(scanFrame(garbage).nalus).toHaveLength(0)
    expect(frameToAvcc(garbage, scanFrame(garbage)).byteLength).toBe(0)
  })

  it('尾部残缺起始码（00 00）归入尾 NALU', () => {
    const a = nalu(7)
    const data = new Uint8Array([...a, 0, 0])
    const frame = scanFrame(data)
    expect(frame.nalus).toEqual([{ offset: 4, length: data.length - 4 }])
  })

  it('两起始码相邻（空 NALU）按边界切分，空载荷不参与 isKey 判定', () => {
    const data = new Uint8Array([0, 0, 0, 1, 0, 0, 0, 1, 0x65, 0x42])
    const frame = scanFrame(data)
    expect(frame.nalus).toHaveLength(2)
    expect(frame.nalus[0]).toEqual({ offset: 4, length: 0 })
    expect(frame.nalus[1]).toEqual({ offset: 8, length: 2 })
    expect(frame.isKey).toBe(true)
  })
})

describe('frameToAvcc', () => {
  it('与旧管线（extractNalus + 4B 长度前缀组装）逐字节一致', () => {
    const sei = new Uint8Array([0, 0, 1, 0x06, 0xaa, 0xbb])   // 3 字节码 SEI
    const idr = nalu(5, 3)                                    // 4 字节码 IDR
    const slice = new Uint8Array([0, 0, 0, 1, 0x41, 0x33, 0x44]) // 多 slice
    for (const data of [
      new Uint8Array([...sei, ...idr]),
      new Uint8Array([...idr, ...slice]),
      new Uint8Array([...sei, ...idr, ...slice]),
    ]) {
      expect(new Uint8Array(frameToAvcc(data, scanFrame(data)))).toEqual(refAvcc(data))
    }
  })

  it('每个 NALU 加 4 字节 big-endian 长度前缀', () => {
    const b = new Uint8Array([0, 0, 1, 0x68, 0xce])
    const data = new Uint8Array([...nalu(7, 3), ...b])
    const avcc = new Uint8Array(frameToAvcc(data, scanFrame(data)))
    const view = new DataView(avcc.buffer)
    expect(view.getUint32(0)).toBe(4)      // 0x67 头 + 3 字节载荷
    expect(avcc[4] & 0x1f).toBe(7)
    expect(view.getUint32(8)).toBe(2)      // 0x68 头 + 0xce
    expect(avcc[12] & 0x1f).toBe(8)
    expect(avcc.byteLength).toBe(14)
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