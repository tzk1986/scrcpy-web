/**
 * 前端应用入口文件
 * ==================
 *
 * 初始化 Vue 3 应用并注册全局插件：
 *   - Pinia：状态管理（替代 Vuex）
 *   - Vue Router：路由管理
 *   - Element Plus：UI 组件库
 *
 * 挂载目标：#app（index.html 中的 div#app）
 */

import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'

import App from './App.vue'
import router from './router'

const app = createApp(App)

app.use(createPinia())
app.use(router)
app.use(ElementPlus)

app.mount('#app')
