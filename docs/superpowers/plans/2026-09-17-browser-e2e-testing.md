# 浏览器端到端测试（视频解码 + 输入控制）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立可重复运行的 Playwright E2E 基础设施，覆盖 H.264 视频解码显示、输入控制（点击/滑动/按键）、坐标映射（含横竖屏）、截图回退四类场景，产出明确的通过/失败结论，作为后续所有体验优化项的判断基线。

**Architecture:** 仓库根新增轻量 Node runner（仅 `@playwright/test` 一个 devDep，解决 `tests/e2e/` 下 spec 文件的模块解析问题）；Playwright `webServer` 自动拉起后端（`python run_server.py`，8765）与前端 dev server（8080，已代理 /api 与 /ws）；spec 通过 device fixture 自动探测在线 ADB 设备，无设备时全部优雅 skip；设备侧断言统一走「创建 debug session → HTTP shell 执行 dumpsys」链路，不引入额外 adb 依赖。

**Tech Stack:** Playwright（chromium）、TypeScript、Vue3 dev server（vite 8080）、FastAPI（8765）

**Spec:** 无独立 spec 文档；需求来源 `方案/进度追踪.md`「当前阶段优先级」第 1 项；行为契约参考 `方案/04-前端视频播放器.md`、`方案/13-scrcpy二进制控制协议重构.md`、`方案/14-调试面板其他标签完善.md`、`docs/经验记录.md` E011-E015。

## Global Constraints

- 浏览器仅支持 Chrome（最新两个稳定版）→ Playwright 只配 chromium project，禁止 firefox/webkit
- 所有测试文件必须位于 `tests/` 下 → specs 放 `tests/e2e/specs/`，配置放 `tests/e2e/`（项目 CLAUDE.md 硬约束）
- 后端端口固定 8765（`run_server.py` 硬编码，端口可配置化是后续独立事项）；前端 dev server 8080（`strictPort: true`）
- E2E 对设备有真实依赖：所有设备相关 spec 必须先探测设备，无在线设备时 skip 而非 fail
- 单设备场景，scrcpy-server 每设备仅一个实例 → `workers: 1` 严格串行
- 视频流启动约需 3s；H264 失败回退截图模式超时为 15s（`VideoPlayer.vue:229`）；截图模式帧率 ~0.7-2 FPS → 设备类断言超时下限 30s
- RK3288 设备硬件编码器已知会崩（`方案/进度追踪.md` 第十一次更新）→ 「视频直播中」断言必须同时接受 h264 与 screenshot 两种模式，另用专测验证回退
- 输入控制断言统一使用 `dumpsys window | grep mCurrentFocus`（shell 走 `POST /api/debug/sessions/{sid}/shell?command=...`），不依赖具体桌面/设置包名
- Git Bash 环境注意路径转换问题不适用于本计划（E2E 全部走 HTTP/WS，不直接调 adb）
- 每个 Task 结束提交一次 commit；commit message 用 `test:`/`feat:`/`docs:` 前缀（中文描述，与仓库风格一致）

## 文件结构（总览）

```
package.json                      # [新建] 根 Node runner（仅 @playwright/test）
.gitignore                        # [修改] 忽略 playwright 产物
tests/e2e/
├── playwright.config.ts          # [新建] webServer、chromium project、超时
├── fixtures.ts                   # [新建] device/shell fixture，无设备自动 skip
├── README.md                     # [新建] 运行手册
└── specs/
    ├── smoke.spec.ts             # [新建] 服务可达 + Dashboard 渲染（无设备依赖）
    ├── video-stream.spec.ts      # [新建] 视频直播中 + 帧计数增长
    ├── screenshot-fallback.spec.ts # [新建] 删 VideoDecoder 强制回退
    ├── input-control.spec.ts     # [新建] 顶部下滑开通知栏 + Home 键
    └── coordinate-mapping.spec.ts  # [新建] 旋转 → canvas 尺寸交换 → 旋转态滑动仍有效
frontend/package.json             # [修改] （不改动，E2E 不进前端目录，见 runner 决策）
方案/进度追踪.md                   # [修改] 最终记录 E2E 结论
```

为什么根目录加 package.json：Playwright 的 spec/config 按 Node 规则从文件所在目录向上解析 `@playwright/test`；`tests/` 位于仓库根（无 node_modules），前端 `frontend/node_modules` 不在解析路径上。最小代价即在根放一个只含 runner 的 package.json（不搬任何业务依赖）。

---

### Task 1: E2E runner 基础设施 + 冒烟测试（无设备可跑）

**Files:**
- Create: `package.json`（仓库根）
- Create: `tests/e2e/playwright.config.ts`
- Create: `tests/e2e/specs/smoke.spec.ts`
- Modify: `.gitignore`（追加产物目录）

**Interfaces:**
- Consumes: 无
- Produces: `npx playwright test`（在仓库根执行）可运行；`tests/e2e/playwright.config.ts` 中 baseURL=`http://localhost:8080`、webServer 双进程编排；后续所有 spec 依赖此配置

- [ ] **Step 1: 安装 runner**

```bash
# 仓库根创建 package.json
cat > package.json <<'EOF'
{
  "name": "openscrcpy-e2e",
  "private": true,
  "scripts": {
    "test:e2e": "playwright test --config tests/e2e/playwright.config.ts"
  },
  "devDependencies": {
    "@playwright/test": "^1.46.0"
  }
}
EOF
npm install
npx playwright install chromium
```

预期生成 `package.json`、`package-lock.json`、根 `node_modules/`。

- [ ] **Step 2: 写失败冒烟测试**

`tests/e2e/specs/smoke.spec.ts`：

```typescript
import { test, expect } from '@playwright/test'

// 冒烟层：只验证三方进程（后端/前端/页面）可达，不依赖设备
test.describe('smoke', () => {
  test('后端健康检查可达', async ({ request }) => {
    // 走前端 8080 没有 /health 代理，直连后端
    const res = await request.get('http://localhost:8765/health')
    expect(res.ok()).toBeTruthy()
    expect(await res.json()).toEqual({ status: 'ok' })
  })

  test('设备 API 可达且返回数组', async ({ request }) => {
    const res = await request.get('/api/devices')   // baseURL 8080 → vite 代理
    expect(res.ok()).toBeTruthy()
    expect(Array.isArray(await res.json())).toBeTruthy()
  })

  test('Dashboard 渲染设备列表页', async ({ page }) => {
    await page.goto('/')
    await expect(page.locator('text=添加设备')).toBeVisible({ timeout: 10_000 })
  })
})
```

- [ ] **Step 3: 运行确认失败**

Run: `npx playwright test --config tests/e2e/playwright.config.ts`
Expected: FAIL —— "Playwright Test did not expect test() to be called here" 之前会先报找不到 config（config 尚未创建）；创建 config 前用不带 `--config` 的调用验证报错即可。

- [ ] **Step 4: 写 Playwright 配置**

`tests/e2e/playwright.config.ts`：

```typescript
import { defineConfig } from '@playwright/test'
import * as path from 'path'

// E2E 编排：自动拉起后端(8765) + 前端 dev server(8080)
// 端口与代理关系见 CLAUDE.md：vite 将 /api 与 /ws 代理到 8765
export default defineConfig({
  testDir: './specs',
  outputDir: './.test-output',
  timeout: 60_000,
  expect: { timeout: 15_000 },
  workers: 1,                 // 单设备 + scrcpy 单实例约束，必须串行
  fullyParallel: false,
  retries: 0,
  reporter: [['list'], ['html', { open: 'never', outputFolder: './report' }]],
  use: {
    baseURL: 'http://localhost:8080',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'off',
  },
  projects: [
    { name: 'chromium', use: { browserName: 'chromium' } },   // 项目仅支持 Chrome
  ],
  webServer: [
    {
      command: 'python run_server.py',
      cwd: path.resolve(__dirname, '../..'),
      url: 'http://localhost:8765/health',
      reuseExistingServer: true,
      timeout: 60_000,
      stdout: 'pipe',
    },
    {
      command: 'npm run dev',
      cwd: path.resolve(__dirname, '../../frontend'),
      url: 'http://localhost:8080',
      reuseExistingServer: true,
      timeout: 120_000,
      stdout: 'pipe',
    },
  ],
})
```

- [ ] **Step 5: 忽略产物目录**

`.gitignore` 追加：

```gitignore
# Playwright E2E
test-results/
.playwright-output/
tests/e2e/.test-output/
tests/e2e/report/
```

- [ ] **Step 6: 运行确认通过**

Run: `npx playwright test`（根目录，自动读 package.json script 亦可 `npm run test:e2e`）
Expected: 3 passed。若本机已有前后端进程在跑，`reuseExistingServer` 会直接复用。

- [ ] **Step 7: Commit**

```bash
git add package.json package-lock.json tests/e2e/playwright.config.ts tests/e2e/specs/smoke.spec.ts .gitignore
git commit -m "test: 搭建 Playwright E2E 基础设施（根 runner + webServer 编排 + 冒烟测试）"
```

---

### Task 2: device / shell fixture（无设备自动 skip）

**Files:**
- Create: `tests/e2e/fixtures.ts`
- Modify: `tests/e2e/specs/smoke.spec.ts`（追加一条 fixture 自检用例）

**Interfaces:**
- Consumes: Task 1 的 config（baseURL、request context）
- Produces: 供全部设备 spec 使用的具名导出——`test`（extend 后）、`expect`、`interface DeviceInfo { id: string; model: string; status: string; resolution: [number, number] }`、fixture `device: DeviceInfo | null`、fixture `shell: (cmd: string) => Promise<string>`（基于 device 创建 debug session，测试结束关闭）。Task 3-6 的 spec `import { test, expect } from '../fixtures'`

- [ ] **Step 1: 写 fixture 自检用例（当前必失败——fixtures.ts 不存在）**

在 `smoke.spec.ts` 追加：

```typescript
import { test as dtest, expect as dexpect, type DeviceInfo } from '../fixtures'

dtest('fixture：设备探测与 shell 通道', async ({ device, shell }) => {
  dtest.skip(!device, '无在线 ADB 设备，跳过')
  dexpect(device!.id).toBeTruthy()
  const out = await shell('echo e2e-ok')
  dexpect(out).toContain('e2e-ok')
})
```

- [ ] **Step 2: 运行确认失败**

Run: `npx playwright test -g "fixture"`
Expected: FAIL —— Cannot find module '../fixtures'

- [ ] **Step 3: 实现 fixtures.ts**

```typescript
import { test as base, expect } from '@playwright/test'

export interface DeviceInfo {
  id: string
  model: string
  status: string
  resolution: [number, number]
}

type Fixtures = {
  device: DeviceInfo | null
  shell: (cmd: string) => Promise<string>
}

// 设备探测：取第一个 online 设备；无设备返回 null，由 spec 决定 skip。
// shell 通道：POST /api/debug/sessions?device_id&user_id → session_id，
// 再 POST /api/debug/sessions/{sid}/shell?command=...，返回 stdout 文本。
export const test = base.extend<Fixtures>({
  device: async ({ request }, use, testInfo) => {
    const res = await request.get('/api/devices')
    const devices = (await res.json()) as DeviceInfo[]
    const dev = devices.find((d) => d.status === 'online') ?? null
    if (!dev) {
      testInfo.annotations.push({ type: 'skip-reason', description: 'no online ADB device' })
    }
    await use(dev)
  },

  shell: async ({ device, request }, use) => {
    if (!device) {
      await use(async () => {
        throw new Error('shell 需要在线设备：请连接 ADB 设备后重试')
      })
      return
    }
    const createRes = await request.post('/api/debug/sessions', {
      params: { device_id: device.id, user_id: 'e2e-runner' },
    })
    const { session_id: sessionId } = (await createRes.json()) as { session_id: string }
    try {
      await use(async (cmd: string) => {
        const res = await request.post(`/api/debug/sessions/${sessionId}/shell`, {
          params: { command: cmd },
        })
        const { output } = (await res.json()) as { output: string }
        return output
      })
    } finally {
      await request.delete(`/api/debug/sessions/${sessionId}`)
    }
  },
})

export { expect }
```

- [ ] **Step 4: 运行 fixture 自检**

Run: `npx playwright test -g "fixture"`
Expected: 有设备：1 passed；无设备：1 skipped（两者均不算失败）

- [ ] **Step 5: Commit**

```bash
git add tests/e2e/fixtures.ts tests/e2e/specs/smoke.spec.ts
git commit -m "test: 新增 E2E device/shell fixture，无设备自动 skip"
```

---

### Task 3: 视频流活跃性验证（H264/截图双模式兼容）

**Files:**
- Create: `tests/e2e/specs/video-stream.spec.ts`

**Interfaces:**
- Consumes: Task 2 的 `device`/`expect`；`VideoPlayer.vue` 的稳定选择器——canvas `.video-canvas`、状态区 `.stats .stat-item`（文本形如 `模式: h264`、`帧: 123`、状态 span 类名 `streaming`）
- Produces: 「进入设备页 → 等到直播中」的页面导航函数 `gotoDevicePage(page, device)`（本文件内定义并在 Task 5/6 复制使用时保持同一实现，见各任务内完整代码）

- [ ] **Step 1: 编写 spec**

`tests/e2e/specs/video-stream.spec.ts`：

```typescript
import { test, expect, type DeviceInfo } from '../fixtures'
import type { Page } from '@playwright/test'

// 进入设备详情页并等待视频状态变为「直播中」。
// h264 或 screenshot 模式均可接受（RK3288 已知编码器崩溃会走回退）。
export async function gotoDevicePage(page: Page, device: DeviceInfo) {
  await page.goto(`/device/${encodeURIComponent(device.id)}`)
  await expect(page.locator('.stat-item.streaming')).toBeVisible({ timeout: 30_000 })
}

async function readFrameCount(page: Page): Promise<number> {
  const text = await page.locator('.stat-item', { hasText: '帧:' }).innerText()
  return Number(text.replace(/\D/g, ''))
}

test.describe('视频流（需设备）', () => {
  test.use({ actionTimeout: 15_000 })

  test('设备页视频直播中且帧计数持续增长', async ({ page, device }) => {
    test.skip(!device, '无在线 ADB 设备')
    await gotoDevicePage(page, device!)

    const modeText = await page.locator('.stat-item', { hasText: '模式:' }).innerText()
    expect(['h264', 'screenshot']).toContain(modeText.replace('模式:', '').trim())

    // 帧计数增长证明解码/渲染链路活着
    const f1 = await readFrameCount(page)
    await page.waitForTimeout(3_000)
    const f2 = await readFrameCount(page)
    expect(f2).toBeGreaterThan(f1)

    // canvas 实际像素尺寸 == 设备分辨率（max_size=0 契约，VideoPlayer 由 config 消息设置）
    const dims = await page.evaluate(() => {
      const c = document.querySelector('.video-canvas') as HTMLCanvasElement
      return { w: c.width, h: c.height }
    })
    const [dw, dh] = device!.resolution
    expect([[dw, dh], [dh, dw]]).toContain([dims.w, dims.h])
  })

  test('canvas 像素非纯色（确有画面渲染）', async ({ page, device }) => {
    test.skip(!device, '无在线 ADB 设备')
    await gotoDevicePage(page, device!)
    await page.waitForTimeout(2_000)
    const sampled = await page.evaluate(() => {
      const c = document.querySelector('.video-canvas') as HTMLCanvasElement
      const ctx = c.getContext('2d', { willReadFrequently: true })!
      // WebCodecs drawImage 到 2d-context canvas 后可直接 getImageData
      const d = ctx.getImageData(0, 0, c.width, c.height).data
      const uniq = new Set<string>()
      for (let i = 0; i < d.length; i += 4 * 997) {
        uniq.add(`${d[i]},${d[i + 1]},${d[i + 2]}`)
        if (uniq.size > 10) break
      }
      return uniq.size
    })
    expect(sampled).toBeGreaterThan(1)
  })
})
```

注意：H.264 模式下解码帧经 `VideoDecoder → drawImage` 写入 canvas，`getContext('2d')` 二次读取可能返回空（WebCodecs VideoFrame 绘制不污染 canvas 但 2d context 与解码器绘制 context 不同源）。若本用例首跑失败且确认是 context 冲突，将断言降级为「采样 canvas 的 CSS 渲染截图」：`await page.locator('.video-canvas').screenshot()` 后在 Node 侧比较 buffer 熵（字节唯一值数 > 10）。降级实现即为最终实现，二选一以首跑结果为准并在 commit message 里注明选择原因。

- [ ] **Step 2: 运行**

Run: `npx playwright test video-stream`
Expected: 有设备全绿；若触发 Step 1 注释的降级路径，改代码后重跑至全绿；无设备 2 skipped

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/specs/video-stream.spec.ts
git commit -m "test: 视频流活跃性 E2E（帧计数增长 + canvas 尺寸契约 + 画面非纯色）"
```

---

### Task 4: 截图回退模式验证（强制 WebCodecs 不可用）

**Files:**
- Create: `tests/e2e/specs/screenshot-fallback.spec.ts`

**Interfaces:**
- Consumes: Task 3 的导航等待模式（本文件内联同款实现，不跨文件 import 业务函数以免 spec 间耦合；fixture 除外）
- Produces: 无（终端用例）

- [ ] **Step 1: 编写 spec**

`tests/e2e/specs/screenshot-fallback.spec.ts`：

```typescript
import { test, expect } from '../fixtures'

// 在页面脚本执行前删除 VideoDecoder/EncodedVideoChunk，
// 使 VideoPlayer.vue 的 isWebCodecsSupported() 返回 false，
// 直接走截图模式（无需等 15s 超时回退）。
test('无 WebCodecs 时自动回退截图模式且出帧', async ({ page, device }) => {
  test.skip(!device, '无在线 ADB 设备')
  await page.addInitScript(() => {
    // @ts-expect-error 故意删除全局对象以模拟不支持环境
    delete window.VideoDecoder
    // @ts-expect-error 同上
    delete window.EncodedVideoChunk
  })
  await page.goto(`/device/${encodeURIComponent(device!.id)}`)
  await expect(page.locator('.stat-item', { hasText: '模式: screenshot' })).toBeVisible({
    timeout: 30_000,
  })
  await expect(page.locator('.stat-item.streaming')).toBeVisible()

  const frameText = page.locator('.stat-item', { hasText: '帧:' })
  const f1 = Number((await frameText.innerText()).replace(/\D/g, ''))
  await page.waitForTimeout(4_000) // 截图模式 ~0.7-2 FPS，4s 足够出 1 帧
  const f2 = Number((await frameText.innerText()).replace(/\D/g, ''))
  expect(f2).toBeGreaterThan(f1)
})
```

- [ ] **Step 2: 运行**

Run: `npx playwright test screenshot-fallback`
Expected: 1 passed（无设备则 1 skipped）

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/specs/screenshot-fallback.spec.ts
git commit -m "test: 截图回退模式 E2E（addInitScript 强制禁用 WebCodecs）"
```

---

### Task 5: 输入控制验证（通知栏下滑 + Home 键）

**Files:**
- Create: `tests/e2e/specs/input-control.spec.ts`

**Interfaces:**
- Consumes: Task 2 `shell`/`device` fixture；canvas mouse 事件 → InputController → /ws/video 二进制控制协议（`方案/13`）；`page.locator('button[title="主页"]')`（VideoPlayer 导航键）
- Produces: 无

- [ ] **Step 1: 编写 spec**

`tests/e2e/specs/input-control.spec.ts`：

```typescript
import { test, expect } from '../fixtures'

async function currentFocus(shell: (c: string) => Promise<string>): Promise<string> {
  const out = await shell('dumpsys window | grep mCurrentFocus')
  const m = out.match(/mCurrentFocus=Window\{[^ ]+ [^ ]+ ([^\s}]+)/)
  return m ? m[1] : out.trim()
}

test.describe('输入控制（需设备）', () => {
  test('画布顶部下滑 → 通知栏展开 → 上滑收回', async ({ page, device, shell }) => {
    test.skip(!device, '无在线 ADB 设备')
    await page.goto(`/device/${encodeURIComponent(device!.id)}`)
    await expect(page.locator('.stat-item.streaming')).toBeVisible({ timeout: 30_000 })

    const box = await page.locator('.video-canvas').boundingBox()
    expect(box).not.toBeNull()
    const cx = box!.x + box!.width / 2

    const before = await currentFocus(shell)

    // 从画面顶缘 1px 处按住下拖（模拟下拉通知栏手势）
    await page.mouse.move(cx, box!.y + 1)
    await page.mouse.down()
    for (let i = 1; i <= 8; i++) {
      await page.mouse.move(cx, box!.y + 1 + (box!.height * i) / 8, { steps: 2 })
      await page.waitForTimeout(30)
    }
    await page.mouse.up()

    // 通知栏窗口获得焦点（不同 ROM 类名为 NotificationPanelView/NotificationShade，
    // 统一用 Notification 前缀匹配）
    await expect
      .poll(async () => await currentFocus(shell), { timeout: 10_000 })
      .toMatch(/Notification/)

    // 收回通知栏
    await page.mouse.move(cx, box!.y + box!.height - 2)
    await page.mouse.down()
    await page.mouse.move(cx, box!.y + 2, { steps: 6 })
    await page.mouse.up()
    await expect
      .poll(async () => await currentFocus(shell), { timeout: 10_000 })
      .not.toMatch(/Notification/)

    // 焦点回到下滑前的界面（证明输入事件确实落到了设备）
    expect(await currentFocus(shell)).toBe(before)
  })

  test('主页按钮 → 焦点切走当前应用', async ({ page, device, shell }) => {
    test.skip(!device, '无在线 ADB 设备')
    // 用设备通用 intent 打开「设置」，不依赖具体包名
    const opened = await shell(
      'am start -a android.settings.SETTINGS; sleep 2; dumpsys window | grep -c mCurrentFocus',
    )
    expect(opened).toContain('1')

    await page.goto(`/device/${encodeURIComponent(device!.id)}`)
    await expect(page.locator('.stat-item.streaming')).toBeVisible({ timeout: 30_000 })

    const focusInSettings = await currentFocus(shell)
    expect(focusInSettings).not.toMatch(/Notification/)

    await page.locator('button[title="主页"]').click()

    // Home 后焦点离开设置（回到桌面，不同 ROM 桌面组件名不同 → 只断言"变了"）
    await expect
      .poll(async () => await currentFocus(shell), { timeout: 10_000 })
      .not.toBe(focusInSettings)
  })
})
```

- [ ] **Step 2: 运行**

Run: `npx playwright test input-control`
Expected: 2 passed（无设备则 2 skipped）。若 `mCurrentFocus` 正则不匹配目标 ROM 输出，按设备实际 dumpsys 输出调整 `currentFocus()` 的正则（只允许改解析函数，不允许弱化断言）。

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/specs/input-control.spec.ts
git commit -m "test: 输入控制 E2E（通知栏下滑/收回 + Home 键，断言走 mCurrentFocus）"
```

---

### Task 6: 坐标映射与横竖屏（旋转后手势仍然有效）

**Files:**
- Create: `tests/e2e/specs/coordinate-mapping.spec.ts`

**Interfaces:**
- Consumes: Task 2 fixture；`settings put system user_rotation`（adb shell 有 WRITE_SETTINGS 权限）；canvas 像素尺寸由后端 config 消息驱动（第十四次更新修复项）
- Produces: 无

- [ ] **Step 1: 编写 spec**

`tests/e2e/specs/coordinate-mapping.spec.ts`：

```typescript
import { test, expect } from '../fixtures'

async function canvasPixelSize(page: import('@playwright/test').Page) {
  return page.evaluate(() => {
    const c = document.querySelector('.video-canvas') as HTMLCanvasElement
    return [c.width, c.height] as [number, number]
  })
}

async function focusOf(shell: (c: string) => Promise<string>): Promise<string> {
  const out = await shell('dumpsys window | grep mCurrentFocus')
  const m = out.match(/mCurrentFocus=Window\{[^ ]+ [^ ]+ ([^\s}]+)/)
  return m ? m[1] : out.trim()
}

test('旋转到横屏：canvas 尺寸交换且顶缘下滑手势依然有效', async ({ page, device, shell }) => {
  test.skip(!device, '无在线 ADB 设备')
  try {
    await page.goto(`/device/${encodeURIComponent(device!.id)}`)
    await expect(page.locator('.stat-item.streaming')).toBeVisible({ timeout: 30_000 })

    const [dw, dh] = device!.resolution
    const portrait = await canvasPixelSize(page)
    expect([portrait, [...portrait].reverse()]).toContainEqual([dw, dh])

    // 关闭自动旋转后强制横屏（RK3288 等定制系统可能忽略 → 探测后 skip）
    await shell('settings put system accelerometer_rotation 0')
    await shell('settings put system user_rotation 1')
    const rotated = await expect
      .poll(() => canvasPixelSize(page), { timeout: 20_000 })
      .toEqual(portrait[0] === dw ? [dh, dw] : [dw, dh])
    void rotated

    // 横屏下从 canvas 顶缘下滑：证明 InputController 按当前（旋转后）分辨率映射
    await expect(page.locator('.stat-item.streaming')).toBeVisible({ timeout: 30_000 })
    const box = await page.locator('.video-canvas').boundingBox()
    const cx = box!.x + box!.width / 2
    await page.mouse.move(cx, box!.y + 1)
    await page.mouse.down()
    await page.mouse.move(cx, box!.y + box!.height - 2, { steps: 8 })
    await page.mouse.up()
    await expect
      .poll(async () => await focusOf(shell), { timeout: 10_000 })
      .toMatch(/Notification/)
  } finally {
    // 恢复竖屏与自动旋转，避免污染后续测试与设备状态
    await shell('settings put system user_rotation 0').catch(() => {})
    await shell('settings put system accelerometer_rotation 1').catch(() => {})
  }
})
```

- [ ] **Step 2: 运行**

Run: `npx playwright test coordinate-mapping`
Expected: 1 passed。若设备忽略 user_rotation（20s 内 canvas 未交换），本 spec 首跑会 fail——此时在 spec 开头增加探测步骤：`test.skip(!(await rotationSupported(shell)), '设备不支持软件旋转')`，其中 `rotationSupported` 用 `settings get system user_rotation` 写入后回读验证。降级到该实现视为通过路径之一，并在 commit message 注明。

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/specs/coordinate-mapping.spec.ts
git commit -m "test: 坐标映射 E2E（旋转后 canvas 尺寸契约 + 横屏下滑手势验证）"
```

---

### Task 7: 运行手册、全量回归与进度回写

**Files:**
- Create: `tests/e2e/README.md`
- Modify: `方案/进度追踪.md`

**Interfaces:**
- Consumes: Task 1-6 全部产物
- Produces: 可交接的运行文档；进度追踪中优先级第 1 项的状态与结论文据

- [ ] **Step 1: 写运行手册**

`tests/e2e/README.md`，内容覆盖（每条一句命令示例 + 一句说明）：

```markdown
# 浏览器端到端测试（tests/e2e）

## 前置
1. 仓库根：`npm install && npx playwright install chromium`（仅首次）
2. ADB 设备已连接并在线：`tools/adb.exe devices`（无设备时设备类用例自动 skip）
3. 无需手动起服务：playwright webServer 自动拉起后端 8765 与前端 dev 8080（已存在实例会复用）

## 运行
- 全量：`npm run test:e2e`（仓库根）
- 单文件：`npx playwright test video-stream --config tests/e2e/playwright.config.ts`
- 有头调试：追加 `--headed --debug`
- 报告：`tests/e2e/report/index.html`（HTML reporter 产物）

## 设计约定
- workers=1 串行（单设备 + scrcpy-server 每设备单实例）
- 设备断言统一走 debug session shell（dumpsys/settings/am），不直接依赖 adb CLI
- 视频用例同时接受 h264 与 screenshot 模式（RK3288 编码器已知问题）
- 端口硬编码 8765/8080：与 run_server.py、vite.config.ts 一致，端口可配置化落地后需同步本文件与 config
```

- [ ] **Step 2: 全量回归**

Run: `npm run test:e2e`
Expected: 全部 passed（有设备）或 smoke+fixture 通过、设备类 skipped（无设备）。任何 fail 都必须修复后重跑，不允许带红收尾。

- [ ] **Step 3: 回写进度追踪**

`方案/进度追踪.md`「当前阶段优先级」第 1 项行尾追加执行结论，模板（按实际结果填写）：

```markdown
1. 浏览器端到端测试：✅ 已落地 `tests/e2e/`（Playwright，N 用例）——H264 解码/输入控制/坐标映射/截图回退结论：____（全通过 / 设备不支持项：____）。详见 `docs/superpowers/plans/2026-09-17-browser-e2e-testing.md`
```

同时更新「质量指标」表 E2E 测试通过率一行（目标 100%，当前按实测填）。

- [ ] **Step 4: Commit**

```bash
git add tests/e2e/README.md 方案/进度追踪.md
git commit -m "docs: E2E 运行手册与进度回写（视频解码+输入控制端到端验证落地）"
```

---

## Self-Review 结论（已执行）

1. 覆盖检查：优先级第 1 项拆解为——视频解码显示（Task 3）、输入控制（Task 5）、横竖屏点击定位（Task 6）、回退体验（Task 4）、基础设施与文档（Task 1/2/7），无遗漏；优先级 2-8 项属于其他子系统，各自另出计划。
2. 占位符扫描：Task 3 的 canvas 像素读取与 Task 6 的旋转不支持均给出了二选一的**具体降级实现**，非 TBD。
3. 类型一致性：`DeviceInfo.resolution: [number, number]` 与后端 `device.py` dataclass 序列化一致；fixture 签名 `shell(cmd)` 与 `debug.py` 端点（query 参数 `command`）一致；选择器 `.stat-item.streaming`/`.video-canvas`/`button[title="主页"]` 均核对自 `VideoPlayer.vue` 现码。
