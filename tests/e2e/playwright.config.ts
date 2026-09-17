import { defineConfig } from '@playwright/test'
import * as path from 'path'

// 后端端口：与后端/vite 共用 BACKEND_PORT（默认 8765），保持三处一致
const backendPort = process.env.BACKEND_PORT || '8765'

// E2E 编排：自动拉起后端 + 前端 dev server(8080)
// vite 将 /api 与 /ws 代理到后端 BACKEND_PORT
export default defineConfig({
  testDir: './specs',
  outputDir: './.test-output',
  timeout: 60_000,
  expect: { timeout: 15_000 },
  workers: 1,                 // 单设备 + scrcpy 单实例约束，必须串行
  fullyParallel: false,
  retries: 0,
  reporter: [['list'], ['html', { open: 'never', outputFolder: './report' }]],
  use: {
    baseURL: 'http://localhost:8080',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'off',
  },
  projects: [
    { name: 'chromium', use: { browserName: 'chromium' } },   // 项目仅支持 Chrome
  ],
  webServer: [
    {
      command: 'python run_server.py',
      cwd: path.resolve(__dirname, '../..'),
      url: `http://localhost:${backendPort}/health`,
      reuseExistingServer: true,
      timeout: 60_000,
      stdout: 'pipe',
      // APP_ENV=e2e → 加载 config/e2e.yaml，关闭自适应码率，
      // 避免真机低帧画面触发按档重启黑屏导致用例时序抖动。
      // BACKEND_PORT → 后端绑定端口，与下方 vite 代理目标保持一致。
      env: { APP_ENV: 'e2e', BACKEND_PORT: backendPort },
    },
    {
      command: 'npm run dev',
      cwd: path.resolve(__dirname, '../../frontend'),
      url: 'http://localhost:8080',
      reuseExistingServer: true,
      timeout: 120_000,
      stdout: 'pipe',
      env: { BACKEND_PORT: backendPort },
    },
  ],
})
