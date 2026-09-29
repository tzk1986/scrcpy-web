/**
 * Logcat 消息语法高亮 tokenizer 单测（方案 25）
 * ==============================================
 * 覆盖：escapeHtml 五字符转义、各 token 类型正/负例、匹配优先级、
 * 长度上限回退、XSS 转义、缓存淘汰后输出不损坏。
 */
import { describe, expect, it } from 'vitest'
import {
  escapeHtml,
  highlightLogcatMessage,
  MAX_TOKENIZE_LEN,
  tokenizeLogcatMessage,
} from './logcatHighlight'

describe('escapeHtml', () => {
  it('转义五个 HTML 敏感字符', () => {
    expect(escapeHtml(`<a href="x" title='y'>&</a>`)).toBe(
      '&lt;a href=&quot;x&quot; title=&#39;y&#39;&gt;&amp;&lt;/a&gt;',
    )
  })
})

describe('tokenizeLogcatMessage', () => {
  it('纯文本：单个 default 段，文本原样', () => {
    expect(tokenizeLogcatMessage('plain text here')).toEqual([
      { type: 'default', text: 'plain text here' },
    ])
  })

  it('空字符串：空数组', () => {
    expect(tokenizeLogcatMessage('')).toEqual([])
  })

  it('string：双引号串整体命中（含转义引号）', () => {
    expect(tokenizeLogcatMessage('say "hello world" ok')).toEqual([
      { type: 'default', text: 'say ' },
      { type: 'string', text: '"hello world"' },
      { type: 'default', text: ' ok' },
    ])
    expect(tokenizeLogcatMessage('a "x\\"y" b')).toEqual([
      { type: 'default', text: 'a ' },
      { type: 'string', text: '"x\\"y"' },
      { type: 'default', text: ' b' },
    ])
  })

  it('url：整段命中（内部数字/冒号不被拆散）', () => {
    expect(tokenizeLogcatMessage('see http://example.com:8080/path?a=1 now')).toEqual([
      { type: 'default', text: 'see ' },
      { type: 'url', text: 'http://example.com:8080/path?a=1' },
      { type: 'default', text: ' now' },
    ])
  })

  it('exception：包名限定异常/错误类命中，无包前缀不命中', () => {
    expect(tokenizeLogcatMessage('java.lang.NullPointerException')).toEqual([
      { type: 'exception', text: 'java.lang.NullPointerException' },
    ])
    expect(tokenizeLogcatMessage('com.example.foo.MyCustomError!')).toEqual([
      { type: 'exception', text: 'com.example.foo.MyCustomError' },
      { type: 'default', text: '!' },
    ])
    expect(tokenizeLogcatMessage('java.lang.Exception')).toEqual([
      { type: 'exception', text: 'java.lang.Exception' },
    ])
    expect(tokenizeLogcatMessage('saw myException rise')).toEqual([
      { type: 'default', text: 'saw myException rise' },
    ])
  })

  it('timestamp：HH:MM:SS(.mmm) 命中，非完整时刻不产出 timestamp', () => {
    expect(tokenizeLogcatMessage('at 12:34:56.789 done')).toEqual([
      { type: 'kw', text: 'at' },
      { type: 'default', text: ' ' },
      { type: 'timestamp', text: '12:34:56.789' },
      { type: 'default', text: ' done' },
    ])
    const tokens = tokenizeLogcatMessage('123:456')
    expect(tokens.some((t) => t.type === 'timestamp')).toBe(false)
  })

  it('number：负数/小数/科学计数；引号内数字归 string', () => {
    expect(tokenizeLogcatMessage('value -3.14e2 end')).toEqual([
      { type: 'default', text: 'value ' },
      { type: 'number', text: '-3.14e2' },
      { type: 'default', text: ' end' },
    ])
    expect(tokenizeLogcatMessage('"n 5"')).toEqual([{ type: 'string', text: '"n 5"' }])
  })

  it('kw：at / Caused by 独立词命中，attach 不命中', () => {
    expect(tokenizeLogcatMessage('at com.foo.MyException')).toEqual([
      { type: 'kw', text: 'at' },
      { type: 'default', text: ' ' },
      { type: 'exception', text: 'com.foo.MyException' },
    ])
    expect(tokenizeLogcatMessage('attach')).toEqual([{ type: 'default', text: 'attach' }])
    expect(tokenizeLogcatMessage('Caused by: java.io.IOException')).toEqual([
      { type: 'kw', text: 'Caused by' },
      { type: 'default', text: ': ' },
      { type: 'exception', text: 'java.io.IOException' },
    ])
  })

  it('优先级：引号串吞内嵌 URL；url 段止于引号', () => {
    expect(tokenizeLogcatMessage('"http://x"')).toEqual([{ type: 'string', text: '"http://x"' }])
    expect(tokenizeLogcatMessage('http://x/"a"')).toEqual([
      { type: 'url', text: 'http://x/' },
      { type: 'string', text: '"a"' },
    ])
  })
})

describe('highlightLogcatMessage', () => {
  it('default 段不包裹 span，仅转义', () => {
    expect(highlightLogcatMessage('a < b')).toBe('a &lt; b')
  })

  it('高亮 token 渲染为 hl-* span', () => {
    expect(highlightLogcatMessage('x "s" 7')).toBe(
      'x <span class="hl-string">&quot;s&quot;</span> <span class="hl-number">7</span>',
    )
  })

  it('XSS：恶意标签全量转义，无裸 <', () => {
    const html = highlightLogcatMessage('<script>alert(1)</script>')
    expect(html).not.toContain('<script')
    expect(html).toContain('&lt;script&gt;')
  })

  it('长度上限：超限消息整体转义、不产出高亮 span', () => {
    const msg = '"quoted" '.repeat(Math.ceil((MAX_TOKENIZE_LEN + 1) / 9))
    expect(msg.length).toBeGreaterThan(MAX_TOKENIZE_LEN)
    const html = highlightLogcatMessage(msg)
    expect(html).not.toContain('hl-string')
    expect(html).toContain('&quot;quoted&quot;')
  })

  it('缓存淘汰不损坏输出：溢出后旧消息重算仍正确', () => {
    const first = highlightLogcatMessage('cache probe "a"')
    expect(first).toContain('hl-string')
    for (let i = 0; i < 5001; i++) highlightLogcatMessage(`filler ${i}`)
    expect(highlightLogcatMessage('cache probe "a"')).toBe(first)
  })
})