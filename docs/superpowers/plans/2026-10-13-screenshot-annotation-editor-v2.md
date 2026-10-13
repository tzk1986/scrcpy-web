# 截图标注编辑器二次设计实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 截图标注编辑器对标业界（字号/线宽可调且显示与导出同观感、undo/redo、命中编辑、椭圆/荧光笔/序号/马赛克、剪贴板/快捷键），修复「文字看不清」根因（20px 硬编码 + 无尺寸字段）。

**Architecture:** 新增纯函数模块 `annotationModel.ts`（类型/常量/比例换算/命中/包围盒/平移/绘制）承载全部可测逻辑；`ScreenshotPreview.vue` 只做状态与事件编排（Vue ref + canvas 重绘）。字号/线宽一律以「相对图宽比例」存储、渲染时 ×canvas.width 换算——显示与导出同源，一次修根因。

**Tech Stack:** Vue 3 `<script setup>` + 原生 canvas 2D + vitest（happy-dom，fake ctx 记录式断言）。零新依赖。

**Spec:** `方案/43-截图标注编辑器二次设计方案.md`（权威；本计划逐节落实其 §1-§5）。

## Global Constraints

- 零新依赖（纯 canvas 自研）；Chrome-only；**不改变截图抓帧链路**（仅编辑器内改造）。
- master 直做；**禁 push**；中文提交 + 尾注「（方案 43 TN）」；无 Co-Authored-By。
- 测试：前端 co-located（`frontend/src/components/stream/*.test.ts`，项目既有惯例）；不复用后端 tests/ 约定。
- 门禁：`cd frontend && npx vitest run` 全绿 + `npx vue-tsc -b` 零错。后端本轮零改动，T5 跑一次全量 pytest 兜底。
- 既有行为兼容：坐标映射（client→canvas 像素比例换算）、会话归零（重开弹窗清态）、直接保存/导出 PNG 语义、`getAnnotationCount` 暴露、IME 组合态回车过滤、下载文件名规则——全部保持。
- 既有测试更新原则：行为断言保留；仅随 UI 结构变化更新结构断言（如工具栏按钮清单）。
- 数据模型铁律：新命令**必须**带比例参数（无尺寸字段即 bug）；undo/redo 双栈；落新命令清空 redo 栈。

## Review Focus（spec 未逐条言明、最可能坑到使用者的输入/环境，各任务已按此配测试）

1. **60vh 缩放下的坐标**：ghost 预览、命中检测、移动必须走既有 `toCanvasPoint` 比例映射——若直接拿 client 坐标做命中会整体偏移（T3 测）。
2. **底图未就绪时**：mosaic 无底图应静默不画（不得抛错）；（T4 测）。
3. **滚轮误触**：wheel 须 `.prevent` 且只在弹窗可见时生效，不能滚动外层容器；滑块边界钳制（T2 测）。
4. **输入框聚焦时的快捷键**：打字中 `1-8`/`Ctrl+Z` 不得劫持输入框（Esc 例外，用于退出编辑）（T2/T3 测）。
5. **超高分辨率图**（如 4000px 宽）：线宽/字号换算后的像素值合理（clamp 上下限），笔迹不窄于 1px（T1 测）。

---

### Task 1: 命令模型参数化（比例制）+ undo/redo 双栈

**Files:**
- Create: `frontend/src/components/stream/annotationModel.ts`
- Create: `frontend/src/components/stream/annotationModel.test.ts`
- Modify: `frontend/src/components/stream/ScreenshotPreview.vue`
- Modify: `frontend/src/components/stream/ScreenshotPreview.test.ts`

**Interfaces（Produces，后续任务依赖）:**
```ts
export type Tool = 'pen' | 'arrow' | 'rect' | 'ellipse' | 'highlight' | 'text' | 'number' | 'mosaic'
export interface Point { x: number; y: number }
export interface PenCmd { kind: 'pen'; points: Point[]; color: string; lineWidth: number }
export interface ArrowCmd { kind: 'arrow'; from: Point; to: Point; color: string; lineWidth: number }
export interface RectCmd { kind: 'rect'; from: Point; to: Point; color: string; lineWidth: number }
export interface EllipseCmd { kind: 'ellipse'; from: Point; to: Point; color: string; lineWidth: number }
export interface HighlightCmd { kind: 'highlight'; points: Point[]; color: string; lineWidth: number }
export interface TextCmd { kind: 'text'; at: Point; text: string; color: string; fontSize: number }
export interface NumberCmd { kind: 'number'; at: Point; index: number; color: string; fontSize: number }
export interface MosaicCmd { kind: 'mosaic'; from: Point; to: Point; lineWidth: number }
export type AnnotationCmd = PenCmd | ArrowCmd | RectCmd | EllipseCmd | HighlightCmd | TextCmd | NumberCmd | MosaicCmd
export const TOOL_KEYS: readonly Tool[]  // ['pen','arrow','rect','ellipse','highlight','text','number','mosaic']
export const FONT_RATIO_DEFAULT = 0.045; FONT_RATIO_MIN = 0.015; FONT_RATIO_MAX = 0.15
export const LINE_RATIO_DEFAULT = 0.0035; LINE_RATIO_MIN = 0.001; LINE_RATIO_MAX = 0.012
export function linePx(lineWidth: number, canvasWidth: number): number   // max(1, ratio*width)；null/NaN→1
export function fontPx(fontSize: number, canvasWidth: number): number    // max(8, ratio*width)
export function nextNumberIndex(commands: AnnotationCmd[]): number       // 现 number 命令 max(index)+1，无则 1
```
注：lineWidth = ratio（如 0.0035）；fontSize = ratio（0.045）。渲染处一律 `linePx(cmd.lineWidth, canvas.width)` 换算。`drawCmd` 本轮仍留在组件内（T3/T4 再迁出参数化扩展），但其内部线宽/字号改用换算函数。

- [ ] **Step 1: 写 annotationModel 失败测试**

`annotationModel.test.ts`（纯函数，直接 import）：
```ts
import { describe, expect, it } from 'vitest'
import { FONT_RATIO_DEFAULT, LINE_RATIO_DEFAULT, fontPx, linePx, nextNumberIndex } from './annotationModel'
import type { AnnotationCmd, NumberCmd } from './annotationModel'

describe('annotationModel 比例换算', () => {
  it('linePx/fontPx 按图宽换算', () => {
    expect(linePx(LINE_RATIO_DEFAULT, 1080)).toBeCloseTo(3.78, 2)
    expect(fontPx(FONT_RATIO_DEFAULT, 1080)).toBeCloseTo(48.6, 1)
  })
  it('极小比例钳到下限（超高分辨率不消失）', () => {
    expect(linePx(0.0001, 4000)).toBe(1)
    expect(fontPx(0.001, 4000)).toBe(8)
  })
  it('nextNumberIndex 取现有最大序号 +1（删除中间不重号）', () => {
    const cmds = [{ kind: 'number', at: { x: 0, y: 0 }, index: 3, color: '#f00', fontSize: 0.05 }] as NumberCmd[]
    expect(nextNumberIndex(cmds)).toBe(4)
    expect(nextNumberIndex([])).toBe(1)
  })
})
```
（`AnnotationCmd` 类型仅 import 保证 union 可编译。）

- [ ] **Step 2: 跑测试确认失败**：`cd frontend && npx vitest run src/components/stream/annotationModel.test.ts` → 红（模块不存在）。

- [ ] **Step 3: 实现 annotationModel.ts**

按 Interfaces 逐字实现：类型 + 常量 + `linePx`（`const px = lineWidth * canvasWidth; return Number.isFinite(px) ? Math.max(1, px) : 1`）+ `fontPx`（`Math.max(8, fontSize*canvasWidth)`，非法→8）+ `nextNumberIndex`（`commands.filter(kind==='number')` max+1）。

- [ ] **Step 4: 组件改造（比例制 + 双栈）——先改既有测试再改实现（TDD）**

`ScreenshotPreview.test.ts` 追加/更新：
1. 更新 `createRecordingCtx`：补 `ellipse`、`arc`、`fill`、`setLineDash`、`save`、`restore`、`measureText: (t: unknown) => ({ width: String(t).length * 12 })`、属性 setter `globalAlpha`、`imageSmoothingEnabled`、`textAlign`、`textBaseline`（为 T2-T4 预埋，本轮不全部用上）。
2. 新增测试（新 describe「ScreenshotPreview 参数化与双栈」）：
```ts
it('文字命令按比例存字号（默认 0.045），渲染换算为像素', async () => {
  const wrapper = await mountAndSettle()          // 画布 800x600
  mockCanvasRect(wrapper)
  await wrapper.find('.tool-text').trigger('click')
  await wrapper.find('input.text-draft').setValue('设备异常')
  await wrapper.find('.text-confirm').trigger('click')
  fakeCtx.calls.length = 0
  await wrapper.find('canvas').trigger('pointerdown', { clientX: 50, clientY: 30 })
  await nextTick()
  const cmds = (wrapper.vm as any).getCommands() as Array<{ fontSize: number }>
  expect(cmds[0].fontSize).toBe(0.045)
  // 800*0.045 = 36 → font 设置过 '36px sans-serif'（fake 若记录 font 值可断言之；至少 fillText 已发生）
  expect(fakeCtx.calls).toContain('fillText')
})
it('画笔命令携带 lineWidth 比例（默认 0.0035）', async () => { /* drawShape → getCommands()[0].lineWidth === 0.0035 */ })
it('undo 后 redo 恢复命令并重绘', async () => {
  // drawShape → btn-undo → btn-redo：commandCount 回 1；redo 后 getRedoCount() === 0
})
it('落新命令清空 redo 栈（redo 不可用）', async () => {
  // drawShape → undo → drawShape → getRedoCount() === 0
})
```
3. `defineExpose` 增 `getCommands: () => commands.value`、`getRedoCount: () => redoStack.value.length`（测试断言用，沿用 getAnnotationCount 先例）。
4. 实现：
   - 删组件内类型定义（Point/PenCmd/…/AnnotationCmd 从 model import）；Tool 从 model import（含全部 8 值，按钮 T2/T4 再加）。
   - `commands` + `redoStack = ref<AnnotationCmd[]>([])`；`pushCmd(cmd)`（push + `redoStack.value = []`）；`undo()`（pop → redoStack.push → redraw）；`redo()`（pop redoStack → commands.push → redraw）；`clearAll()` 同时清双栈。
   - 所有 push 点（指针提交、文字落字）改走 `pushCmd`；落字与提交时携带 `lineWidth: LINE_RATIO_DEFAULT` / `fontSize: FONT_RATIO_DEFAULT`。
   - `drawCmd`：`ctx.lineWidth = linePx(cmd.lineWidth, ctx.canvas.width)`（text 分支 `ctx.font = \`${fontPx(cmd.fontSize, ctx.canvas.width)}px sans-serif\``）；箭头 `headLength = linePx * 3.5`（默认 4px→14px 与原一致）。
   - 红绿循环中若 `ctx.canvas` 在 fake 下缺失：fake ctx 补 `canvas: { width: 800, height: 600 }` 或 drawCmd 改收 `canvasWidth` 显式参数（推荐后者：`drawCmd(ctx, cmd, canvasWidth)`，调用处传 `canvas.width`；测试 tip 更稳）。

- [ ] **Step 5: 全绿验证**：`cd frontend && npx vitest run src/components/stream/annotationModel.test.ts src/components/stream/ScreenshotPreview.test.ts` → 全绿（既有 22 测 + 新 ~7 测）；`npx vue-tsc -b` 零错。

- [ ] **Step 6: 提交**
```bash
git add frontend/src/components/stream/annotationModel.ts frontend/src/components/stream/annotationModel.test.ts frontend/src/components/stream/ScreenshotPreview.vue frontend/src/components/stream/ScreenshotPreview.test.ts
git commit -m "feat: 标注命令参数化（字号/线宽比例制）+ undo/redo 双栈（方案 43 T1）"
```

---

### Task 2: 工具栏分组重构 + 参数组（滑块/滚轮）+ 快捷键

**Files:**
- Modify: `frontend/src/components/stream/ScreenshotPreview.vue`
- Modify: `frontend/src/components/stream/ScreenshotPreview.test.ts`

**Interfaces:**
- Consumes: T1 常量与 pushCmd/undo/redo。
- Produces: `fontRatio = ref(FONT_RATIO_DEFAULT)`、`lineRatio = ref(LINE_RATIO_DEFAULT)`（新标注默认值）；`.btn-redo` 按钮；`.font-size-slider` / `.line-width-slider`（原生 range）；`onWheel` / `onKeydown`。

- [ ] **Step 1: 写真测试（追加 describe「ScreenshotPreview 参数组与快捷键」）**

```ts
it('重做按钮：无 redo 时置灰，撤销后可点且恢复命令', async () => {
  // drawShape → .btn-redo disabled；btn-undo → 可点 → click → commandCount 1
})
it('滚轮调字号：文字工具下 wheel up 增大 fontSize 比例（钳制上限）', async () => {
  // 选文字工具 → canvas.trigger('wheel', { deltaY: -100 }) → getDefaultFontRatio() > 0.045
  // 连续 50 次 -100 → ≤ 0.15
})
it('滚轮调线宽：画笔工具下 wheel down 减小 lineWidth 比例（钳制下限 0.001）', async () => {})
it('滑块输入更新默认参数并用于新命令', async () => {
  // find('.font-size-slider').setValue('0.1')（trigger input）→ 落一段文字 → cmd.fontSize === 0.1
})
it('字号滑块仅文字/序号工具显示，线宽滑块仅画笔/箭头/矩形（含后续新工具）显示', async () => {
  // pen: .line-width-slider 存在、.font-size-slider 不存在；text: 反之
})
it('数字键 1-4 切工具（画笔/箭头/矩形/文字），输入框聚焦时不劫持', async () => {})
it('Ctrl+Z 撤销 / Ctrl+Shift+Z 重做（window keydown）', async () => {
  // window.dispatchEvent(new KeyboardEvent('keydown', { key: 'z', ctrlKey: true }))
})
```
（为断默认参数，`defineExpose` 增 `getDefaultFontRatio: () => fontRatio.value`、`getDefaultLineRatio: () => lineRatio.value`。）

- [ ] **Step 2: 跑测试确认失败。**

- [ ] **Step 3: 实现**
   - 模板：工具栏改分组——[工具组 4 钮（保留类 .tool-pen/.tool-arrow/.tool-rect/.tool-text 与文案）] `[sep]` [操作组：`.btn-undo` `.btn-redo`(disabled=redoStack 空) `.btn-clear` `[sep] .btn-copy`(占位语义 T5 实装，本轮先不加) `.btn-save` `.btn-export`]；参数行独立一行 `.param-row`：色板（`.color-swatch`×6 预设：#ff3b30 红 / #ff9500 橙 / #ffcc00 黄 / #34c759 绿 / #007aff 蓝 / #000000 黑，点击 `strokeColor = 色值`，当前色高亮 `swatch-active`）+ 既有 `.color-input` 自定义色 + `.line-width-slider`（v-if lineTools 含 pen/arrow/rect，T4 扩 ellipse/highlight/mosaic）+ `.font-size-slider`（v-if text/number，本轮 text）+ 各自右侧 `{{ Math.round(ratio*canvasWidth) }}px` 标签（canvasWidth 用 `canvasWidth = ref(0)`，底图就绪 watch 里赋值）。
   - 原生 range：`:min="LINE_RATIO_MIN" :max="LINE_RATIO_MAX" :step="0.0005" :value="lineRatio"` `@input="onLineSlider"`（`lineRatio.value = Number((e.target as HTMLInputElement).value)`）；字号步长 0.001。
   - `onWheel(e: WheelEvent)`：`e.preventDefault()`（模板 `@wheel.prevent`）；`factor = e.deltaY < 0 ? 1.1 : 1/1.1`；text → `fontRatio = clamp(fontRatio*factor, MIN, MAX)`；线宽类 → 同理；`redraw()`（ghost 预览尺寸随动）。
   - `onKeydown(e: KeyboardEvent)`：`if (!props.visible) return`；target 为 INPUT/TEXTAREA → 仅放行 Escape（T3 用于退出编辑），其余 return；`Ctrl/Cmd+Z`（shift → redo）；`Ctrl/Cmd+S` → exportPng；`Ctrl/Cmd+C` → T5 的 copyToClipboard（本轮若未实现则 no-op 占位注释，不拦截默认）；`Delete/Backspace` → T3；数字键 `1-4` → `TOOL_KEYS[idx-1]`（T4 扩到 8 自动生效，按 TOOL_KEYS 表长度）。`watch(props.visible)` 时 window addEventListener / removeEventListener；`onUnmounted` 兜底移除。
   - 滑块与选中命令联动本轮不做（T3 编辑场景再引入绑定），保持简单。

- [ ] **Step 4: 全绿 + 全量前端**：目标文件绿后跑 `npx vitest run`（全量回归）+ `npx vue-tsc -b`。

- [ ] **Step 5: 提交**
```bash
git commit -m "feat: 工具栏分组重构 + 参数组（滑块/滚轮调参）+ 快捷键（方案 43 T2）"
```

---

### Task 3: 文字 ghost 预览 + 命中检测/选中/移动/删除/双击编辑

**Files:**
- Modify: `frontend/src/components/stream/annotationModel.ts`（+ hitTest/cmdBBox/translateCmd）
- Modify: `frontend/src/components/stream/annotationModel.test.ts`
- Modify: `frontend/src/components/stream/ScreenshotPreview.vue`
- Modify: `frontend/src/components/stream/ScreenshotPreview.test.ts`

**Interfaces:**
- Produces:
```ts
export interface BBox { x: number; y: number; width: number; height: number }
export function hitTest(commands: AnnotationCmd[], point: Point, canvasWidth: number, measure: (text: string, px: number) => number): number | null
// 自顶向下（数组末尾优先）返回首个命中索引；未命中 null
export function cmdBBox(cmd: AnnotationCmd, canvasWidth: number, measure: (text: string, px: number) => number): BBox
export function translateCmd(cmd: AnnotationCmd, dx: number, dy: number): void  // 原地平移
```
- 组件新增暴露：`getSelectedIndex: () => selectedIndex.value`。

**命中规则（近似命中，写入代码注释）：** 笔划类（pen/highlight）：点到折线各段距离 ≤ `max(8, linePx*2)`；arrow：点到线段距离 ≤ 同阈值；rect/ellipse：点到边框距离 ≤ 阈值（**内部不算**——保证新图形可画在旧图形内）；mosaic：点在包围盒内；text/number：包围盒内（text 宽度用 `measure(text, fontPx)`，盒 y 从 `at.y - fontPx*1.0` 到 `at.y + fontPx*0.25`；number 用圆 `dist ≤ radius+阈值`）。

- [ ] **Step 1: model 层失败测试**

```ts
describe('hitTest / cmdBBox / translateCmd', () => {
  it('矩形边框命中、内部不命中', () => {
    const rect: RectCmd = { kind: 'rect', from: { x: 100, y: 100 }, to: { x: 300, y: 200 }, color: '#f00', lineWidth: 0.0035 }
    expect(hitTest([rect], { x: 200, y: 100 }, 800, () => 0)).toBe(0)   // 上边框上
    expect(hitTest([rect], { x: 200, y: 150 }, 800, () => 0)).toBeNull() // 内部
  })
  it('文字包围盒命中（宽度经 measure）', () => {
    const text: TextCmd = { kind: 'text', at: { x: 100, y: 200 }, text: '告警', color: '#f00', fontSize: 0.05 }
    // 800*0.05=40px；measure 返回 80 → 盒 x∈[100,180], y∈[160,210]
    expect(hitTest([text], { x: 140, y: 190 }, 800, () => 80)).toBe(0)
    expect(hitTest([text], { x: 300, y: 190 }, 800, () => 80)).toBeNull()
  })
  it('自顶向下：重叠时返回最上层（数组末尾）', () => { /* 两条重合 rect，索引 1 命中 */ })
  it('translateCmd 平移各类型', () => {
    // pen 全部点 +5,-3；rect from/to 同变；text at 变；mosaic from/to 变
  })
})
```

- [ ] **Step 2: 跑红 → 实现 model 三函数 → 绿。**

- [ ] **Step 3: 组件行为失败测试（追加 describe「ScreenshotPreview 选中与编辑」）**

```ts
it('文字 ghost：hove 时半透明预览，点击才落字（命令数不变）', async () => {
  // 选文字+确认文案 → canvas.trigger('pointermove', {clientX:50,clientY:30}) → fillText 出现但 commandCount 0；
  // pointerleave → 再 move 无 fillText
})
it('命中选中：点击已画矩形边框 → getSelectedIndex()===0；（重绘含 setLineDash 选中框）', async () => {})
it('拖拽移动：选中后 pointerdown+move+up 平移命令坐标', async () => {
  // drawShape rect → 点边框选中 → pointerdown(边框)→move(+40,+20 client=+20,+10 像素)→up
  // getCommands()[0].from ≈ {x:120,y:110}（换算后断言）
})
it('Delete 删除选中命令；Esc 取消选中', async () => {})
it('双击文字进入编辑：确认后原地改文案（命令数不变、fontSize 保留）', async () => {
  // 落字 → dblclick 在文字盒内 → 输入框预填 → 改名+确认 → getCommands()[0].text 更新
})
it('笔划类命中用点距（画笔线中点附近命中，远离不命中）', async () => {})
```
实现要点：
- `hoverPoint = ref<Point | null>(null)`；pointermove 无 drag/move 时更新 + redraw；pointerleave 清空。`redraw()` 末尾：text 工具 + confirmedText + hoverPoint → `ctx.globalAlpha = 0.5` 画 ghost（`ctx.save()/restore()` 包裹），随后按需画选中框。
- `selectedIndex = ref<number | null>(null)`；`redraw()` 画选中框：`ctx.setLineDash([6,4])` 虚线蓝框（`#409eff`，框 = `cmdBBox(cmd)` 外扩 4px，`ctx.strokeRect`），画完 `ctx.setLineDash([])`。
- `onPointerDown` 顺序：hitTest → 命中则 `selectedIndex=hit`、进入 `moveState = { index, last: point }`；未命中 → `selectedIndex=null`，走原工具逻辑（text/number 落字前照旧确认检查）。
- `onPointerMove`：`moveState` → `translateCmd(cmd, dx, dy)` + `last=point` + redraw。
- `onPointerUp`：`moveState=null`（移动已实时应用，无需提交）。
- Delete/Backspace（keydown 已挂）：`deleteSelected()` → `commands.splice(idx,1)`、`redoStack=[]`、`selectedIndex=null`、redraw。
- Escape：编辑中 → 退出编辑（清 editingIndex）；否则 drag 取消 / 清 selectedIndex；redraw。
- 双击：`@dblclick` → hitTest 且 kind==='text' → `editingIndex=hit`、`tool='text'`、`selectedIndex=hit`、`textDraft=cmd.text`、`confirmedText=cmd.text`；`confirmText()` 若 `editingIndex!=null` → 更新该 cmd.text（无新增）并 `editingIndex=null`；否则原行为。
- 会话归零 watch 增清：redoStack/selectedIndex/editingIndex/hoverPoint/fontRatio/lineRatio 归默认（**评审注意**：字体/线宽默认值重开是否归位——归位，与「全新会话」语义一致）。

- [ ] **Step 4: 全绿（目标文件 + `npx vue-tsc -b`）。**

- [ ] **Step 5: 提交**
```bash
git commit -m "feat: 文字 ghost 预览 + 命中选中/移动/删除/双击编辑（方案 43 T3）"
```

---

### Task 4: 新工具——椭圆 / 荧光笔 / 序号气泡 / 马赛克

**Files:**
- Modify: `frontend/src/components/stream/annotationModel.ts`（drawCmd 迁出 + 全类型渲染）
- Modify: `frontend/src/components/stream/annotationModel.test.ts`
- Modify: `frontend/src/components/stream/ScreenshotPreview.vue`
- Modify: `frontend/src/components/stream/ScreenshotPreview.test.ts`

**Interfaces:**
- Produces: `export function drawCmd(ctx: CanvasRenderingContext2D, cmd: AnnotationCmd, canvasWidth: number, baseImage: HTMLImageElement | null): void`（自组件迁入 model，组件 redraw 调用；组件内私有 drawArrowHead 一并迁入）。
- 组件：`drawTool` 扩展 `'ellipse' | 'highlight' | 'mosaic'`；`DragState.tool: 'pen'|'arrow'|'rect'|'ellipse'|'highlight'|'mosaic'`。

- [ ] **Step 1: model 失败测试（渲染路径）**

```ts
it('ellipse 渲染调用 ctx.ellipse（rx/ry 中心式）', () => {
  const ctx = createRecordingCtx()  // 需本地重建 fake（复制组件测试的 createRecordingCtx，含 ellipse/arc/fill 等）
  drawCmd(ctx as any, { kind: 'ellipse', from: { x: 0, y: 0 }, to: { x: 200, y: 100 }, color: '#f00', lineWidth: 0.0035 }, 800, null)
  expect(ctx.calls).toContain('ellipse'); expect(ctx.calls).toContain('stroke')
})
it('highlight 半透明（globalAlpha 0.35 且 restore）', () => { /* calls 含 save、restore；alpha 记录 */ })
it('number 画实心圆 + 白色序号（fill + fillText(index)）', () => {})
it('mosaic 无底图静默返回；有底图时经离屏 canvas 缩-放（drawImage ≥ 2 次）', () => {
  // baseImage 传 {} as HTMLImageElement（drawImage 只记录调用）
  // document.createElement('canvas') 在 happy-dom 可用；断言 ctx.calls 中 drawImage 计数 ≥2 且 imageSmoothingEnabled 被设置
})
it('mosaic 块尺寸随 lineWidth（块大→离屏尺寸小）', () => {})
```

- [ ] **Step 2: 跑红 → 实现**
   - model：迁入 drawCmd + drawArrowHead；switch 全 8 分支：
     - ellipse：`ctx.ellipse(cx, cy, rx, ry, 0, 0, Math.PI*2)` + stroke（cx/cy=from/to 中点，rx/ry=半宽高）。
     - highlight：同 pen 路径，`ctx.save()` → `ctx.globalAlpha = 0.35` → stroke（lineCap round）→ `ctx.restore()`。
     - number：`radius = fontPx*0.75`；fillStyle=color → `arc(at, radius)` + fill；fillStyle='#fff'、textAlign='center'、textBaseline='middle'、`font = 'bold ' + fontPx*0.9 + 'px sans-serif'` → fillText(String(index), at)。
     - mosaic：`x=min(from.x,to.x)` 等；`block = Math.max(4, linePx(lineWidth, w))`；`sw = Math.max(1, Math.round(bw/block))`；离屏 `document.createElement('canvas')` → `octx.drawImage(base, x, y, bw, bh, 0, 0, sw, sh)` → `ctx.imageSmoothingEnabled = false` → `ctx.drawImage(off, x, y, bw, bh)` → 恢复 `true`；`if (!baseImage || bw < 1 || bh < 1) return`。
   - 组件：工具栏加 4 按钮（类 `.tool-ellipse` `.tool-highlight` `.tool-number` `.tool-mosaic`，文案 椭圆/荧光笔/序号/马赛克，顺序按 TOOL_KEYS：画笔 箭头 矩形 椭圆 荧光笔 文字 序号 马赛克）；`onPointerDown`：number → `pushCmd({kind:'number', at, index: nextNumberIndex(commands), color, fontSize: fontRatio})`；拖拽类扩 ellipse/mosaic（mosaic 无 color）；`onPointerUp` 提交对应命令（ellipse 同 rect 最小位移守卫；mosaic 用 `Math.max(8, ...)` 面积守卫？——两轴各 ≥2px 即可，与原 rect 同口径）；`redraw()` 拖拽预览：ellipse → 椭圆轮廓预览；mosaic → 虚线矩形轮廓（`setLineDash([4,4])`，提交后显示真实马赛克）；highlight → 同 pen 路径。线宽滑块显示条件扩全 stroke 类；**mosaic 无颜色参数（色板隐藏）**。
   - 命中/包围盒/平移（T3 已通用实现，此处补 mosaic bbox=from/to、ellipse 边框距离）——若 T3 实现已覆盖全 kind，则此任务只补测试。

- [ ] **Step 3: 全绿 + 全量 `npx vitest run` + `npx vue-tsc -b`。**

- [ ] **Step 4: 提交**
```bash
git commit -m "feat: 新工具椭圆/荧光笔/序号气泡/马赛克（方案 43 T4）"
```

---

### Task 5: 剪贴板复制 + 全量门禁 + 文档回写

**Files:**
- Modify: `frontend/src/components/stream/ScreenshotPreview.vue`（`.btn-copy` + copyToClipboard + Ctrl+C 接线）
- Modify: `frontend/src/components/stream/ScreenshotPreview.test.ts`
- Modify: `docs/用户手册.md`、`方案/43-截图标注编辑器二次设计方案.md`、`方案/README.md`、`方案/进度追踪.md`、`方案/后续事项评估与路线图.md`

- [ ] **Step 1: 剪贴板测试（红）**

```ts
it('复制到剪贴板：write([ClipboardItem(image/png)]) 被调用', async () => {
  const write = vi.fn().mockResolvedValue(undefined)
  vi.stubGlobal('navigator', { ...navigator, clipboard: { write } })
  vi.stubGlobal('ClipboardItem', class { constructor(public items: unknown) {} })
  // click .btn-copy → toBlobMock 调用；write 收到长度 1 的数组，元素为 ClipboardItem 实例
})
it('Ctrl+C 触发复制；clipboard 缺失时静默不抛', async () => {})
```
- [ ] **Step 2: 实现**：`async function copyToClipboard()`——`if (!navigator.clipboard?.write) return`；`canvas.toBlob(async (blob) => { if (!blob) return; try { await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })]) } catch { /* 剪贴板权限/焦点问题静默 */ } }, 'image/png')`。按钮 `.btn-copy` 文案「复制」放操作组（撤销 重做 | 清空 | 复制 直接保存 导出 PNG）。Ctrl+C 接线（T2 已留钩子）。
- [ ] **Step 3: 全量门禁**（逐字）：
```bash
cd frontend && npx vitest run && npx vue-tsc -b
cd .. && python -m ruff check backend/app && python -m mypy backend/app
PYTHONPATH=backend:. python -m pytest tests/ --ignore=tests/e2e -q --cov=app --cov-fail-under=80
```
（后端零改动，pytest 为兜底回归；任何失败修复后再继续。）
- [ ] **Step 4: 文档回写**（就地更新；先 Read 定位）：
   1. `docs/用户手册.md` 截图标注相关小节：补「工具与参数」要点——8 工具、滚轮/滑块调字号线宽（或选中已放置标注直接调）、撤销/重做、点击已放置标注可拖动/Delete 删除、双击文字可二次编辑、复制到剪贴板、快捷键表（1-8 切工具 / Ctrl+Z / Ctrl+Shift+Z / Ctrl+C / Ctrl+S / Esc）。
   2. `方案/43-截图标注编辑器二次设计方案.md` 状态行改「已实施（2026-10-13，master 提交 <区间>；门禁全绿 <数字>；Chrome 手动验收待用户执行）」+ 文末「## 六、实施记录」：任务→提交表、门禁数字、测试计数、Ruling、遗留（Chrome 交互验收归用户真机包验证）。
   3. `方案/README.md` 方案 43 条目 → 已实施；`方案/进度追踪.md` 追加第七十九次更新；`方案/后续事项评估与路线图.md` 第 12 项 → 已实施（更新记录追加同日五更）。
- [ ] **Step 5: 提交**
```bash
git add frontend/src/components/stream/ScreenshotPreview.vue frontend/src/components/stream/ScreenshotPreview.test.ts docs/用户手册.md 方案/43-截图标注编辑器二次设计方案.md 方案/README.md 方案/进度追踪.md 方案/后续事项评估与路线图.md
git commit -m "feat: 复制到剪贴板 + 全量门禁 + 文档回写（方案 43 T5）"
```

---

## 执行方式（本批约定，用户指示）

- master 直做；每任务 TDD 红→绿→提交；**不设逐任务评审**（用户指示「减少无谓评审，只在关键验收节点评审」）。
- 关键验收节点：T5 全量门禁全绿 + 打包 exe 后由用户明日 Chrome 真机验证（交互验收归用户）。
- 禁 push（逐批显式授权）。
- 打包：完成后按方案 15 流程出 Windows exe 包（另行执行）。