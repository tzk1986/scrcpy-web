import { test, expect } from '../fixtures'
import type { Page } from '@playwright/test'

async function canvasPixelSize(page: Page): Promise<[number, number]> {
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

// 设备可能忽略 user_rotation（RK3288 定制 ROM 实测 settings 可写但 SurfaceOrientation
// 不变）→ 以真实屏幕方向生效为准探测，不支持则恢复原状并返回 false
async function rotationSupported(shell: (c: string) => Promise<string>): Promise<boolean> {
  await shell('settings put system accelerometer_rotation 0; settings put system user_rotation 1')
  try {
    for (let i = 0; i < 5; i++) {
      const out = await shell('dumpsys input | grep -m1 SurfaceOrientation')
      if (/SurfaceOrientation:\s*[123]/.test(out)) return true
      await new Promise((r) => setTimeout(r, 1000))
    }
    return false
  } finally {
    if (!/SurfaceOrientation:\s*[123]/.test(await shell('dumpsys input | grep -m1 SurfaceOrientation'))) {
      await shell('settings put system user_rotation 0; settings put system accelerometer_rotation 1')
    }
  }
}

test.describe('坐标映射与横竖屏（需设备）', () => {
  test('旋转到横屏：canvas 尺寸交换且顶缘下滑手势依然有效', async ({ page, device, shell }) => {
    test.skip(!device, '无在线 ADB 设备')
    await shell('input keyevent KEYCODE_WAKEUP; svc power stayon true')
    test.skip(!(await rotationSupported(shell)), '设备不支持软件旋转（user_rotation 被忽略）')
    try {
      await page.goto(`/device/${encodeURIComponent(device!.id)}`)
      await expect(page.locator('.stat-item.streaming')).toBeVisible({ timeout: 30_000 })

      const [dw, dh] = device!.resolution
      const before = await canvasPixelSize(page)
      expect([before, [...before].reverse() as [number, number]]).toContainEqual([dw, dh])

      // user_rotation 已在探测时置 1：等待 config 消息驱动 canvas 尺寸交换
      const [w0, h0] = before
      await expect
        .poll(() => canvasPixelSize(page), { timeout: 20_000 })
        .toEqual([h0, w0])

      // 旋转后从 canvas 顶缘下滑：证明输入按当前（旋转后）分辨率映射
      await expect(page.locator('.stat-item.streaming')).toBeVisible({ timeout: 30_000 })
      const box = await page.locator('.video-canvas').boundingBox()
      expect(box).not.toBeNull()
      const cx = box!.x + box!.width / 2
      await page.mouse.move(cx, box!.y + 1)
      await page.mouse.down()
      await page.mouse.move(cx, box!.y + box!.height - 2, { steps: 8 })
      await page.mouse.up()
      await expect
        .poll(async () => await focusOf(shell), { timeout: 10_000 })
        .toMatch(/StatusBar|Notification/)
    } finally {
      // 恢复默认旋转与自动旋转，避免污染后续测试与设备状态
      await shell('settings put system user_rotation 0').catch(() => {})
      await shell('settings put system accelerometer_rotation 1').catch(() => {})
    }
  })
})
