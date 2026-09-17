import { defineConfig } from '@playwright/test'
import * as path from 'path'

// E2E 编排：自动拉起后端(8765) + 前端 dev server(8080)
// 端口与代理关系见 CLAUDE.md：vite 将 /api 与 /ws 代理到 8765
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
      url: 'http://localhost:8765/health',
      reuseExistingServer: true,
      timeout: 60_000,
      stdout: 'pipe',
    },
    {
      command: 'npm run dev',
      cwd: path.resolve(__dirname, '../../frontend'),
      url: 'http://localhost:8080',
      reuseExistingServer: true,
      timeout: 120_000,
      stdout: 'pipe',
    },
  ],
})
