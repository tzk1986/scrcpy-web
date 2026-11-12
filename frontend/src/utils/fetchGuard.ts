/**
 * 请求代次守卫（方案 58 §八-3「8.3 请求代次/在飞守卫范式」统一工具）。
 *
 * 迟到响应覆盖一族（E-01/E-09/E-13/E-15/E-27）的共用原语：发起前
 * `begin()` 领取本轮令牌，响应/失败回调到达时 `isCurrent(token)` 仍为
 * 真才允许写入状态——新一轮已发起（切目录/切会话/切选中项/新轮询）则
 * 过期响应丢弃。禁各视图逐点手搓裸计数器。
 *
 * 身份比对型守卫（如「响应到达时包名/会话未变才写入」）语义不同，
 * 由调用点直接比对身份，不适用本工具。
 */
export function createEpochGuard() {
  let current = 0
  return {
    /** 发起一轮请求，返回本轮令牌。 */
    begin(): number {
      current += 1
      return current
    },
    /** 令牌是否仍是最新一轮（false=已被更新轮次取代，响应应丢弃）。 */
    isCurrent(token: number): boolean {
      return token === current
    },
    /** 当前轮次号（测试/调试用）。 */
    value(): number {
      return current
    },
  }
}
