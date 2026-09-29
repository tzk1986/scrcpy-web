/**
 * Logcat 消息语法高亮（方案 25）
 * ==============================
 *
 * 自研轻量 tokenizer：单遍字符游标 + 粘性正则规则表，无回溯、无嵌套 token。
 * 规则优先级（首个命中者整段吞下）：string → url → exception → timestamp → number → kw。
 *
 * 安全：tokenize 原始文本后逐 token escapeHtml 再拼接，v-html 渲染路径无 XSS；
 * 超长消息（> MAX_TOKENIZE_LEN）整体转义、不做 tokenize（性能护栏）。
 * 性能：highlightLogcatMessage 结果按原始消息缓存（FIFO 上限 5000 条）。
 */

export type TokenType = 'string' | 'url' | 'exception' | 'timestamp' | 'number' | 'kw' | 'default'

export interface HighlightToken {
  type: TokenType
  text: string
}

/** 消息长度上限：超过则不 tokenize，整体转义原色渲染。 */
export const MAX_TOKENIZE_LEN = 2000

/** 高亮结果缓存上限（FIFO 淘汰）。 */
const CACHE_CAP = 5000

interface Rule {
  type: TokenType
  re: RegExp
}

const RULES: Rule[] = [
  { type: 'string', re: /"(?:[^"\\]|\\.)*"/y },
  { type: 'url', re: /https?:\/\/[^\s"'<>()]+/y },
  {
    type: 'exception',
    re: /(?:[a-z][a-z0-9_$]*\.)+(?:[A-Z][A-Za-z0-9_$]*(?:Exception|Error)|Exception|Error)\b/y,
  },
  { type: 'timestamp', re: /\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?/y },
  { type: 'number', re: /-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/y },
  { type: 'kw', re: /\b(?:Caused by|at)\b/y },
]

const ESCAPE_MAP: Record<string, string> = {
  '&': '&amp;',
  '<': '&lt;',
  '>': '&gt;',
  '"': '&quot;',
  "'": '&#39;',
}

/** HTML 敏感字符转义（五字符全覆盖）。 */
export function escapeHtml(text: string): string {
  return text.replace(/[&<>"']/g, (ch) => ESCAPE_MAP[ch])
}

/** 将消息切分为平铺 token 流（不重叠、无嵌套）。 */
export function tokenizeLogcatMessage(message: string): HighlightToken[] {
  const tokens: HighlightToken[] = []
  let plain = ''
  let i = 0
  while (i < message.length) {
    let matched: HighlightToken | null = null
    for (const { type, re } of RULES) {
      re.lastIndex = i
      const m = re.exec(message)
      if (m) {
        matched = { type, text: m[0] }
        break
      }
    }
    if (matched) {
      if (plain) {
        tokens.push({ type: 'default', text: plain })
        plain = ''
      }
      tokens.push(matched)
      i += matched.text.length
    } else {
      plain += message[i]
      i += 1
    }
  }
  if (plain) tokens.push({ type: 'default', text: plain })
  return tokens
}

const cache = new Map<string, string>()

/** 渲染为可 v-html 的 HTML 字符串（带缓存；超长消息仅转义）。 */
export function highlightLogcatMessage(message: string): string {
  const cached = cache.get(message)
  if (cached !== undefined) return cached

  let html: string
  if (message.length > MAX_TOKENIZE_LEN) {
    html = escapeHtml(message)
  } else {
    html = tokenizeLogcatMessage(message)
      .map((t) =>
        t.type === 'default'
          ? escapeHtml(t.text)
          : `<span class="hl-${t.type}">${escapeHtml(t.text)}</span>`,
      )
      .join('')
  }

  if (cache.size >= CACHE_CAP) {
    const oldest = cache.keys().next().value
    if (oldest !== undefined) cache.delete(oldest)
  }
  cache.set(message, html)
  return html
}