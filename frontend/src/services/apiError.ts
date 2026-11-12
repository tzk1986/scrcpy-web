/**
 * HTTP 错误体统一提取助手（方案 58 E-17：errOf 范式共享化）
 * =====================================================
 *
 * 后端 core/exceptions.py 的四个全局处理器统一输出 `{error:{code,message}}`
 * 形状（openscrcpy / http / validation / generic）；个别 FastAPI 内部构造或
 * 旧链路会给出 `{detail}`。两个助手分别覆盖两种消费形态：
 *   - errOf  — 需要 code 分流的原 files 端点调用方（FileManager 本地版上提）
 *   - errText — 只要一句话文案的 toast 场景（exportKit 版语义，扩展为
 *              error.message 优先、detail 兜底）
 */

/** axios 风格错误的响应体形状。 */
interface AxiosLike {
  response?: {
    data?: {
      error?: { code?: string; message?: string }
      detail?: string
    }
  }
}

/** 错误体 → {code,message}；无 message 时退 e.message / String(e)，code 可为空串。 */
export function errOf(e: unknown): { code: string; message: string } {
  const resp = (e as AxiosLike)?.response
  const code = resp?.data?.error?.code ?? ''
  const message =
    resp?.data?.error?.message ?? (e instanceof Error ? e.message : String(e))
  return { code, message }
}

/** 错误体 → 单句文案：`{error.message}` 优先，兼容 `{detail}`，都没有时取 fallback。 */
export function errText(e: unknown, fallback: string): string {
  const data = (e as AxiosLike)?.response?.data
  return data?.error?.message || data?.detail || fallback
}
