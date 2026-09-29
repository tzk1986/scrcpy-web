# 方案 25：Logcat 虚拟滚动与语法高亮

> **状态**：已实施（2026-09-29，SDD T1~T4 完成；5 万条真机浏览器验证，见进度追踪）。
>
> **由来**：方案 06 走查遗留两个 ⏳ 缺口——前端虚拟滚动（曾实现，2026-09-09；后于
> `5f2b92f` 移除，当前 v-for 全量渲染）与日志语法高亮（可选增强，当前以级别着色替代）。
> 2026-09-28 完成度走查将两者绑定（一并规划），经用户拍板立项。
>
> **用户已定方向（2026-09-28）**：
> 1. 虚拟滚动：**复用 DynamicScroller**（vue-virtual-scroller 仍为项目既有依赖，零新依赖）；
> 2. 语法高亮：**自研轻量 tokenizer**（不引 highlight.js / Prism 等重型高亮库）。
>
> **关联**：方案 06（Logcat 日志系统）、方案 17（实施项 5——同批 `5f2b92f` 的录制/搜索/清理改造）。

---

## 1. 背景与现状

### 1.1 现状（代码事实，2026-09-29 核实）

**渲染路径**

- `frontend/src/components/debug/LogcatView.vue` 当前为 **v-for 全量渲染**（`:111-129`）：
  `filteredLogs` 全部条目直接生成 DOM。store 缓冲上限 5 万条（`debug.ts` `appendLog` FIFO），
  即极端情形 **5 万行 DOM**。行高可变：`.message { white-space: pre-wrap; word-break: break-all; }`
  （`:482-486`）——长消息折行成多行。
- 自动滚动：`watch(debugStore.logs.length)` → `nextTick` → `scrollToBottom()`
  （`:219-224`；`scrollToBottom` 用 `scrollTop = scrollHeight`，`:323-327`）。
- 过滤：`filteredLogs` computed（级别 + 搜索防抖，`:170-195`）；服务端过滤由 store `setFilter` 同步。

**依赖事实（关键）**

- `frontend/package.json:26` 仍声明 `"vue-virtual-scroller": "^2.0.0-beta.8"`；
  `package-lock.json` 实际解析 **2.0.1**，`node_modules` 在位。即：**`5f2b92f` 只移除了组件用法，
  依赖从未卸载** → 复用 DynamicScroller 零新依赖、lock 无变化。
- 全 frontend 无任何文件 import vue-virtual-scroller（已 grep 核实）。

**测试基建**

- `LogcatView.test.ts` 已有完整 stub 体系（MockWebSocketService / mockApi / Element Plus 轻量 stub），
  组件以 `shallowMount` 真实挂载。改造时需增补 DynamicScroller 系 stub。
- 覆盖率门禁（`frontend/vitest.config.ts`）：`src/components/debug/LogcatView.vue` **70%**（改造后需保持）。

### 1.2 缺口

| # | 缺口 | 后果 |
|---|------|------|
| G1 | 5 万行 DOM 全量渲染 | 大缓冲下滚动卡顿、初始渲染阻塞（体验问题，当前靠服务端过滤缓解） |
| G2 | 仅级别着色 | 消息内容无结构高亮：异常类名/URL/数字/引号串与普通文本混色，长日志可读性差 |

### 1.3 历史教训：`5f2b92f` 为何移除 RecycleScroller（必须规避的坑）

当时用的是 **RecycleScroller**（固定 `:item-size="24"`，见 06 方案 §前端实现示例）：

1. **固定行高与变高行冲突**：`.message` 折行使行高按内容膨胀，24px 固定高度下多行日志被截断
   或视窗高度计算错乱（滚动跳动、底部错位）——这是移除的直接原因类别。
2. 同批提交还引入了录制/搜索等大改，虚拟滚动问题被连带下线，未单独修复。

**本次对策**：改用 **DynamicScroller**（动态测量行高，`size-dependencies` 复用同文本行高），
从机制上规避固定行高假设；并单独成方案、单独成提交，不再与其它功能并轨。

---

## 2. 设计决策

| # | 决策 | 说明 |
|---|------|------|
| D1 | 复用 DynamicScroller（不引新依赖） | vue-virtual-scroller 2.0.1 已在依赖中；用 `DynamicScroller` + `DynamicScrollerItem`，弃用固定行高的 RecycleScroller |
| D2 | 自研轻量 tokenizer | 纯函数、单遍字符级扫描、规则表驱动；无第三方高亮库依赖（highlight.js/Prism 体积与依赖扩面不值当） |
| D3 | 高亮范围仅 message 列 | 时间戳/级别/tag/pid 已有列级着色；只对消息正文做结构高亮 |
| D4 | tokenize 原始文本 → 逐 token 转义 | 先切 token 再对每段 `escapeHtml`，保证 XSS 安全的同时不破坏引号串等 token 的可识别性（先转义后切会让引号串变成 `&quot;` 无法匹配） |
| D5 | 性能护栏：长度上限 + 结果缓存 | 消息 > 2000 字符不 tokenize（整体转义原色渲染）；tokenize 结果（HTML 字符串）缓存 Map，5000 条 FIFO 淘汰——复合性：虚拟滚动下仅视窗内行才渲染，tokenize 请求天然受限 |
| D6 | 自动滚动改 `scrollToBottom()` | DynamicScroller 缓冲高度下 `scrollHeight` 是估算值，`scrollTop = scrollHeight` 不可靠；改用组件暴露的 `scrollToBottom()` API 滚底对标旧行为（2.0.1 已提供，无需索引自算） |
| D7 | 条目 ID：`uid = seq ?? \`${ts}-${i}\`` | DynamicScroller 以 `keyField`（默认 `id`）作条目键，键值不得重复；`LogEntry.seq` 为可选字段（TS 类型上），映射成稳定 uid 兜底 |
| D8 | 交互零变化 | 过滤/搜索/导出/清理/点击复制/录制开关/LIVE 指示器/暂停横幅全部沿用，仅列表渲染层替换 |

## 3. 虚拟滚动设计

### 3.1 组件结构（在现有模板上做最小替换）

```vue
<!-- 替换 log-list 内 v-for 区块（API 已按 2.0.1 实际 d.ts 核实：key-field / :index / #empty） -->
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
      <div class="log-entry" :class="`level-${item.level.toLowerCase()}`"
           @click="copyLog(item)" :title="'点击复制'">
        <span class="timestamp">{{ formatTime(item.ts) }}</span>
        <span class="level">{{ item.level }}</span>
        <span class="tag" :title="item.tag">{{ item.tag }}</span>
        <span class="message" v-html="highlightMessage(item)"></span>
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

- `scrollerItems = computed(() => filteredLogs.value.map((l, i) => ({ ...l, uid: String(l.seq ?? `${l.ts}-${i}`) })))`
  （过滤条件变化时 uid 重算，视窗重建——与旧 v-for key 变化语义一致）。
- `DynamicScrollerItem` 的 `size-dependencies` 取 `[item.message]`：**文本相同行高必然相同**，
  高度复用安全（折行数只由 message 决定）。
- `.log-list` 样式沿用（`flex:1; overflow-y:auto`），DynamicScroller 基于该容器滚动。
- 空态 `empty-state` 与 `LIVE` 指示器位置不变。

### 3.2 自动滚动适配

```ts
watch(() => debugStore.logs.length, async () => {
  if (!autoScroll.value) return
  await nextTick()
  scrollerRef.value?.scrollToBottom()
})
```

纪录用户手动上翻时暂停自动滚动的既有交互不变（由 autoScroll 开关控制，不新增上翻检测——YAGNI）。

## 4. 语法高亮设计（自研 tokenizer）

### 4.1 Token 类型与配色

| 类型 | 匹配规则 | 配色 |
|------|----------|------|
| `string` | `"…"` 双引号串（含转义 `\"`） | `#a31515` |
| `url` | `http(s)://…` 至空白/引号/括号止 | `#1a73e8` + 下划线 |
| `exception` | Java 包名限定的异常/错误类：`(?:[a-z][a-z0-9_$]*\.)+(?:[A-Z][A-Za-z0-9_$]*(?:Exception\|Error)\|Exception\|Error)\b`（裸 `java.lang.Exception` 亦命中） | `#c7254e` 加粗 |
| `timestamp` | `HH:MM:SS(.mmm)`（消息正文内的时刻，区别于列级时间戳） | `#8a8a8a` |
| `number` | 整数/小数/科学计数（可带负号，单边词边界） | `#098658` |
| `kw` | `at`、`Caused by`（独立词） | `#7b5bcd` 斜体 |
| `default` | 其余文本 | 继承 `.message` 原色 |

### 4.2 匹配算法（单遍、无回溯、无嵌套）

字符游标从左到右推进；在每个位置按 **string → url → exception → timestamp → number → kw**
顺序尝试各规则，首个命中者取最长匹配；无命中则游标 +1 并入 default 段。规则间天然不重叠
（先匹配者整段吞下：引号内 URL/数字归入 string，URL 内数字归入 url），产出平铺 token 流，
渲染时无嵌套 span，DOM 结构简单。

```ts
export interface HighlightToken { type: TokenType; text: string }
export type TokenType = 'string' | 'url' | 'exception' | 'timestamp' | 'number' | 'kw' | 'default'

const RULES: { type: TokenType; re: RegExp }[] = [
  { type: 'string',    re: /"(?:[^"\\]|\\.)*"/y },
  { type: 'url',       re: /https?:\/\/[^\s"'<>()]+/y },
  { type: 'exception', re: /(?:[a-z][a-z0-9_$]*\.)+(?:[A-Z][A-Za-z0-9_$]*(?:Exception|Error)|Exception|Error)\b/y },
  { type: 'timestamp', re: /\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?/y },
  { type: 'number',    re: /-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/y },
  { type: 'kw',        re: /\b(?:Caused by|at)\b/y },
]
```

（`y` 粘性标志 + `re.lastIndex = i` 逐位置尝试；长度上限 2000 字符保证最坏情形 O(n×规则数) 可控。）

### 4.3 API 与渲染管线

文件：`frontend/src/utils/logcatHighlight.ts`（新建，随目录约定）

```ts
export function tokenizeLogcatMessage(message: string): HighlightToken[]

export function highlightLogcatMessage(message: string): string
// = tokenize + 逐 token escapeHtml + <span class="hl-{type}"> 拼接（default 段仅转义不包裹）

export const MAX_TOKENIZE_LEN = 2000  // 超限整体转义原色返回
```

缓存：模块级 `Map<string, string>`（message → highlighted HTML），cap 5000，FIFO 淘汰。
`LogcatView.highlightMessage(item)` 调 `highlightLogcatMessage`（自带缓存）。
转义函数 `escapeHtml` 覆盖 `& < > " '` 五字符。

CSS：`.hl-*` 着色类定义于 `LogcatView.vue` `<style>`（与第 4.1 节配色表一致），
`scoped` 下用 `:deep()` 落在 `v-html` 内容上。

## 5. 测试策略

### 5.1 tokenizer 单测（`frontend/src/utils/logcatHighlight.test.ts`，对照 4.1 表逐型断言）

- 纯文本：tokenize 产出单个 default 段；highlight 后文本相等（无包裹 span）
- string：`"hello world"` / 含转义引号 `"a\"b"` 各一例
- url：`http://example.com:8080/path?a=1` 整段为 url token（数字不被 number 规则拆散）
- exception：`java.lang.NullPointerException`、`com.example.foo.MyCustomError`、`java.lang.Exception`
  （裸 Exception 类）命中；`myException`（无包前缀）不命中
- timestamp：`12:34:56.789` 命中；列级以外的普通数字不误判（`123:456` 不命中）
- number：`-3.14e2`；引号串内数字不拆（string 优先）
- kw：`at com.foo.Bar` 中 `at` 命中、`attach` 不命中；`Caused by: java.io.IOException` 中 kw 与 exception 各归其位
- 优先级：`"http://x"` 整体 string；`http://x/"a"` 中 url 段止于引号（`http://x/` 归 url、`"a"` 归 string）
- XSS：`<script>alert(1)</script>` → highlight 输出不含 `<script`，`<` 全为 `&lt;`
- 长度上限：2001 字符消息返回纯转义文本、不 tokenize
- 缓存：同一 message 两次调用返回同一 HTML 字符串（等价即可）

### 5.2 LogcatView 测试改造（`frontend/src/components/debug/LogcatView.test.ts`）

- stub `DynamicScroller`（渲染 `slots.default` 供断言）与 `DynamicScrollerItem`；
  既有 stub 体系（MockWebSocketService / mockApi / Element Plus）全部沿用
- 既有行为测试全量保留（挂载零请求 / 录制开关 / 过滤 / 搜索防抖 / 复制 / 导出 / 清理 / 自动滚动）
- 新增断言：
  - `scrollerItems` 含 `uid`（有 seq 用 seq；无 seq 回退 `ts-index`）
  - 日志追加且 autoScroll 开 → scroller 的 `scrollToBottom()` 被调；autoScroll 关 → 不调
  - 消息渲染为 `v-html` 高亮输出（journal 含 exception 类名时出现 `hl-exception` span）

### 5.3 门禁

- vitest 全绿；LogcatView 覆盖率 **70% 门禁保持**（`new utils` 无单独硬门禁，仍配全用例）
- eslint + `vue-tsc -b && vite build` 全绿；后端零改动（pytest/ruff/mypy 不受影响，跑一遍确认）
- 零新依赖：package.json / package-lock.json 无 diff

### 5.4 真机/浏览器验证

- 浏览器（dev 起整站）：虚拟滚动——devtools 手工注入 5 万条 mock 日志进 store，
  滚动遍历全程流畅（肉眼无卡顿、无行高错乱/截断）；高亮——构造含异常栈/URL/JSON 串的日志，
  彩色正确；XSS——注入 `<img src=x onerror=…>` 消息，页面无副作用
- 真机：录制开关开 → 高负载 logcat（设备压日志）跑 5 分钟，自动滚动贴底、LIVE 正常、
  复制/过滤/导出/清理无回归（.25 可用；.18 为 facecash 专用只读设备，仅连接级冒烟）

## 6. 风险与应对

| # | 风险 | 应对 |
|---|------|------|
| R1 | 虚拟滚动替换引发交互回归（auto-scroll/复制/过滤） | LogcatView 测试全量改造保留 + 真机冒烟清单（§5.4） |
| R2 | 变高行高度缓存错误（截断/错位——上次移除的元凶） | `size-dependencies=[message]` 保证同文本同高；浏览器 5 万条实测专项盯行高 |
| R3 | tokenizer 性能/灾难回溯 | 粘性正则逐位置线性扫描 + 2000 字符上限 + 结果缓存，无嵌套无回溯 |
| R4 | v-html XSS | D4 逐 token 转义 + 注入用例；code review 复核 escapeHtml 覆盖五字符 |
| R5 | seq 缺失致 id 冲突 | D7 uid 回退；无 seq 历史日志渲染验证 |
| R6 | scrollToItem 与缓冲高度估算不符（滚动不到底） | 真机自动滚动贴底专项验证；异常时退路为 DynamicScroller 文档化 API 换用 |
| R7 | 高亮配色与整体 UI 冲突 | 配色表（§4.1）与现有级别色系（V/D/I/W/E/F）错开色相，浏览器目验后定稿 |
| R8 | 该方案被后续功能并轨导致再移除（前车之鉴） | 独立方案、独立提交、独立验收；实施计划按 TDD 分任务落地 |

## 7. 验收标准

- ✅ 5 万条日志虚拟滚动流畅（浏览器注入实测无卡顿；行高正确无截断/错位）
- ✅ 语法高亮正确（tokenizer 单测全绿；真机异常栈/URL/引号串肉眼核验）
- ✅ XSS 安全（注入用例全绿，页面无副作用）
- ✅ 交互零回归（录制/搜索/过滤/复制/导出/清理/自动滚动全绿）
- ✅ 零新依赖（lock 文件无变化）
- ✅ 门禁全绿（vitest + LogcatView 70% 覆盖 + eslint + build；后端套件确认无影响）

## 8. 交付物

- `frontend/src/utils/logcatHighlight.ts`（tokenizer + 缓存 + 渲染管线）
- `frontend/src/utils/logcatHighlight.test.ts`（单测）
- `frontend/src/components/debug/LogcatView.vue`（DynamicScroller 改造 + message 高亮渲染 + `.hl-*` 样式）
- `frontend/src/components/debug/LogcatView.test.ts`（测试改造）
- 方案 06 文档状态/验收节订正（两 ⏳ → ✅）
- 进度追踪 + README 联动订正

## 9. 实施节奏建议

经 writing-plans 出实施计划（TDD，每任务独立提交、中文提交信息），建议任务切分：

1. **T1 tokenizer 独立落地**（§4 纯函数 + §5.1 单测）——不触组件，先行闭环渲染管线
2. **T2 DynamicScroller 改造**（§3 + §5.2 测试改造，message 暂以纯文本渲染）——虚拟滚动先行
3. **T3 高亮接入**（message 列换 `v-html` + `.hl-*` 样式 + 补充断言）——两功能合流，走查 XSS/性能护栏
4. **T4 文档与验证**（§5.4 浏览器/真机验证 + 06/进度追踪/README 订正 + 全门禁终跑）

> 实施记录：已按本方案经 writing-plans 出实施计划并经 SDD 落地（2026-09-29）。