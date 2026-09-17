import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'path'

// 后端端口：与后端共用 BACKEND_PORT 环境变量（默认 8765，见 config/settings.py）
const backendPort = process.env.BACKEND_PORT || '8765'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src'),
    },
  },
  server: {
    port: 8080,
    strictPort: true,
    proxy: {
      '/api': {
        target: `http://localhost:${backendPort}`,
        changeOrigin: true,
      },
      '/ws': {
        target: `ws://localhost:${backendPort}`,
        ws: true,
      },
    },
  },
  build: {
    target: 'chrome114',
  },
  optimizeDeps: {
    exclude: ['xterm', 'monaco-editor'],
  },
})
