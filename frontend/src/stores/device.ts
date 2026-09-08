/**
 * 设备状态管理（Pinia Store）
 * ==============================
 *
 * 管理设备列表的全局状态：
 *   - devices: 设备列表
 *   - loading: 加载状态
 *   - error: 错误信息
 *
 * 提供的方法：
 *   - fetchDevices(): 从后端获取设备列表
 *   - installApk(): 在设备上安装 APK
 *
 * 使用方式（在 Vue 组件中）：
 *   import { useDeviceStore } from '@/stores/device'
 *   const store = useDeviceStore()
 *   await store.fetchDevices()
 */

import { defineStore } from 'pinia'
import { ref } from 'vue'
import { api } from '@/services/api'

/**
 * 设备信息接口。
 * 与后端 DeviceInfo 数据类对应。
 */
export interface DeviceInfo {
  id: string
  model: string
  os_version: string
  resolution: [number, number]
  battery: number
  status: string
  ip?: string
  port?: number
}

/**
 * 设备 Store。
 * 使用 Composition API 风格（setup 函数）。
 */
export const useDeviceStore = defineStore('device', () => {
  const devices = ref<DeviceInfo[]>([])
  const loading = ref(false)
  const error = ref<string | null>(null)

  /**
   * 从后端获取设备列表。
   * 设置 loading 状态，捕获错误。
   */
  async function fetchDevices() {
    loading.value = true
    error.value = null
    try {
      devices.value = await api.listDevices()
    } catch (e) {
      error.value = (e as Error).message
    } finally {
      loading.value = false
    }
  }

  /**
   * 在指定设备上安装 APK。
   */
  async function installApk(deviceId: string, apkPath: string) {
    return await api.installApk(deviceId, apkPath)
  }

  return { devices, loading, error, fetchDevices, installApk }
})
