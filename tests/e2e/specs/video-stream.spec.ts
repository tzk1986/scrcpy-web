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

  test('设备页视频直播中且帧计数持续增长', async ({ page, device, shell }) => {
    test.skip(!device, '无在线 ADB 设备')
    // 亮屏，保证编码器/截屏持续出帧
    await shell('input keyevent KEYCODE_WAKEUP; svc power stayon true')
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
    expect([[dw, dh], [dh, dw]]).toContainEqual([dims.w, dims.h])
  })

  test('canvas 像素非纯色（确有画面渲染）', async ({ page, device, shell }) => {
    test.skip(!device, '无在线 ADB 设备')
    await shell('input keyevent KEYCODE_WAKEUP; svc power stayon true')
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
