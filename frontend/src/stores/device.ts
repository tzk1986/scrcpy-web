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
 *   - connectDevice(): 通过 TCP/IP 连接设备
 *   - disconnectDevice(): 断开设备连接
 *   - installApk(): 在设备上安装 APK
 *   - startSSE(): 启动 SSE 实时事件监听
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
  const eventSource = ref<EventSource | null>(null)

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
   * 通过 TCP/IP 连接到设备。
   * 连接成功后自动刷新设备列表。
   *
   * @param ip - 设备 IP 地址
   * @param port - ADB 端口（默认 5555）
   */
  async function connectDevice(ip: string, port = 5555) {
    error.value = null
    try {
      const result = await api.connectDevice(ip, port)
      if (result.success) {
        // 等待一下让设备完全连接
        await new Promise(resolve => setTimeout(resolve, 1000))
        await fetchDevices()
      }
      return result
    } catch (e) {
      error.value = (e as Error).message
      throw e
    }
  }

  /**
   * 断开设备的 TCP/IP 连接。
   * 断开后自动刷新设备列表。
   *
   * @param deviceId - 设备 ID（格式为 "ip:port"）
   */
  async function disconnectDevice(deviceId: string) {
    error.value = null
    try {
      const result = await api.disconnectDevice(deviceId)
      if (result.success) {
        await fetchDevices()
      }
      return result
    } catch (e) {
      error.value = (e as Error).message
      throw e
    }
  }

  /**
   * 启动 SSE 实时设备事件监听。
   * 自动处理设备连接/断开事件，实时更新设备列表。
   */
  function startSSE() {
    // 如果已有连接，先关闭
    stopSSE()

    const wsProtocol = window.location.protocol === 'https:' ? 'https:' : 'http:'
    const sseUrl = `${wsProtocol}//${window.location.host}/api/devices/events`

    const es = new EventSource(sseUrl)
    eventSource.value = es

    es.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data)
        if (data.type === 'connected') {
          // 新设备连接，添加到列表
          const device = data.device as DeviceInfo
          const exists = devices.value.find(d => d.id === device.id)
          if (!exists) {
            devices.value.push(device)
          }
        } else if (data.type === 'disconnected') {
          // 设备断开，从列表移除
          devices.value = devices.value.filter(d => d.id !== data.device_id)
        }
      } catch (e) {
        console.error('Failed to parse SSE event:', e)
      }
    }

    es.onerror = () => {
      console.error('SSE connection error')
      // EventSource 会自动重连，不需要手动处理
    }
  }

  /**
   * 停止 SSE 实时设备事件监听。
   */
  function stopSSE() {
    if (eventSource.value) {
      eventSource.value.close()
      eventSource.value = null
    }
  }

  /**
   * 在指定设备上安装 APK。
   */
  async function installApk(deviceId: string, apkPath: string) {
    return await api.installApk(deviceId, apkPath)
  }

  /**
   * 获取单个设备信息。
   */
  async function getDevice(deviceId: string): Promise<DeviceInfo | null> {
    try {
      return await api.getDevice(deviceId)
    } catch {
      return null
    }
  }

  return {
    devices,
    loading,
    error,
    fetchDevices,
    connectDevice,
    disconnectDevice,
    startSSE,
    stopSSE,
    installApk,
    getDevice,
  }
})
