# Shell 输入框模式方案

> **版本**：v1.0
> **日期**：2026-09-10
> **状态**：待评审

---

## 1. 问题背景

### 1.1 当前问题

**问题 A：回车两次才生效**

- 当前架构：xterm.js `onData` → WebSocket → 后端 `send_input()` → 设备 PTY
- 设备 PTY 可能未设置 `ICRNL` 标志（`\r` → `\n` 转换）
- `\r` 被 PTY 当作普通字符，shell 的 `readline()` 等不到 `\n` 行结束符
- 用户按回车无反应，需再次按回车才能执行

**问题 B：复制功能缺失**

- xterm.js 支持原生文本选择和复制，但需要验证
- 用户需要能选中终端输出内容并复制

**问题 C：Tab 补全未验证**

- xterm.js 的 Tab 键应透传到设备 shell
- 设备 shell 的 readline 处理 Tab 补全
- 需要验证实际效果

---

## 2. 方案概述

### 2.1 核心思路

采用 **混合模式**：保留 xterm.js 作为完整的 PTY 终端（支持复制、Tab 补全），在底部添加命令输入框作为快捷输入方式。

```
┌──────────────────────────────────────┐
│  xterm.js 终端（只读 + 选择复制）     │
│  ┌────────────────────────────────┐  │
│  │ rk3288:/ $ ls                   │  │  ← 设备回显（Tab 补全在这里显示）
│  │ Android  data  sdcard  ...      │  │
│  │ rk3288:/ $                      │  │
│  ────────────────────────────────┘  │
├──────────────────────────────────────┤
│  [ls -la ____________________] [发送] │
│  ↑ 回车发送  ↑↓历史  Tab 补全（可选）  │
└──────────────────────────────────────┘
```

### 2.2 为什么不用纯输入框方案

| 需求 | 纯输入框方案 | 混合模式 |
|------|-------------|---------|
| 回车问题 | ✅ 发 `\n` 解决 | ✅ 输入框发 `\n` 解决 |
| 复制功能 | ❌ 需额外实现 | ✅ xterm.js 原生支持 |
| Tab 补全 |  需后端查询 | ✅ 设备 shell 原生处理 |
| 交互式命令 | ❌ 无法支持 top/vi | ✅ 终端可直接交互 |
| 实现复杂度 | 中 | 低（改动小） |

---

## 3. 详细设计

### 3.1 架构

```
输入框模式（快捷命令）：
  输入框 Enter → 前端发 "命令\n" → WebSocket → send_input(b"命令\n") → 设备
                                                                              ↓
终端模式（Tab 补全 / 交互式）：
  xterm.js Tab → 前端发 "\t" → WebSocket → send_input(b"\t") → 设备 shell readline 处理
  设备补全结果 → stdout → _output_queue → WebSocket → xterm.js 渲染
```

### 3.2 回车问题修复

**方案**：在 `sendInput` 中将 `\r` 替换为 `\n`

```typescript
// frontend/src/stores/debug.ts
function sendInput(data: string) {
  if (data === '\r') {
    data = '\n'  // 确保 shell 识别行结束
  }
  const encoded = btoa(unescape(encodeURIComponent(data)))
  debugWs.send({ op: 'input', data: encoded })
}
```

**影响分析**：
- 对 `\r` → `\n` 的替换：shell 收到 `\n`，`readline()` 立即返回，新 prompt 显示
- 对 Tab、Ctrl+C 等其他按键：不受影响，原样透传
- 风险：设备如果设置了 `ONLCR`（输出时 `\n` → `\r\n`），可能多一个空行，但不影响功能

### 3.3 复制功能验证

**xterm.js 原生支持**：
- 鼠标选择文本后，`Ctrl+C` 或右键菜单可复制
- 无需额外代码，浏览器原生行为
- **验证方法**：在终端中选中文字，按 `Ctrl+C`，粘贴到记事本

**如果需要增强**：
- 添加"复制全部"按钮
- 添加"复制选中"按钮
- 使用 `term.getSelection()` API 获取选中内容

### 3.4 Tab 补全验证

**原理**：
1. 用户按 Tab → xterm.js `onData('\t')` → 前端 `sendInput('\t')`
2. 后端 `send_input(b'\t')` → 设备 PTY
3. 设备 shell 的 `readline()` 处理 Tab，列出补全选项
4. 补全结果通过 stdout → 后端 `_output_queue` → WebSocket → xterm.js 渲染

**验证方法**：
1. 输入 `cd /sd` + Tab → 应补全为 `cd /sdcard/`
2. 输入 `ls /` + Tab → 应列出根目录文件

**如果 Tab 不工作**：
- 可能是 xterm.js 拦截了 Tab（用于焦点切换）
- 需要在 xterm.js 配置中禁用 Tab 的默认行为：
  ```typescript
  term.onKey((e) => {
    if (e.key === '\t') {
      e.domEvent.preventDefault()
      debugStore.sendInput('\t')
    }
  })
  ```

### 3.5 输入框设计

#### UI 布局

```html
<div class="shell-container">
  <!-- 终端区域 -->
  <div ref="terminalRef" class="terminal"></div>

  <!-- 输入框区域 -->
  <div class="input-bar">
    <input
      v-model="command"
      @keydown.enter="sendCommand"
      @keydown.up="historyUp"
      @keydown.down="historyDown"
      placeholder="输入命令..."
      class="command-input"
    />
    <button @click="sendCommand">发送</button>
  </div>
</div>
```

#### 命令历史

```typescript
// 前端维护命令历史（50 条）
const history = ref<string[]>([])
const historyIndex = ref(-1)
const command = ref('')

function sendCommand() {
  const cmd = command.value.trim()
  if (!cmd) return

  // 添加到历史
  history.value.push(cmd)
  if (history.value.length > 50) {
    history.value.shift()
  }
  historyIndex.value = -1

  // 发送命令（追加 \n）
  debugStore.sendInput(cmd + '\n')
  command.value = ''
}

function historyUp() {
  if (historyIndex.value < history.value.length - 1) {
    historyIndex.value++
    command.value = history.value[history.value.length - 1 - historyIndex.value]
  }
}

function historyDown() {
  if (historyIndex.value > 0) {
    historyIndex.value--
    command.value = history.value[history.value.length - 1 - historyIndex.value]
  } else {
    historyIndex.value = -1
    command.value = ''
  }
}
```

#### 输入框的 Tab 处理

**选项 A**：输入框中按 Tab 不补全（直接发送 Tab 到终端）
- 简单，但用户体验一般

**选项 B**：输入框中按 Tab 触发前端补全
- 需要后端提供补全 API
- 实现复杂，暂不推荐

**推荐**：选项 A，Tab 补全在终端中完成

---

## 4. 实施步骤

### Phase 1：修复回车问题（10 分钟）

**文件**：`frontend/src/stores/debug.ts`

```diff
  function sendInput(data: string) {
+   if (data === '\r') {
+     data = '\n'
+   }
    const encoded = btoa(unescape(encodeURIComponent(data)))
    debugWs.send({ op: 'input', data: encoded })
  }
```

**验证**：
- [ ] 按一次回车，命令立即执行

### Phase 2：验证 Tab 补全（5 分钟）

**操作**：
1. 在终端输入 `cd /sd` + Tab
2. 观察是否补全为 `cd /sdcard/`

**如果失败**，添加 Tab 键拦截：

```typescript
// frontend/src/components/debug/ShellView.vue
term.onKey((e) => {
  if (e.key === '\t') {
    e.domEvent.preventDefault()
    debugStore.sendInput('\t')
  }
})
```

### Phase 3：验证复制功能（5 分钟）

**操作**：
1. 在终端中用鼠标选中文字
2. 按 `Ctrl+C` 或右键"复制"
3. 粘贴到记事本验证

**如果需要增强**：
- 添加"复制选中"按钮
- 使用 `term.getSelection()` API

### Phase 4：添加输入框（30 分钟）

**文件**：`frontend/src/components/debug/ShellView.vue`

1. 添加输入框 UI
2. 实现 `sendCommand`、`historyUp`、`historyDown`
3. 样式调整

### Phase 5：完整验证（10 分钟）

- [ ] 回车一次执行命令
- [ ] Tab 补全文件名
- [ ] 复制终端内容
- [ ] 输入框发送命令
- [ ] ↑↓ 切换历史
- [ ] Ctrl+C 中断命令
- [ ] `cd` 持久化
- [ ] `su` 提权

---

## 5. 风险评估

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|---------|
| `\r` → `\n` 导致多换行 | 中 | 低 | 可接受，不影响功能 |
| Tab 被浏览器拦截 | 中 | 中 | 添加 `preventDefault` |
| 输入框与终端焦点冲突 | 低 | 低 | 终端点击后聚焦输入框 |
| 命令历史丢失 | 低 | 低 | 刷新后重建，可持久化到 localStorage |

---

## 6. 验收标准

- [ ] 按一次回车，命令立即执行
- [ ] 终端内容可用鼠标选择并复制
- [ ] 按 Tab 可补全文件名（如 `cd /sd` + Tab → `cd /sdcard/`）
- [ ] 输入框可发送命令到 shell
- [ ] 输入框 ↑↓ 可切换历史命令
- [ ] 终端输出实时显示在 xterm.js 中
- [ ] 所有现有功能（cd 持久化、su 提权、Ctrl+C）正常工作

---

## 7. 后续优化（可选）

1. **命令历史持久化**：存到 localStorage，刷新后保留
2. **输入框 Tab 补全**：后端提供补全 API，前端显示候选列表
3. **终端输出过滤**：过滤 prompt 行，只显示命令输出
4. **多命令支持**：输入框支持 `;`、`&&` 等命令分隔符

---

## 附录：方案对比

### 方案 A：纯输入框模式（用户原始方案）

```
┌──────────────────────────────────────┐
│  终端输出区（只读，显示结果）          │
│  ┌────────────────────────────────┐  │
│  │ $ ls                             │  │
│  │ Android  data  sdcard            │  │
│  │ $                                │  │
│  ────────────────────────────────┘  │
├──────────────────────────────────────┤
│  [ls -la ____________________] [发送] │
└──────────────────────────────────────┘
```

- ✅ 回车问题解决
- ❌ Tab 补全需额外实现
-  交互式命令（top/vi）无法使用
- ⚠️ 复制功能需额外实现

### 方案 B：混合模式（推荐）

```
┌──────────────────────────────────────┐
│  xterm.js 终端（完整 PTY）            │
│  ┌────────────────────────────────┐  │
│  │ $ cd /sd<Tab>                    │  │  ← Tab 补全在这里
│  │ $ cd /sdcard/                    │  │
│  │ $ ls                             │  │
│  │ Android  data  sdcard            │  │
│  │ $                                │  │
│  └────────────────────────────────┘  │
├──────────────────────────────────────┤
│  [ls -la ____________________] [发送] │  ← 快捷输入
└──────────────────────────────────────┘
```

- ✅ 回车问题解决（输入框发 `\n`）
- ✅ Tab 补全（终端原生支持）
- ✅ 交互式命令（终端直接交互）
- ✅ 复制功能（xterm.js 原生支持）
- ✅ 改动量小

**结论**：推荐方案 B（混合模式）。
