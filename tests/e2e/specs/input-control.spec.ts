import { test, expect } from '../fixtures'

async function currentFocus(shell: (c: string) => Promise<string>): Promise<string> {
  const out = await shell('dumpsys window | grep mCurrentFocus')
  const m = out.match(/mCurrentFocus=Window\{[^ ]+ [^ ]+ ([^\s}]+)/)
  return m ? m[1] : out.trim()
}

test.describe('输入控制（需设备）', () => {
  test('画布顶部下滑 → 通知栏展开 → 上滑收回', async ({ page, device, shell }) => {
    test.skip(!device, '无在线 ADB 设备')
    await shell('input keyevent KEYCODE_WAKEUP; svc power stayon true')
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

    // 通知栏窗口获得焦点（不同 ROM 焦点名为 StatusBar/NotificationPanelView/
    // NotificationShade，统一用 StatusBar|Notification 匹配）
    await expect
      .poll(async () => await currentFocus(shell), { timeout: 10_000 })
      .toMatch(/StatusBar|Notification/)

    // 收回通知栏
    await page.mouse.move(cx, box!.y + box!.height - 2)
    await page.mouse.down()
    await page.mouse.move(cx, box!.y + 2, { steps: 6 })
    await page.mouse.up()
    await expect
      .poll(async () => await currentFocus(shell), { timeout: 10_000 })
      .not.toMatch(/StatusBar|Notification/)

    // 焦点回到下滑前的界面（证明输入事件确实落到了设备）
    expect(await currentFocus(shell)).toBe(before)
  })

  test('主页按钮 → 焦点切走当前应用', async ({ page, device, shell }) => {
    test.skip(!device, '无在线 ADB 设备')
    // 用设备通用 intent 打开「设置」，不依赖具体包名
    const opened = await shell(
      'input keyevent KEYCODE_WAKEUP; am start -a android.settings.SETTINGS; sleep 2; dumpsys window | grep -c mCurrentFocus',
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
