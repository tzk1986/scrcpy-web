import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'path'

// vitest 配置（与 vite.config.ts 对齐别名与 vue 插件）：
// happy-dom 提供 window/document，供 store 与组件级测试使用
export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src'),
    },
  },
  test: {
    environment: 'happy-dom',
    include: ['src/**/*.{test,spec}.?(c|m)[jt]s?(x)'],
    coverage: {
      provider: 'v8',
      include: ['src/**'],
      // 启动入口与测试文件本身不计入（自定义 exclude 会覆盖默认值，需显式写回）
      exclude: ['src/main.ts', 'src/App.vue', 'src/**/*.{test,spec}.?(c|m)[jt]s?(x)'],
      // 分层门禁（2026-09-21 用户拍板口径）：
      // 核心逻辑 services/stores 硬指标 80%；关键组件 70%；纯展示组件只做 mount 冒烟不卡线
      thresholds: {
        'src/services/**': { statements: 80, lines: 80 },
        'src/stores/**': { statements: 80, lines: 80 },
        'src/components/stream/VideoPlayer.vue': { statements: 70, lines: 70 },
        'src/components/debug/LogcatView.vue': { statements: 70, lines: 70 },
        'src/components/debug/DebugPanel.vue': { statements: 70, lines: 70 },
      },
    },
  },
})