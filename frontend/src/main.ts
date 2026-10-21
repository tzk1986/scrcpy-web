/**
 * 前端应用入口文件
 * ==================
 *
 * 初始化 Vue 3 应用并注册全局插件：
 *   - Pinia：状态管理（替代 Vuex）
 *   - Vue Router：路由管理
 *   - Element Plus：UI 组件库
 *
 * 全局错误兜底（方案 51 F2-07）：Vue errorHandler 与 window
 * onunhandledrejection 都先 console 全量记录（排障依据不丢），再按
 * 7s debounce 出一条轻提示——同类异常连发时只提示一次，防刷屏。
 *
 * 挂载目标：#app（index.html 中的 div#app）
 */

import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus, { ElMessage } from 'element-plus'
import 'element-plus/dist/index.css'

import App from './App.vue'
import router from './router'

const app = createApp(App)

app.use(createPinia())
app.use(router)
app.use(ElementPlus)

/** 同类异常连发只出一条轻提示的 debounce 窗口（ms） */
const ERROR_TOAST_DEBOUNCE_MS = 7000
let lastErrorToast = { text: '', at: 0 }

function toastErrorOnce(text: string) {
  const now = Date.now()
  if (lastErrorToast.text === text && now - lastErrorToast.at < ERROR_TOAST_DEBOUNCE_MS) return
  lastErrorToast = { text, at: now }
  ElMessage.error(text)
}

app.config.errorHandler = (err) => {
  console.error('[App] Unhandled error:', err)
  toastErrorOnce('页面发生异常，如持续出现请刷新重试')
}

window.addEventListener('unhandledrejection', (event) => {
  console.error('[App] Unhandled rejection:', event.reason)
  toastErrorOnce('后台请求异常，如持续出现请刷新重试')
})

app.mount('#app')
