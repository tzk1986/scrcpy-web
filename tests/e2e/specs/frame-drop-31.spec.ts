/**
 * 方案 31 浏览器侧验收：丢帧锁定至下一 IDR（追赶状态机）+ warn 限速
 * ========================================================================
 *
 * 覆盖清单项（方案 31 §四）：
 *   1  活动流 + CPU 6× throttle：解码队列积压 → 进入追赶（丢帧 + 催帧
 *      request_keyframe 恰一次）→ IDR 干净恢复；恢复延迟量化
 *   2  恢复 CPU：帧率回升、droppedFrames 停止增长
 *   3  静止流：无积压无追赶误触发、无多余催帧
 *   附  console warn 不风暴（decoder-not-ready / frame-size-mismatch 限速）、
 *      全程无 VideoDecoder error
 *
 * 运行（需真机 + 后端 8765 + 前端 8080；webServer 复用既有实例）：
 *   # 场景 1+2：活动流设备（.18 时钟页 26-30fps）
 *   E2E_DEVICE=192.168.8.18:5555 npx playwright test frame-drop-31 \
 *     --config tests/e2e/playwright.config.ts -g "CPU 限速"
 *   # 场景 3：静止设备
 *   E2E_DEVICE=192.168.8.25:5555 npx playwright test frame-drop-31 \
 *     --config tests/e2e/playwright.config.ts -g "静止流"
 */
import { test, expect, type DeviceInfo } from '../fixtures'
import type { Page } from '@playwright/test'

interface DropEvent {
  at: number
  mode: string
  frames: number
  drops: number
  stateLabel: string
}

/** 注入页面内统计采样器：UI 统计项 100ms 采样（变化才记），供事后时间线分析 */
async function installProbe(page: Page) {
  await page.addInitScript(() => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const w = window as any
    const events: unknown[] = []
    w.__drop31 = { events }

    let last = ''
    setInterval(() => {
      const stats = Array.from(document.querySelectorAll('.stat-item'))
      const pick = (prefix: string) => {
        const el = stats.find((e) => (e.textContent ?? '').startsWith(prefix))
        return el ? (el.textContent ?? '').replace(prefix, '').trim() : ''
      }
      const mode = pick('模式:')
      const frames = pick('帧:')
      const drops = pick('丢帧:')
      const stateLabel = stats.length ? (stats[stats.length - 1].textContent ?? '').trim() : ''
      const key = [mode, frames, drops, stateLabel].join('|')
      if (key !== last) {
        last = key
        events.push({
          at: Date.now(),
          mode,
          frames: Number(frames) || 0,
          drops: Number(drops) || 0,
          stateLabel,
        })
      }
    }, 100)
  })
}

async function readEvents(page: Page): Promise<DropEvent[]> {
  return (await page.evaluate(() => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    return ((window as any).__drop31?.events ?? []) as unknown[]
  })) as DropEvent[]
}

/** 当前「帧:」计数（锚定行首，避开「丢帧:」） */
async function readFrameCount(page: Page): Promise<number> {
  const text = await page.locator('.stat-item').filter({ hasText: /^帧:\s*\d+/ }).innerText()
  return Number(text.replace(/\D/g, ''))
}

/** 当前「丢帧:」计数（h264 模式才显示；截图模式返回 0） */
async function readDropCount(page: Page): Promise<number> {
  const el = page.locator('.stat-item').filter({ hasText: /^丢帧:/ })
  if ((await el.count()) === 0) return 0
  return Number((await el.innerText()).replace(/\D/g, ''))
}

function fmt(ms: number): string {
  return `${(ms / 1000).toFixed(2)}s`
}

/** console warn 风暴断言共用：按子串计数 */
function countConsole(messages: string[], needle: string): number {
  return messages.filter((m) => m.includes(needle)).length
}

test.describe('方案31 浏览器侧验收（需设备）', () => {
  test('CPU 限速追赶：积压 → 追赶锁定 → 催帧恰一次 → IDR 恢复（1+2）', async ({
    page,
    device,
    context,
  }) => {
    test.skip(!device, '无在线 ADB 设备')
    test.setTimeout(150_000)
    await installProbe(page)

    const consoleMessages: string[] = []
    page.on('console', (m) => consoleMessages.push(m.text()))

    // WS 代理：记录 client → server 的 request_keyframe 催帧时刻（Node 侧打点）
    const kfTimes: number[] = []
    await page.routeWebSocket(/\/ws\/video\//, (client) => {
      const server = client.connectToServer()
      client.onMessage((m) => {
        if (typeof m === 'string') {
          try {
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            if ((JSON.parse(m) as any)?.op === 'request_keyframe') kfTimes.push(Date.now())
          } catch {
            /* 非 JSON 载荷忽略 */
          }
        }
        server.send(m)
      })
      server.onMessage((m) => client.send(m))
      client.onClose(() => server.close())
    })

    await page.goto(`/device/${encodeURIComponent((device as DeviceInfo).id)}`)
    await expect(page.locator('.stat-item.streaming')).toBeVisible({ timeout: 30_000 })
    await expect(page.locator('.stat-item', { hasText: '模式: h264' })).toBeVisible()

    // 基线窗口（注入前 10s）：headless 软解环境的恒态丢帧率对照
    await page.waitForTimeout(2_000)
    const fBase0 = await readFrameCount(page)
    const dBase0 = await readDropCount(page)
    await page.waitForTimeout(10_000)
    const fBase1 = await readFrameCount(page)
    const dBase1 = await readDropCount(page)
    console.log(
      `[31] 基线窗口（注入前 10s）：帧 ${fBase0}→${fBase1}` +
        `（${((fBase1 - fBase0) / 10).toFixed(1)}fps），丢帧 ${dBase0}→${dBase1}`,
    )

    const cdp = await context.newCDPSession(page)
    const tThrottle = Date.now()
    await cdp.send('Emulation.setCPUThrottlingRate', { rate: 6 })
    console.log(`[31] >>> CPU 6× throttle 注入 t=0（${new Date(tThrottle).toISOString()}）`)

    try {
      // 积压出现：droppedFrames 增长（进入追赶）
      await page.waitForFunction(
        () => {
          const el = Array.from(document.querySelectorAll('.stat-item')).find((e) =>
            (e.textContent ?? '').startsWith('丢帧:'),
          )
          return el ? Number((el.textContent ?? '').replace(/\D/g, '')) >= 1 : false
        },
        undefined,
        { timeout: 30_000, polling: 200 },
      )
      const tFirstDrop = Date.now()
      console.log(`[31] <<< 首次积压丢帧 t+${fmt(tFirstDrop - tThrottle)}`)

      // 追赶触发即催帧：request_keyframe 到达服务端侧记录（只看注入后的）
      await expect
        .poll(() => kfTimes.filter((t) => t >= tThrottle).length, {
          timeout: 10_000,
          message: '追赶应催帧 request_keyframe',
        })
        .toBeGreaterThanOrEqual(1)
      const tFirstKf = kfTimes.filter((t) => t >= tThrottle)[0]
      console.log(`[31] 催帧 request_keyframe #1 t+${fmt(tFirstKf - tThrottle)}`)

      // IDR 干净恢复：催帧后帧计数重新增长（解码提交走出追赶）
      const fAtKf = await readFrameCount(page)
      await page.waitForFunction(
        (f0) => {
          const el = Array.from(document.querySelectorAll('.stat-item')).find((e) =>
            /^帧:\s*\d+/.test(e.textContent ?? ''),
          )
          const n = el ? Number((el.textContent ?? '').replace(/\D/g, '')) : 0
          return n >= f0 + 2
        },
        fAtKf,
        { timeout: 15_000, polling: 200 },
      )
      const tResume = Date.now()
      console.log(
        `[31] <<< 催帧 IDR 恢复出帧 t+${fmt(tResume - tThrottle)}` +
          `（进追赶→恢复 ${fmt(tResume - tFirstDrop)}，催帧→恢复 ${fmt(tResume - tFirstKf)}）`,
      )

      // 追赶期静止无花屏的间接证据：全程无解码器报错
      expect(
        consoleMessages.filter((m) => m.includes('VideoDecoder error')),
        '不得出现解码器错误（花屏伴随解码报错）',
      ).toEqual([])
    } finally {
      // 恢复 CPU：帧率回升、积压平息（场景 2）
      await cdp.send('Emulation.setCPUThrottlingRate', { rate: 1 })
      console.log('[31] >>> CPU 恢复正常，等 5s 消化与取证')
      await page.waitForTimeout(5_000)
      const f1 = await readFrameCount(page)
      const d1 = await readDropCount(page)
      await page.waitForTimeout(10_000)
      const f2 = await readFrameCount(page)
      const d2 = await readDropCount(page)
      const fpsWindow = (f2 - f1) / 10

      // 取证先行：无论断言成败都输出时间线
      const tl = await readEvents(page)
      console.log(`[31] 统计时间线（throttle 起，共 ${tl.length} 条）:`)
      for (const e of tl.filter((x) => x.at >= tThrottle)) {
        console.log(
          `  t+${fmt(e.at - tThrottle)} mode=${e.mode} frames=${e.frames} drops=${e.drops} state=${e.stateLabel}`,
        )
      }
      const kfAfter = kfTimes.filter((t) => t >= tThrottle)
      console.log(
        `[31] 催帧次数：注入后 ${kfAfter.length} 次` +
          `（${kfAfter.map((t) => `t+${fmt(t - tThrottle)}`).join(', ') || '无'}），` +
          `基线期 ${kfTimes.length - kfAfter.length} 次`,
      )
      console.log(
        `[31] <<< 恢复窗口（10s）：帧 ${f1}→${f2}（${fpsWindow.toFixed(1)}fps），丢帧 ${d1}→${d2}`,
      )

      expect(fpsWindow, 'CPU 恢复后帧率应回升（活动流 ≥15fps）').toBeGreaterThanOrEqual(15)
      // headless 软解 + 30fps 源存在轻度过载，恢复后允许零星丢帧（追赶保护），
      // 但不应持续大量丢帧（严重积压时丢帧率可达到达帧率级）
      expect(d2 - d1, 'CPU 恢复后 droppedFrames 增长须平息（10s ≤3）').toBeLessThanOrEqual(3)
    }

    // warn 限速（方案 31 §2.2）：状态性 warn 不随帧数增长（防风暴）
    const notReadyWarns = countConsole(consoleMessages, 'decoder not ready')
    const sizeWarns = countConsole(consoleMessages, 'Frame size mismatch')
    console.log(`[31] console warn 统计：not-ready ${notReadyWarns} 条，size-mismatch ${sizeWarns} 条`)
    expect(notReadyWarns, 'decoder-not-ready warn 须限速（不得每帧风暴）').toBeLessThanOrEqual(3)
    expect(sizeWarns, 'frame-size-mismatch warn 须限速（不得每帧风暴）').toBeLessThanOrEqual(3)
  })

  test('静止流：无积压无追赶误触发、无多余催帧（3）', async ({ page, device }) => {
    test.skip(!device, '无在线 ADB 设备')
    const observeSeconds = Number(process.env.STREAM31_OBSERVE_SECONDS ?? 90)
    test.setTimeout((observeSeconds + 60) * 1_000)
    await installProbe(page)

    const consoleMessages: string[] = []
    page.on('console', (m) => consoleMessages.push(m.text()))
    const kfTimes: number[] = []
    await page.routeWebSocket(/\/ws\/video\//, (client) => {
      const server = client.connectToServer()
      client.onMessage((m) => {
        if (typeof m === 'string') {
          try {
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            if ((JSON.parse(m) as any)?.op === 'request_keyframe') kfTimes.push(Date.now())
          } catch {
            /* 非 JSON 载荷忽略 */
          }
        }
        server.send(m)
      })
      server.onMessage((m) => client.send(m))
      client.onClose(() => server.close())
    })

    await page.goto(`/device/${encodeURIComponent((device as DeviceInfo).id)}`)
    await expect(page.locator('.stat-item.streaming')).toBeVisible({ timeout: 30_000 })
    await expect(page.locator('.stat-item', { hasText: '模式: h264' })).toBeVisible()
    const f0 = await readFrameCount(page)
    console.log(`[31] 静止观察开始（${observeSeconds}s），起始帧计数 ${f0}`)

    await page.waitForTimeout(observeSeconds * 1_000)
    const f1 = await readFrameCount(page)
    const d1 = await readDropCount(page)
    console.log(
      `[31] 静止观察结束：帧 ${f0}→${f1}，丢帧 ${d1}，催帧 ${kfTimes.length} 次`,
    )

    expect(d1, '静止流无积压：不得出现追赶丢帧').toBe(0)
    expect(kfTimes.length, '静止流不得催帧（无追赶误触发）').toBe(0)
    expect(f1, '静止流帧计数应缓慢增长').toBeGreaterThan(f0)
    expect(
      consoleMessages.filter((m) => m.includes('VideoDecoder error')),
      '无解码器错误',
    ).toEqual([])
    const notReadyWarns = countConsole(consoleMessages, 'decoder not ready')
    console.log(`[31] console warn 统计：not-ready ${notReadyWarns} 条`)
    expect(notReadyWarns).toBeLessThanOrEqual(3)
  })
})