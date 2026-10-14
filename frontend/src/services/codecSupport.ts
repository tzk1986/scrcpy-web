/**
 * 编解码支持面探测（方案 45 T4）
 * ================================
 *
 * 浏览器侧 HEVC（h265）硬件/软件解码能力探测，供设置页展示与支持面
 * 决策使用。纯探测函数，无副作用；三态语义（无 API / 探测异常 / 支持
 * 与否）统一收敛为 boolean，异常一律按「不支持」处理。
 */

/**
 * Chrome 侧 h265（HEVC）解码支持面探测（方案 45 T4）。
 *
 * 探测串取 Level 3.1（1080p30 常用档）的 Main profile：hvc1.1.6.L93.B0
 * （profile_space=0 省略字母、profile_idc=1、compat=6、L93、无约束尾）。
 * 无 VideoDecoder API（非 Chrome / 旧内核）或探测抛异常时返回 false。
 */
export async function isH265DecoderSupported(): Promise<boolean> {
  if (typeof VideoDecoder === 'undefined') return false
  try {
    const support = await VideoDecoder.isConfigSupported({
      codec: 'hvc1.1.6.L93.B0',
      codedWidth: 1920,
      codedHeight: 1080,
    })
    return !!support.supported
  } catch {
    return false
  }
}
