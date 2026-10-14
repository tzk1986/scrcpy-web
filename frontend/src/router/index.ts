/**
 * Vue Router 路由配置
 * =====================
 *
 * 定义应用的路由表：
 *   /                   → Dashboard.vue（设备列表）
 *   /device/:id         → DeviceDetail.vue（设备详情 + 调试面板）
 *   /device/:id/files   → FileManager.vue（设备文件管理，方案 39）
 *   /settings           → Settings.vue（独立设置页，方案 45）
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
    {
      // 独立设置页（方案 45 T5；T8 支持 ?device=<id> 设备级覆盖）
      path: '/settings',
      name: 'settings',
      component: () => import('@/views/Settings.vue'),
    },
    {
      // 录屏产物独立页（方案 46）：历史列表/播放/下载/删除；?device= 过滤
      path: '/recordings',
      name: 'recordings',
      component: () => import('@/views/Recordings.vue'),
    },
  ],
})

export default router
