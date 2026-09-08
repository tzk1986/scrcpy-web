/**
 * Vue Router 路由配置
 * =====================
 *
 * 定义应用的路由表：
 *   /              → Dashboard.vue（设备列表）
 *   /device/:id    → DeviceDetail.vue（设备详情 + 调试面板）
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
  ],
})

export default router
