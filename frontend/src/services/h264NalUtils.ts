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

/** 单帧 NALU 载荷区间（offset/length 为去起始码载荷的半开区间下标） */
export interface FrameNaluPos {
  offset: number
  length: number
}

/** 单帧零拷贝扫描结果：NALU 载荷区间列表 + 关键帧判定 */
export interface FrameScan {
  nalus: FrameNaluPos[]
  isKey: boolean
}

/**
 * 零拷贝单遍扫描单帧（一整 AU）：产出 NALU 载荷区间与关键帧判定，
 * 边界语义与 extractNalus 逐字节一致（3 字节码优先于 4 字节码，
 * 码位后跳到载荷起点）。帧包边界即帧边界（方案 17/26），不做跨包
 * 缓冲。isKey 判定与 hasKeyFrame 一致（空载荷跳过）。
 */
export function scanFrame(data: Uint8Array): FrameScan {
  const nalus: FrameNaluPos[] = []
  const len = data.length
  let codeStart = -1  // 当前 NALU 起始码位置（-1 = 尚未找到）
  let dataStart = -1  // 当前 NALU 载荷起始位置
  let isKey = false

  let i = 0
  while (i < len) {
    const is3 = i + 2 < len && data[i] === 0 && data[i + 1] === 0 && data[i + 2] === 1
    const is4 = !is3 && i + 3 < len &&
      data[i] === 0 && data[i + 1] === 0 && data[i + 2] === 0 && data[i + 3] === 1
    if (is3 || is4) {
      if (codeStart >= 0) {
        const length = i - dataStart
        nalus.push({ offset: dataStart, length })
        if (length > 0 && (data[dataStart] & 0x1f) === 5) isKey = true
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
    const length = len - dataStart
    nalus.push({ offset: dataStart, length })
    if (length > 0 && (data[dataStart] & 0x1f) === 5) isKey = true
  }
  return { nalus, isKey }
}

/**
 * 单次分配组装 AVCC：按 scanFrame 结果加 4 字节 big-endian 长度前缀
 * 并直拷载荷（subarray 零拷贝视图 + bytes.set 单次写入）。
 */
export function frameToAvcc(data: Uint8Array, frame: FrameScan): ArrayBuffer {
  let totalSize = 0
  for (const n of frame.nalus) {
    totalSize += 4 + n.length
  }

  const buffer = new ArrayBuffer(totalSize)
  const view = new DataView(buffer)
  const bytes = new Uint8Array(buffer)
  let offset = 0

  for (const n of frame.nalus) {
    view.setUint32(offset, n.length)
    offset += 4
    bytes.set(data.subarray(n.offset, n.offset + n.length), offset)
    offset += n.length
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