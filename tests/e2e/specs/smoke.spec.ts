import { test, expect } from '@playwright/test'
import { test as dtest, expect as dexpect } from '../fixtures'

// 冒烟层：只验证三方进程（后端/前端/页面）可达，不依赖设备
test.describe('smoke', () => {
  test('后端健康检查可达', async ({ request }) => {
    // 走前端 8080 没有 /health 代理，直连后端（端口与后端共用 BACKEND_PORT）
    const backendPort = process.env.BACKEND_PORT || '8765'
    const res = await request.get(`http://localhost:${backendPort}/health`)
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

dtest('fixture：设备探测与 shell 通道', async ({ device, shell }) => {
  dtest.skip(!device, '无在线 ADB 设备，跳过')
  dexpect(device!.id).toBeTruthy()
  const out = await shell('echo e2e-ok')
  dexpect(out).toContain('e2e-ok')
})
