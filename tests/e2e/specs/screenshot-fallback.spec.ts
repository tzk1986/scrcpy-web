import { test, expect } from '../fixtures'

// 在页面脚本执行前删除 VideoDecoder/EncodedVideoChunk，
// 使 VideoPlayer.vue 的 isWebCodecsSupported() 返回 false，
// 直接走截图模式（无需等 15s 超时回退）。
test('无 WebCodecs 时自动回退截图模式且出帧', async ({ page, device, shell }) => {
  test.skip(!device, '无在线 ADB 设备')
  await shell('input keyevent KEYCODE_WAKEUP; svc power stayon true')
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
