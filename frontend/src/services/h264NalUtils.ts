/**
 * H.264 Annex B NAL 工具（纯函数）
 * ================================
 *
 * 视频帧处理路径的 NAL 解析/转换/丢帧判定，独立于 H264VideoStream，
 * 便于单测（vitest）与复用。全部为纯函数，无副作用。
 */

export interface H264Nalu {
  /** 包含起始码的完整 NALU */
  raw: Uint8Array
  /** 不含起始码、含 NAL 头的 NALU 载荷 */
  data: Uint8Array
}

/** 解码队列积压判定阈值：队列达到该值且为 delta 帧时丢弃 */
export const MAX_DECODE_QUEUE = 2

/**
 * 从 Annex B 字节流中提取所有 NAL 单元（单指针前向扫描，O(n)）。
 *
 * 起始码识别顺序与语义：3 字节码（00 00 01）优先于 4 字节码
 * （00 00 00 01）——位置 i 处先判 3 字节码，避免「00 00 01 后紧跟
 * 0x01 头部」的合法 3 字节码被 4 字节码检查吞掉一个头字节。
 *
 * 相邻两起始码之间为空 NALU 时按边界切分（scrcpy 码流中不会出现，
 * 但保持语义自洽）；流尾无下一起始码的 NALU 原样输出（含可能的
 * 残缺起始码字节）。
 */
export function extractNalus(data: Uint8Array): H264Nalu[] {
  const nalus: H264Nalu[] = []
  const len = data.length
  let codeStart = -1  // 当前 NALU 起始码位置（-1 = 尚未找到）
  let dataStart = -1  // 当前 NALU 载荷起始位置

  let i = 0
  while (i < len) {
    const is3 = i + 2 < len && data[i] === 0 && data[i + 1] === 0 && data[i + 2] === 1
    const is4 = !is3 && i + 3 < len &&
      data[i] === 0 && data[i + 1] === 0 && data[i + 2] === 0 && data[i + 3] === 1
    if (is3 || is4) {
      if (codeStart >= 0) {
        // 新起始码闭合前一个 NALU
        nalus.push({ raw: data.slice(codeStart, i), data: data.slice(dataStart, i) })
      }
      codeStart = i
      dataStart = i + (is4 ? 4 : 3)
      i = dataStart
    } else {
      i++
    }
  }

  // 尾部 NALU（无下一边界）
  if (codeStart >= 0) {
    nalus.push({ raw: data.slice(codeStart), data: data.slice(dataStart) })
  }
  return nalus
}

/** 判断 NALU 列表是否含 IDR 关键帧（NAL 类型 5） */
export function hasKeyFrame(nalus: H264Nalu[]): boolean {
  for (const nalu of nalus) {
    if (nalu.data.length > 0 && (nalu.data[0] & 0x1f) === 5) return true
  }
  return false
}

/**
 * 将 NALU 列表转换为 AVCC 格式：
 * Annex B: [start_code] NALU [start_code] NALU ...
 * AVCC:    [4-byte big-endian length] NALU [4-byte length] NALU ...
 */
export function nalusToAvcc(nalus: H264Nalu[]): ArrayBuffer {
  if (nalus.length === 0) return new ArrayBuffer(0)

  let totalSize = 0
  for (const nalu of nalus) {
    totalSize += 4 + nalu.data.length
  }

  const buffer = new ArrayBuffer(totalSize)
  const view = new DataView(buffer)
  const bytes = new Uint8Array(buffer)
  let offset = 0

  for (const nalu of nalus) {
    view.setUint32(offset, nalu.data.length)
    offset += 4
    bytes.set(nalu.data, offset)
    offset += nalu.data.length
  }

  return buffer
}

/**
 * 解码队列积压丢帧判定。
 *
 * Web 端丢弃的是解码输入帧：丢 key 帧会导致后续 delta 全部花屏直至
 * 下一个 IDR（设备端 I 帧间隔 10s），因此只丢 delta 帧、key 帧永不丢。
 * 与 scrcpy 官方桌面端「丢已解码帧」无需保 key 的策略差异的原因
 * 见 方案/17-推流流畅度优化.md 实施项 2。
 */
export function shouldDropFrame(queueSize: number, isKey: boolean): boolean {
  return !isKey && queueSize >= MAX_DECODE_QUEUE
}