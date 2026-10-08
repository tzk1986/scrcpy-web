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

// 部分设备忽略 user_rotation（settings 可写但 SurfaceOrientation 不变）→
// 以真实屏幕方向生效为准探测；无论结果如何 finally 都还原横屏，
// 保证测试主体测得横屏基准尺寸后再显式旋转
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
    await shell('settings put system user_rotation 0').catch(() => {})
    // 等待方向实际回 0，避免页面加载时设备仍处于旋转过渡态
    for (let i = 0; i < 10; i++) {
      const out = await shell('dumpsys input | grep -m1 SurfaceOrientation').catch(() => '')
      if (/SurfaceOrientation:\s*0/.test(out)) break
      await new Promise((r) => setTimeout(r, 500))
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

      // scrcpy 编码尺寸向下取整到 8 的倍数（如 1366→1360），canvas 反映的是
      // config 消息中的编码流尺寸而非物理分辨率，比较前需同样取整
      const round8 = (v: number): number => v - (v % 8)
      const [dw, dh] = device!.resolution
      const before = await canvasPixelSize(page)
      expect([before, [...before].reverse() as [number, number]]).toContainEqual([
        round8(dw),
        round8(dh),
      ])

      // 显式旋转到竖屏：等待 config 消息驱动 canvas 尺寸交换
      await shell('settings put system user_rotation 1')
      const [w0, h0] = before
      await expect
        .poll(() => canvasPixelSize(page), { timeout: 20_000 })
        .toEqual([h0, w0])

      // 旋转后从 canvas 顶缘下滑：证明输入按当前（旋转后）分辨率映射
      await expect(page.locator('.stat-item.streaming')).toBeVisible({ timeout: 30_000 })

      // 前台锚定：部分设备固件的默认 launcher 为沉浸全屏（隐藏系统栏），
      // 其前台时顶缘下滑不会展开通知栏（.18 真机实测，注入/原生滑动均无效）；
      // 且 kiosk 应用可能随时抢占前台。先导航到 Settings（非沉浸）并等待
      // 转场结束，保证滑动落点环境稳定后再测映射
      await shell('cmd statusbar collapse; am start -a android.settings.DEVICE_INFO_SETTINGS')
      await new Promise((r) => setTimeout(r, 2000))

      const box = await page.locator('.video-canvas').boundingBox()
      expect(box).not.toBeNull()
      const cx = box!.x + box!.width / 2
      const swipeDown = async (): Promise<void> => {
        await page.mouse.move(cx, box!.y + 1)
        await page.mouse.down()
        await page.mouse.move(cx, box!.y + box!.height - 2, { steps: 8 })
        await page.mouse.up()
      }
      await swipeDown()
      // 一次重试兜底窗口切换瞬态的偶发丢弃
      try {
        await expect
          .poll(async () => await focusOf(shell), { timeout: 4_000 })
          .toMatch(/StatusBar|Notification/)
      } catch {
        await swipeDown()
        await expect
          .poll(async () => await focusOf(shell), { timeout: 10_000 })
          .toMatch(/StatusBar|Notification/)
      }
    } finally {
      // 恢复默认旋转与自动旋转，避免污染后续测试与设备状态
      await shell('settings put system user_rotation 0').catch(() => {})
      await shell('settings put system accelerometer_rotation 1').catch(() => {})
    }
  })
})
