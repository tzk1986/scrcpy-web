import { test, expect } from '@playwright/test'

// 命令面板（Ctrl+K）冒烟：不依赖设备，验证开关、过滤、执行、无匹配提示
test.describe('command palette', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/')
    await expect(page.locator('text=添加设备')).toBeVisible({ timeout: 10_000 })
  })

  test('Ctrl+K 打开，Esc 关闭', async ({ page }) => {
    await page.keyboard.press('Control+k')
    await expect(page.locator('.cmd-input')).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(page.locator('.cmd-input')).toBeHidden()
  })

  test('输入过滤后 Enter 执行导航命令并关闭面板', async ({ page }) => {
    await page.keyboard.press('Control+k')
    await page.locator('.cmd-input').fill('Dashboard')
    await expect(page.locator('.cmd-item', { hasText: '打开 Dashboard' })).toBeVisible()
    await page.keyboard.press('Enter')
    await expect(page.locator('.cmd-input')).toBeHidden()
    await expect(page).toHaveURL(/\/$/)
  })

  test('调试与视图命令可被检索', async ({ page }) => {
    await page.keyboard.press('Control+k')
    await page.locator('.cmd-input').fill('shell')
    await expect(page.locator('.cmd-item', { hasText: '切换到 shell 面板' })).toBeVisible()
    await page.locator('.cmd-input').fill('全屏')
    await expect(page.locator('.cmd-item', { hasText: '切换全屏' })).toBeVisible()
  })

  test('无匹配时显示提示', async ({ page }) => {
    await page.keyboard.press('Control+k')
    await page.locator('.cmd-input').fill('zzzz')
    await expect(page.locator('.cmd-empty')).toBeVisible()
  })
})
