# Logcat 虚拟滚动与语法高亮 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 Logcat 视图落地虚拟滚动（DynamicScroller 动态行高替换 v-for 全量渲染）与消息语法高亮（自研轻量 tokenizer），使 06 模块达 100%。

**Architecture:** 纯前端改造。tokenizer 为无框架依赖纯函数（`frontend/src/utils/logcatHighlight.ts`），tokenize 原始文本后逐 token 转义拼接为可 `v-html` 的 HTML（XSS 安全）；`LogcatView.vue` 列表层从 v-for 换成 `DynamicScroller`/`DynamicScrollerItem`（vue-virtual-scroller 2.0.1，既有依赖），条目映射出稳定 `uid`，自动滚动改用组件暴露的 `scrollToBottom()`。

**Tech Stack:** Vue 3.5（script setup）/ Pinia / vitest + happy-dom / vue-virtual-scroller 2.0.1（**已在依赖，零新依赖**）

**Spec:** `方案/25-Logcat虚拟滚动与语法高亮方案.md`（含 API 核实订正：`key-field` / `:index` / `#empty` slot / `scrollToBottom()`；异常正则含裸 `Exception` 类）

## Global Constraints

- 测试文件位置：前端 co-located（`src/**/*.test.ts`），本方案不新增后端测试；后端零改动
- 门禁命令：`cd frontend && npx vitest run`；覆盖率 `npx vitest run --coverage`（`LogcatView.vue` ≥70%、services/stores ≥80% 阈值不动）；`npx eslint .`；`npm run build`（= `vue-tsc -b && vite build`）
- 后端确认命令：`PYTHONPATH=backend python -m pytest tests/ --ignore=tests/e2e -q`（应无任何影响）
- **零新依赖**：`frontend/package.json` 与 `package-lock.json` 不得出现 diff（vue-virtual-scroller 2.0.1 已在依赖中）
- 提交：每任务一次提交，中文信息，结尾 `Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>`
- 不新增 emoji；既有 UI 文案（⏸ ▶ LIVE）原样保留
- 浏览器基准仅 Chrome（最新两稳定版）；不使用 lookbehind 等非必要特性
- 环境：Windows Git Bash；工作目录 `D:\tangzk\py\scrcpy-web`；T4 真机用 `192.168.8.25:5555`（当前在线）
- 既有测试基建（Element Plus 轻量 stub / MockWebSocketService / mockApi）保留，测试语义只增不减

## Review Focus

1. **XSS**：v-html 注入路径必须逐 token 转义；超长消息（>2000 字符）回退路径同样只能输出转义文本——任何裸 `<` 进入 HTML 都是缺陷
2. **自动滚动回归**：仅 autoScroll 开启且 logs 长度变化时触发 `scrollToBottom()`；关闭后不滚；items 为空时不抛异常
3. **变高行截断/错位**（`5f2b92f` 移除虚拟滚动的元凶）：`size-dependencies=[item.message]` 正确传递、多行消息完整渲染、滚动到底部不跳动
4. **uid 唯一与回退**：`seq ?? ts-index` 混合场景不触发 vue-virtual-scroller 的 "Key is ... on item" 异常（键重复会直接 throw）
5. **交互零回归**：点击复制、级别过滤、搜索防抖、导出、清理、录制开关、空态（`.empty-state`）、暂停横幅（`.paused-banner`）、LIVE 指示器全部保持既有行为

---

### Task 1: tokenizer 纯函数与高亮渲染管线

**Files:**
- Create: `frontend/src/utils/logcatHighlight.ts`
- Create: `frontend/src/utils/logcatHighlight.test.ts`

**Interfaces:**
- Consumes: 无（纯函数，无外部依赖）
- Produces（@/utils/logcatHighlight）：
  - `export type TokenType = 'string' | 'url' | 'exception' | 'timestamp' | 'number' | 'kw' | 'default'`
  - `export interface HighlightToken { type: TokenType; text: string }`
  - `export const MAX_TOKENIZE_LEN = 2000`
  - `export function escapeHtml(text: string): string`
  - `export function tokenizeLogcatMessage(message: string): HighlightToken[]`
  - `export function highlightLogcatMessage(message: string): string`（带 5000 条 FIFO 缓存；超长仅转义）

- [ ] **Step 1: 写失败测试**（完整文件内容）

```ts
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx vitest run src/utils/logcatHighlight.test.ts`
Expected: FAIL —— `Cannot find module './logcatHighlight'`（或等价的模块解析错误）；不是断言失败

- [ ] **Step 3: 写最小实现**（完整文件内容）

```ts
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
```

- [ ] **Step 4: 跑测试确认通过 + 全量回归 + lint**

Run: `cd frontend && npx vitest run src/utils/logcatHighlight.test.ts`
Expected: PASS（全部用例）
Run: `cd frontend && npx vitest run && npx eslint src/utils/logcatHighlight.ts src/utils/logcatHighlight.test.ts`
Expected: 全量 PASS、eslint 0 错误

- [ ] **Step 5: 提交**

```bash
git add frontend/src/utils/logcatHighlight.ts frontend/src/utils/logcatHighlight.test.ts
git commit -m "feat(logcat): 自研轻量 tokenizer 与高亮渲染管线（方案 25 T1）"
```

---

### Task 2: LogcatView 列表层 DynamicScroller 改造（消息暂纯文本）

**Files:**
- Modify: `frontend/src/components/debug/LogcatView.vue`（模板 log-list 区块 + script）
- Modify: `frontend/src/components/debug/LogcatView.test.ts`（stub 基建 + 自动滚动用例改造 + uid 用例）

**Interfaces:**
- Consumes: `vue-virtual-scroller` 2.0.1 的 `{ DynamicScroller, DynamicScrollerItem }`（`key-field`/`:index`/`#empty` slot 均已核实）；随样式 `vue-virtual-scroller/dist/vue-virtual-scroller.css`
- Produces（供 T3/T4 依赖）：条目映射 `scrollerItems`（每项含 `uid: string`）；`scrollerRef`（`{ scrollToBottom(): void }`）；`.empty-state` 移入 `#empty` slot 但类名与文案不变；`.log-entry` 结构不变（message 元素仍为 `<span class="message">`）

- [ ] **Step 1: 改造 LogcatView.vue**

模板：将现有 `<div class="log-list" ref="logListRef">` 整块（含 `log-entries`/v-for/empty-state，约 :111-130）替换为：

```vue
    <DynamicScroller
      ref="scrollerRef"
      class="log-list"
      :items="scrollerItems"
      :min-item-size="22"
      key-field="uid"
    >
      <template #default="{ item, index, active }">
        <DynamicScrollerItem
          :item="item"
          :active="active"
          :index="index"
          :size-dependencies="[item.message]"
        >
          <div
            :class="['log-entry', `level-${item.level.toLowerCase()}`]"
            @click="copyLog(item)"
            :title="'点击复制'"
          >
            <span class="timestamp">{{ formatTime(item.ts) }}</span>
            <span class="level">{{ item.level }}</span>
            <span class="tag" :title="item.tag">{{ item.tag }}</span>
            <span class="message">{{ item.message }}</span>
          </div>
        </DynamicScrollerItem>
      </template>
      <template #empty>
        <div class="empty-state">
          <span v-if="!debugStore.isRecording">已暂停录制，无新日志</span>
          <span v-else>▶ 开始</span>
        </div>
      </template>
    </DynamicScroller>
```

script：
1. import 区新增：
```ts
import { DynamicScroller, DynamicScrollerItem } from 'vue-virtual-scroller'
import 'vue-virtual-scroller/dist/vue-virtual-scroller.css'
```
2. 删除 `logListRef` 声明、`scrollToBottom()` 函数；
3. 新增：
```ts
/** 虚拟滚动条目：LogEntry + 稳定 uid（seq 缺失时以 ts-index 兜底，键不得重复）。 */
const scrollerItems = computed(() =>
  filteredLogs.value.map((l, i) => ({ ...l, uid: String(l.seq ?? `${l.ts}-${i}`) })),
)

/** DynamicScroller 实例（暴露 scrollToBottom）。 */
const scrollerRef = ref<{ scrollToBottom: () => void } | null>(null)
```
4. `watch(() => debugStore.logs.length, ...)` 与 `watch(() => debugStore.isRecording, ...)` 内的 `scrollToBottom()` 调用改为：
```ts
      scrollerRef.value?.scrollToBottom()
```
（两处；保留 `if (autoScroll.value)` 与 `await nextTick()` 语义）
5. 删除 `<style scoped>` 中的 `.log-entries { display: flex; flex-direction: column; }` 规则（包裹层已不存在，成为死样式）；
6. 注释更新：组件头功能列表加「虚拟滚动（DynamicScroller 动态行高，缓冲上限 5 万条）」；性能优化段把「使用 computed 缓存过滤结果」保留、删除对全量渲染的旧表述；两处 watcher 上方注释中「v-for DOM 更新后再计算 scrollHeight」改为「DOM 更新后再触发 scrollToBottom」。

- [ ] **Step 2: 改造测试文件**

在文件头部 stub 区（elStubs 附近）新增：

```ts
const scrollerCalls = vi.hoisted(() => ({ scrollToBottom: vi.fn() }))

const DynamicScrollerStub = defineComponent({
  name: 'DynamicScroller',
  props: { items: { type: Array, default: () => [] } },
  setup(props, { slots, expose }) {
    expose({ scrollToBottom: scrollerCalls.scrollToBottom })
    return () => {
      const items = props.items as Array<Record<string, unknown>>
      // 关键：items 为空时渲染 #empty slot，否则既有 .empty-state 用例（:316 已暂停提示 / :323 ▶ 开始）失败
      return h(
        'div',
        { class: 'dynamic-scroller' },
        items.length
          ? items.map((item, index) => slots.default?.({ item, index, active: true }))
          : slots.empty?.(),
      )
    }
  },
})

const DynamicScrollerItemStub = defineComponent({
  name: 'DynamicScrollerItem',
  props: {
    item: { type: Object, required: true },
    active: Boolean,
    index: Number,
    sizeDependencies: { type: Array, default: null },
  },
  setup(_, { slots }) {
    return () => h('div', { class: 'dynamic-scroller-item' }, slots.default?.())
  },
})
```

`mountView` 的 `global.stubs` 改为：
```ts
      stubs: {
        ...elStubs(),
        DynamicScroller: DynamicScrollerStub,
        DynamicScrollerItem: DynamicScrollerItemStub,
      },
```

`beforeEach` 末尾新增：`scrollerCalls.scrollToBottom.mockClear()`

用以下用例**替换**原「自动滚动：新日志滚到底部，关闭开关后不再滚动」用例：

```ts
  it('自动滚动：新日志触发 scrollToBottom，关闭开关后不再滚动', async () => {
    const store = useDebugStore()
    const wrapper = mountView()
    await flushPromises()
    scrollerCalls.scrollToBottom.mockClear()

    store.logs = makeLogs()
    await flushPromises()
    expect(scrollerCalls.scrollToBottom).toHaveBeenCalledTimes(1)

    // 关闭自动滚动
    wrapper.findComponent(ElSwitch).vm.$emit('update:modelValue', false)
    await nextTick()
    scrollerCalls.scrollToBottom.mockClear()
    store.logs.push(makeLogs()[0])
    await flushPromises()
    expect(scrollerCalls.scrollToBottom).not.toHaveBeenCalled()
  })
```

`describe` 内新增 uid 用例：

```ts
  it('scrollerItems：有 seq 用 seq，无 seq 回退 ts-index', async () => {
    const store = useDebugStore()
    store.logs = [
      { ts: 100, level: 'I', pid: 1, tid: 1, tag: 'T', message: 'a', seq: 7 },
      { ts: 200, level: 'I', pid: 1, tid: 1, tag: 'T', message: 'b' },
    ]
    const wrapper = mountView()
    await flushPromises()

    const scroller = wrapper.findComponent(DynamicScrollerStub)
    const items = scroller.props('items') as Array<{ uid: string }>
    expect(items.map((i) => i.uid)).toEqual(['7', '200-1'])
  })
```

其余用例不动（stub 全量渲染所有 items，`.log-entry` 断言与既有语义一致）。

- [ ] **Step 3: 按 RED→GREEN 顺序执行（组件与测试需协同改动，RED 点在此）**

1. 先只做 Step 2 中的 **stub 基建**（`scrollerCalls` / 两个 stub / `mountView` 注册 / `beforeEach` 清理，不改任何用例）→ Run: `cd frontend && npx vitest run src/components/debug/LogcatView.test.ts` → Expected: 全 PASS（基建无副作用；旧模板未用 DynamicScroller，stub 只是登记）
2. 再做 Step 1 组件改造 → Run 同上 → Expected: **FAIL**——旧「自动滚动」用例在 `expect(list.scrollTop).toBe(777)` 处失败（滚动改由 scrollerRef 暴露的 `scrollToBottom()` 承担，DOM scrollTop 不再变化），其余用例 PASS（`.log-entry` / `.empty-state` 断言由 stub 的 default/#empty 渲染兜住）
3. 最后做 Step 2 的**用例改造**（替换自动滚动用例 + 新增 uid 用例）→ Run 同上 → Expected: 全 PASS（GREEN）

- [ ] **Step 4: 全量回归 + lint + build**

Run: `cd frontend && npx vitest run && npx eslint . && npm run build`
Expected: 全量 PASS、eslint 0、vue-tsc + vite build 成功

- [ ] **Step 5: 提交**

```bash
git add frontend/src/components/debug/LogcatView.vue frontend/src/components/debug/LogcatView.test.ts
git commit -m "feat(logcat): 列表层改用 DynamicScroller 动态行高虚拟滚动（方案 25 T2）"
```

---

### Task 3: 消息高亮接入（v-html + .hl-* 样式）

**Files:**
- Modify: `frontend/src/components/debug/LogcatView.vue`（message 渲染 + 样式）
- Modify: `frontend/src/components/debug/LogcatView.test.ts`（高亮断言用例）

**Interfaces:**
- Consumes: T1 的 `highlightLogcatMessage(message: string): string`（@/utils/logcatHighlight）
- Produces: `.message` 以 `v-html` 渲染；样式类 `hl-string / hl-url / hl-exception / hl-timestamp / hl-number / hl-kw`（T4 浏览器验证按类名抽查）

- [ ] **Step 1: 改 LogcatView.vue**

1. import 新增：`import { highlightLogcatMessage } from '@/utils/logcatHighlight'`
2. 新增函数（放 copyLog 附近）：
```ts
/** 消息高亮 HTML（tokenize + 缓存 + 转义），v-html 渲染。 */
function highlightMessage(item: { message: string }) {
  return highlightLogcatMessage(item.message)
}
```
3. 模板 message 行改为：
```vue
            <span class="message" v-html="highlightMessage(item)"></span>
```
4. `<style scoped>` 末尾新增（v-html 内容需 :deep 穿透）：
```css
/* 消息语法高亮（方案 25）：与级别列色系错开 */
.message :deep(.hl-string) { color: #a31515; }
.message :deep(.hl-url) { color: #1a73e8; text-decoration: underline; }
.message :deep(.hl-exception) { color: #c7254e; font-weight: bold; }
.message :deep(.hl-timestamp) { color: #8a8a8a; }
.message :deep(.hl-number) { color: #098658; }
.message :deep(.hl-kw) { color: #7b5bcd; font-style: italic; }
```
5. 组件头注释功能列表加「消息语法高亮（string/url/exception/timestamp/number/kw，自研 tokenizer）」。

- [ ] **Step 2: 新增测试用例**（describe 内追加）

```ts
  it('消息高亮：异常类名/数字/关键字渲染为 hl-* span', async () => {
    const store = useDebugStore()
    store.logs = [
      {
        ts: 1700000000,
        level: 'E',
        pid: 1,
        tid: 1,
        tag: 'Crash',
        message: 'FATAL at com.example.foo.MyCustomError count 42',
      },
    ]
    const wrapper = mountView()
    await flushPromises()

    const msg = wrapper.find('.message')
    expect(msg.find('.hl-exception').text()).toBe('com.example.foo.MyCustomError')
    expect(msg.find('.hl-number').text()).toBe('42')
    expect(msg.find('.hl-kw').text()).toBe('at')
    expect(msg.text()).toContain('count')
  })

  it('消息高亮：恶意 HTML 全量转义（XSS 防护）', async () => {
    const store = useDebugStore()
    store.logs = [
      { ts: 1700000000, level: 'I', pid: 1, tid: 1, tag: 'T', message: '<img src=x onerror=alert(1)>' },
    ]
    const wrapper = mountView()
    await flushPromises()

    const msg = wrapper.find('.message')
    expect(msg.find('img').exists()).toBe(false)
    expect(msg.html()).toContain('&lt;img')
  })
```

- [ ] **Step 3: 跑测试 + 全量回归 + lint + build**

Run: `cd frontend && npx vitest run src/components/debug/LogcatView.test.ts`
Expected: PASS
Run: `cd frontend && npx vitest run --coverage && npx eslint . && npm run build`
Expected: 全量 PASS；覆盖率阈值通过（LogcatView ≥70%）

- [ ] **Step 4: 提交**

```bash
git add frontend/src/components/debug/LogcatView.vue frontend/src/components/debug/LogcatView.test.ts
git commit -m "feat(logcat): 消息语法高亮接入 v-html 渲染（方案 25 T3）"
```

---

### Task 4: 门禁终跑 + 浏览器/真机验证 + 文档收尾

**Files:**
- Modify: `方案/06-Logcat日志系统.md`（状态节两处 ⏳→✅、验收节、交付物、状态头）
- Modify: `方案/进度追踪.md`（Week 5-6 Logcat 行 → 100%、Logcat 节两处 ⏳→✅、前端调试面板行、第 37 次更新、footer）
- Modify: `README.md`（06 行 → 100%、专项表 25 行 → 已实施、最新进展/下一步）
- Modify: `方案/25-Logcat虚拟滚动与语法高亮方案.md`（状态头：立项 → 已实施）
- Modify: `方案/README.md`（索引 25 行 → 已实施 + v1.9 更新记录行）

**Interfaces:**
- Consumes: T1~T3 全部产物 + 本任务产生的验证数据（DOM 行数、截图结论、门禁数字）
- Produces: 06 模块 100% 的文档口径；验证证据记录

- [ ] **Step 1: 门禁全量终跑**

Run:
```bash
cd frontend && npx vitest run --coverage 2>&1 | tail -30
cd frontend && npx eslint . && npm run build
PYTHONPATH=backend python -m pytest tests/ --ignore=tests/e2e -q 2>&1 | tail -5
cd /d/tangzk/py/scrcpy-web && git status --short && git diff --stat frontend/package.json frontend/package-lock.json
```
Expected: vitest 全绿且覆盖率阈值通过；eslint/build 全绿；后端套件无失败；lock 文件**无 diff**（零新依赖验证）

- [ ] **Step 2: 浏览器 + 真机验证**（.25 真机 logcat 灌 5 万条）

前置（Git Bash）：
```bash
MSYS_NO_PATHCONV=1 adb connect 192.168.8.25:5555
curl -s http://localhost:8765/health        # 后端在线（本会话既有 dev 后端）
cd frontend && npm run dev                   # 若 8080 未在跑；代理 /api → 8765
MSYS_NO_PATHCONV=1 adb -s 192.168.8.25:5555 shell 'log -p e -t HLTEST "FATAL at com.example.foo.MyCustomError count 42"'
```
（log 注入若提示 log: not found，记为已知环境差异继续——设备稳态 logcat 自身亦含数字/字符串 token，截图抽查 hl-number/hl-string 即可）

创建 `D:/tmp/logcat_verify.mjs`（临时脚本不入仓）：

```js
// 方案 25 浏览器验证：.25 真机 logcat → 50K 缓冲虚拟滚动 + 高亮目验
import { createRequire } from 'module'
const require = createRequire('D:/tangzk/py/scrcpy-web/frontend/package.json')
const { chromium } = require('playwright')

const BASE = 'http://localhost:8080'
const DEVICE = '192.168.8.25:5555'

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1600, height: 900 } })
const errors = []
page.on('console', (m) => {
  if (m.type() === 'error') errors.push(m.text())
})
page.on('pageerror', (e) => errors.push(String(e)))

await page.goto(`${BASE}/device/${encodeURIComponent(DEVICE)}`, { waitUntil: 'domcontentloaded' })
await page.waitForSelector('.logcat-view', { timeout: 15000 })

// 开始录制
await page.click('button:has-text("开始")')

// 等缓冲到 5 万上限（约 538 条/秒 → ~93s；上限 180s）
await page.waitForFunction(
  () => {
    const el = document.querySelector('.stats-control')
    const m = el && /共 (\d+) 条/.exec(el.textContent || '')
    return !!m && Number(m[1]) >= 50000
  },
  { timeout: 180000 },
)

const stats = await page.evaluate(() => {
  const s = document.querySelector('.log-list')
  return {
    statsText: document.querySelector('.stats-control')?.textContent?.trim(),
    domRows: document.querySelectorAll('.log-entry').length,
    scrollHeight: s ? s.scrollHeight : 0,
    clientHeight: s ? s.clientHeight : 0,
  }
})
console.log('STATS', JSON.stringify(stats))

async function snap(name) {
  await page.waitForTimeout(500)
  const rows = await page.evaluate(() => document.querySelectorAll('.log-entry').length)
  await page.screenshot({ path: `D:/tmp/logcat_${name}.png` })
  return rows
}

await page.evaluate(() => {
  const s = document.querySelector('.log-list')
  if (s) s.scrollTop = 0
})
const topRows = await snap('top')

await page.evaluate(() => {
  const s = document.querySelector('.log-list')
  if (s) s.scrollTop = Math.floor(s.scrollHeight / 2)
})
const midRows = await snap('mid')

await page.evaluate(() => {
  const s = document.querySelector('.log-list')
  if (s) s.scrollTop = s.scrollHeight
})
const botRows = await snap('bottom')

const hl = await page.evaluate(() => ({
  exception: document.querySelectorAll('.log-entry .hl-exception').length,
  number: document.querySelectorAll('.log-entry .hl-number').length,
  string: document.querySelectorAll('.log-entry .hl-string').length,
}))
console.log('DOM_ROWS', JSON.stringify({ topRows, midRows, botRows }))
console.log('HIGHLIGHT', JSON.stringify(hl))
console.log('CONSOLE_ERRORS', JSON.stringify(errors.slice(0, 10)))
await browser.close()
```

Run: `cd frontend && node D:/tmp/logcat_verify.mjs`

**通过判据**（写入报告，作为文档更新的证据）：
- `STATS.domRows < 300`（5 万条仅渲染视窗行 → 虚拟滚动生效）
- `DOM_ROWS` 三项均在数百内（滚动各位置维持虚拟化）
- `CONSOLE_ERRORS` 为空或与本功能无关的既有噪音
- 截图 `D:/tmp/logcat_top.png` / `logcat_mid.png` / `logcat_bottom.png`：行高完整无截断无重叠、彩色高亮可见、LIVE 指示与统计条正常
- `HIGHLIGHT` 至少 number/string > 0；exception 若为 0 记录为「设备稳态未出现异常类名」的观测事实（不阻塞）

- [ ] **Step 3: 文档订正**（按下述精确替换执行；验证数字以 Step 1/2 实际输出为准）

`方案/06-Logcat日志系统.md`：
- 状态节：`- ⏳ 虚拟滚动：曾实现（2026-09-09，vue-virtual-scroller）后于 \`5f2b92f\` 移除，当前为 v-for 全量渲染（缓冲上限 5 万条）——已立项：方案 25（DynamicScroller 复用 + 自研 tokenizer），实施待排期（2026-09-29）` → `- ✅ 虚拟滚动：DynamicScroller（vue-virtual-scroller 2.0.1）动态行高 + uid 键 + scrollToBottom 自动滚动；5 万条实测仅渲染视窗行（方案 25 实施，2026-09-29）`
- 状态节：`- ⏳ 日志语法高亮——可选增强（当前以级别着色替代）——已立项：方案 25（自研轻量 tokenizer，与虚拟滚动一并实施，2026-09-29）` → `- ✅ 日志语法高亮：自研轻量 tokenizer（string/url/exception/timestamp/number/kw），逐 token 转义防 XSS（方案 25 实施，2026-09-29）`
- 验收节：`- ⏳ 虚拟滚动流畅（5 万条不卡顿）——曾实现后移除，当前全量渲染，见状态节订正` → `- ✅ 虚拟滚动流畅（5 万条真机实测，DOM 视窗行数 < 300，2026-09-29）`
- 验收节：`- ⏳ 日志语法高亮（未实现，当前以级别着色替代）` → `- ✅ 日志语法高亮（tokenizer 单测全绿 + 真机彩色渲染目验，2026-09-29）`
- 交付物：`- ✅ LogcatView 组件（当前全量渲染；虚拟滚动已移除，见状态节）` → `- ✅ LogcatView 组件（DynamicScroller 虚拟滚动 + 消息语法高亮，2026-09-29）`
- 前端实现 §虚拟滚动 小节：在「方案：使用 vue-virtual-scroller」段落替换为 `**已实现（方案 25，2026-09-29）**：DynamicScroller 动态行高（弃用固定行高的 RecycleScroller）+ uid 键 + scrollToBottom 自动滚动；消息以自研 tokenizer 高亮（utils/logcatHighlight.ts）。下方示例为历史设计稿（`type` 字段协议已随 5f2b92f 演进），以实际实现为准。`

`方案/25-Logcat虚拟滚动与语法高亮方案.md`：
- 状态头：`> **状态**：立项（设计已定，实施待排期——需经 writing-plans 出实施计划后 TDD 落地）。` → `> **状态**：已实施（2026-09-29，SDD T1~T4 完成；5 万条真机浏览器验证，见进度追踪）。`
- §9 末注 `> 注意：本立项文档完成即「立项」；实施须先经用户审阅并通过 writing-plans 出实施计划，不在本轮直接开工（与既有项目节奏一致）。` → `> 实施记录：已按本方案经 writing-plans 出实施计划并经 SDD 落地（2026-09-29）。`

`方案/进度追踪.md`：
- `| Week 5-6 | Logcat 日志系统 | ✅ 完成 | 98% | 智能精简 + 自动清理 + 导出（余语法高亮为可选增强） |` → `| Week 5-6 | Logcat 日志系统 | ✅ 完成 | 100% | 智能精简 + 自动清理 + 导出 + 虚拟滚动（DynamicScroller，5 万条实测）+ 语法高亮（方案 25） |`
- Logcat 节：`- ⏳ 前端虚拟滚动——曾实现（2026-09-09）后于 \`5f2b92f\` 移除，当前全量渲染（缓冲上限 5 万条）；已立项：方案 25（DynamicScroller 复用 + 自研 tokenizer，实施待排期，2026-09-29）` → `- ✅ 前端虚拟滚动（DynamicScroller 2.0.1 动态行高；.25 真机 5 万条实测仅渲染视窗行，方案 25，2026-09-29）`
- Logcat 节：`- ⏳ 日志语法高亮——可选增强（当前以级别着色替代）；已立项：方案 25（自研轻量 tokenizer，与虚拟滚动一并实施，2026-09-29）` → `- ✅ 日志语法高亮（自研轻量 tokenizer，逐 token 转义防 XSS；方案 25，2026-09-29）`
- 前端调试面板节：`- ✅ LogcatView 实时日志（当前全量渲染；虚拟滚动已移除，见 Logcat 节订正）` → `- ✅ LogcatView 实时日志（DynamicScroller 虚拟滚动 + 消息语法高亮，2026-09-29）`
- 在 35/36 次更新之后追加第 37 次更新（沿既有格式）：

```markdown
### 2026-09-29（第三十七次更新 - 方案 25 实施，Logcat 达 100%）

> 背景：06 模块唯二遗留缺口（虚拟滚动 + 语法高亮）按方案 25 经 SDD 四任务落地。

- ✅ Logcat 升级 **100%**：列表层 DynamicScroller 动态行高虚拟滚动（uid 键 / size-dependencies / scrollToBottom）+ 消息语法高亮（自研 tokenizer：单遍粘性正则、逐 token 转义防 XSS、2000 字符上限 + 5000 条缓存）
- ✅ 验证：tokenizer 单测全覆盖（含 XSS/长度/缓存淘汰）；.25 真机 5 万条 logcat 浏览器实测——DOM 视窗行数实测 N 行（判据 <300，N 以 Step 2 输出 STATS.domRows 填入），滚动截图行高完整、高亮彩色可见
- ✅ 门禁：vitest 全绿（LogcatView 覆盖 ≥70%）、eslint/build 全绿、后端套件不受影响、零新依赖（lock 无 diff）
- 联动订正：06 方案文档状态/验收/交付物、25 方案状态头、README 模块表与专项表
```

- footer：`**最后更新**：2026-09-29（第三十六次更新 - 方案 25 立项）` → `**最后更新**：2026-09-29（第三十七次更新 - 方案 25 实施，Logcat 达 100%）`

`README.md`：
- `| Week 5-6 | Logcat 日志系统 | [06-Logcat日志系统.md](方案/06-Logcat日志系统.md) | ✅ 98%（余虚拟滚动 + 语法高亮，已立项方案 25） |` → `| Week 5-6 | Logcat 日志系统 | [06-Logcat日志系统.md](方案/06-Logcat日志系统.md) | ✅ 100%（含虚拟滚动 + 语法高亮，方案 25） |`
- `| 25 | [Logcat 虚拟滚动与语法高亮](方案/25-Logcat虚拟滚动与语法高亮方案.md) | 📋 已立项（2026-09-29，复用 DynamicScroller 零新依赖 + 自研轻量 tokenizer，实施待排期） |` → `| 25 | [Logcat 虚拟滚动与语法高亮](方案/25-Logcat虚拟滚动与语法高亮方案.md) | ✅ 已实施（2026-09-29，DynamicScroller 虚拟滚动 + 自研 tokenizer，5 万条真机验证） |`
- 最新进展/下一步两行：`**最新进展**：2026-09-29（07 终端 resize 完成并真机验证，远程 Shell 达 100%；方案 25 立项（Logcat 虚拟滚动 + 语法高亮）；01/08/09 已升 100%；方案 24 已实施并运行时验证，CI 三 job 全绿）` → `**最新进展**：2026-09-29（方案 25 实施并 5 万条真机验证，Logcat 达 100%——模块 01/07/08/09 同达 100%；方案 24 已实施并运行时验证，CI 三 job 全绿）`；`**下一步**：方案 25 实施（DynamicScroller + 自研 tokenizer，TDD 分任务）；绿色版试用分发（zip + 发布说明随包）` → `**下一步**：绿色版试用分发（zip + 发布说明随包）；MVP 发布决策（pre-release 就绪）`

`方案/README.md`：
- `- [25-Logcat虚拟滚动与语法高亮方案.md](./25-Logcat虚拟滚动与语法高亮方案.md) - 复用 DynamicScroller（零新依赖）+ 自研轻量 tokenizer（立项 2026-09-29，实施待排期）` → `- [25-Logcat虚拟滚动与语法高亮方案.md](./25-Logcat虚拟滚动与语法高亮方案.md) - 复用 DynamicScroller（零新依赖）+ 自研轻量 tokenizer（已实施 2026-09-29）`
- 更新记录表追加一行：`| 2026-09-29 | v1.9 | 25 已实施（SDD T1~T4 + 5 万条真机验证） | - |`

- [ ] **Step 4: 提交**

```bash
git add 方案/06-Logcat日志系统.md 方案/25-Logcat虚拟滚动与语法高亮方案.md 方案/进度追踪.md 方案/README.md README.md
git commit -m "docs: 方案 25 实施完成回写，Logcat 模块达 100%"
```