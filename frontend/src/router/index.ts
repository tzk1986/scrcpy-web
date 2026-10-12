/**
 * Vue Router 路由配置
 * =====================
 *
 * 定义应用的路由表：
 *   /                   → Dashboard.vue（设备列表）
 *   /device/:id         → DeviceDetail.vue（设备详情 + 调试面板）
 *   /device/:id/files   → FileManager.vue（设备文件管理，方案 39）
 *
 * 路由模式：HTML5 History（无 # 号）
 * 懒加载：所有页面组件使用 import() 动态导入，优化首屏加载速度
 */

import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'dashboard',
      component: () => import('@/views/Dashboard.vue'),
    },
    {
      path: '/device/:id',
      name: 'device-detail',
      component: () => import('@/views/DeviceDetail.vue'),
      props: true,  // 将路由参数 :id 作为 props 传给组件
    },
    {
      // 设备文件管理独立页（方案 39 T5）；置于 /device/:id 之后——
      // vue-router 静态段优先，/device/x/files 不会误入详情页路由
      path: '/device/:id/files',
      name: 'device-files',
      component: () => import('@/views/FileManager.vue'),
      props: true,  // 将路由参数 :id 作为 props 传给组件
    },
  ],
})

export default router
