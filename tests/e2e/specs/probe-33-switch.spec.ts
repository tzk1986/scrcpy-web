// 临时诊断 spec（.33 切页画面滞后调研，非回归用例）：
// 在真实页面点击「规格二」tab，采集 canvas 内容变化时间线 + [H264] console 日志
// + 前端丢帧/帧计数，量化「点击 → 浏览器可见新画面」的各段耗时。
import { test } from '../fixtures'
import type { Page } from '@playwright/test'

async function installCanvasProbe(page: Page) {
  await page.evaluate(() => {
    const c = document.querySelector('.video-canvas') as HTMLCanvasElement
    const small = document.createElement('canvas')
    small.width = 32
    small.height = 51
    const sctx = small.getContext('2d', { willReadFrequently: true })!
    ;(window as any).__probeEvents = [] as Array<{ t: number; sig: string }>
    let last = ''
    const tick = () => {
      sctx.drawImage(c, 0, 0, 32, 51)
      const d = sctx.getImageData(0, 0, 32, 51).data
      let h = 0
      for (let i = 0; i < d.length; i += 4) {
        h = ((h << 5) - h + d[i] + d[i + 1] * 31 + d[i + 2] * 127) | 0
      }
      const s = String(h)
      if (s !== last) {
        ;(window as any).__probeEvents.push({ t: performance.now(), sig: s })
        last = s
      }
      ;(window as any).__probeTimer = setTimeout(tick, 100)
    }
    tick()
  })
}

test.describe('诊断：.33 切页画面滞后', () => {
  test('点击规格二 tab 的端到端可见延迟', async ({ page, device, shell }) => {
    test.skip(!device, '无在线 ADB 设备')
    await shell('input keyevent KEYCODE_WAKEUP; svc power stayon true')

    const logs: string[] = []
    page.on('console', (m) => {
      const t = m.text()
      if (/\[H264\]|\[Video|丢帧|decode|queue/i.test(t)) {
        logs.push(`${m.type()}: ${t}`)
        console.log(`  [browser${m.type() === 'warning' ? ' warn' : ''}] ${t}`)
      }
    })

    await page.goto(`/device/${encodeURIComponent(device!.id)}`)
    await page.waitForSelector('.stat-item.streaming', { timeout: 45_000 })

    // 稳定 6s（越过启动密集 IDR 期），再装采样器
    await page.waitForTimeout(6_000)
    await installCanvasProbe(page)
    await page.waitForTimeout(2_000) // 采样基线

    const readStats = async () => {
      const text = await page.evaluate(() => {
        const items = Array.from(document.querySelectorAll('.stat-item')) as HTMLElement[]
        return items.map((i) => i.innerText).join(' | ')
      })
      return text
    }
    console.log('[probe] click 前 stats:', await readStats())

    // canvas 坐标映射：设备 (216,396) → 显示坐标
    const box = await page.locator('.video-canvas').boundingBox()
    const dims = await page.evaluate(() => {
      const c = document.querySelector('.video-canvas') as HTMLCanvasElement
      return { w: c.width, h: c.height }
    })
    const dx = box!.x + (216 / dims.w) * box!.width
    const dy = box!.y + (396 / dims.h) * box!.height
    const tClick = await page.evaluate(() => performance.now())
    await page.mouse.click(dx, dy)
    console.log(`[probe] click@(${dx.toFixed(0)},${dy.toFixed(0)}) canvas=${dims.w}x${dims.h} t=${tClick.toFixed(0)}`)

    await page.waitForTimeout(10_000)

    const events = await page.evaluate(() => {
      clearTimeout((window as any).__probeTimer)
      return (window as any).__probeEvents as Array<{ t: number; sig: string }>
    })
    console.log('[probe] click 后 stats:', await readStats())

    // 输出变化时间线（相对 click），>300ms 的静默间隔单独标注
    const rel = events.map((e) => e.t - tClick)
    console.log(`[probe] canvas 变化事件数=${events.length}`)
    let prev: number | null = null
    const lines: string[] = []
    for (const r of rel) {
      if (r < -3_000) continue
      const gap = prev !== null ? r - prev : 0
      const flag = prev !== null && gap > 300 ? `  <<< 静默 ${gap.toFixed(0)}ms` : ''
      if (r > -3_000 && r < 6_000) lines.push(`  dt=${(r / 1000).toFixed(2)}s gap=${gap.toFixed(0)}ms${flag}`)
      prev = r
    }
    console.log('[probe] canvas 变化时间线:\n' + lines.join('\n'))

    // 静默段统计：click 后最大变化间隔（= 浏览器可见「卡住」的时长）
    let maxGap = 0
    let maxGapAt = 0
    prev = null
    for (const r of rel) {
      if (prev !== null && r > 0 && r < 8_000) {
        const g = r - prev
        if (g > maxGap) {
          maxGap = g
          maxGapAt = r
        }
      }
      prev = r
    }
    console.log(`[probe] click 后最大静默间隔=${maxGap.toFixed(0)}ms（结束于 dt=${(maxGapAt / 1000).toFixed(2)}s）`)

    console.log(`[probe] console 相关日志 ${logs.length} 条:`)
    for (const l of logs.slice(0, 40)) console.log('  ' + l)
  })
})