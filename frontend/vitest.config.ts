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
  },
})