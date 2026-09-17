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
