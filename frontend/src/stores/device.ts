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
 *   - scanSubnet(): 网段扫描并批量连接（方案 36）
 *   - loadThumbnail()/loadThumbnails(): 设备屏幕缩略图（TTL 缓存 + 并发池，方案 36）
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
import { api, type ScanResult } from '@/services/api'

// 缩略图缓存 TTL（方案 36 D5）：命中直接显示，过期/缺失才重拉
const THUMBNAIL_TTL = 30_000

// 批量缩略图并发上限（方案 36 D5）：同时最多 3 台 screencap，对设备温和
const THUMBNAIL_CONCURRENCY = 3

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
  /**
   * 瞬态展示字段（方案 35 D7）：该设备本轮信息查询失败（最常见原因为
   * adb 响应慢），当前信息可能为缓存/默认值。光源为列表响应即出即用。
   */
  slow?: boolean
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

  // 缩略图缓存（方案 36 D5）：deviceId → {objectURL, 快照时刻}。
  // 响应式 plain object（非裸 Map——Vue 不跟踪裸 Map 增改）
  const thumbnails = ref<Record<string, { url: string; ts: number }>>({})
  const thumbLoading = ref<Record<string, boolean>>({})
  const thumbFailed = ref<Record<string, boolean>>({})

  // 同设备 in-flight 去重（非响应式，仅请求合并用）
  const thumbInFlight = new Map<string, Promise<void>>()
  // 并发池（自建 3 槽队列，in-flight 数可观测以支撑测试断言）
  let thumbActive = 0
  const thumbQueue: Array<() => void> = []

  // 请求代次（方案 35 D6）：并发重拉时丢弃过期响应，防止慢响应
  // 覆盖新状态（例如把刚本地移除的断开设备复活）
  let fetchGeneration = 0

  /**
   * 从后端获取设备列表。
   * 设置 loading 状态，捕获错误。
   *
   * @param opts.silent - 静默刷新：不置列表 loading（用于断开/连接后的
   *   兜底重拉，不阻塞用户操作关键路径）
   */
  async function fetchDevices(opts: { silent?: boolean } = {}) {
    const generation = ++fetchGeneration
    if (!opts.silent) loading.value = true
    error.value = null
    try {
      const list = await api.listDevices()
      if (generation !== fetchGeneration) return // 过期响应：丢弃
      devices.value = list
    } catch (e) {
      if (generation !== fetchGeneration) return
      error.value = (e as Error).message
    } finally {
      if (!opts.silent) loading.value = false
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
        // 静默重拉补齐设备行（方案 35 D6）：不 await，对话框立即关闭；
        // fetchDevices 内部不抛出，无 unhandled rejection 风险
        void fetchDevices({ silent: true })
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
        // 不可达断开是「跳过 adb 清理」路径，adb 侧表项可能短暂残留；
        // 先本地移除保证立即生效，再静默重拉兜底同步（方案 35 D6：
        // 不 await，按钮立即恢复）
        devices.value = devices.value.filter(d => d.id !== deviceId)
        removeThumbnail(deviceId)
        void fetchDevices({ silent: true })
      }
      return result
    } catch (e) {
      error.value = (e as Error).message
      throw e
    }
  }

  /**
   * 网段扫描并批量连接（方案 36）。
   * 成功后静默重拉设备列表（不阻塞扫描返回，同 connectDevice 先例），
   * 并对连接成功（ok=true）的设备自动加载缩略图（D5 触发时机 1）。
   *
   * @param cidr - 目标网段（IPv4，/22-/32）
   * @param connect - 是否对开放主机执行 adb 连接（默认 true）
   * @param port - ADB 端口（默认 5555）
   */
  async function scanSubnet(cidr: string, connect = true, port = 5555): Promise<ScanResult> {
    error.value = null
    try {
      const result = await api.scanSubnet(cidr, connect, port)
      void fetchDevices({ silent: true })
      const okIds = result.connect_results
        .filter((r) => r.ok && r.device_id)
        .map((r) => r.device_id as string)
      void loadThumbnails(okIds)
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
          // 设备断开，从列表移除并清理缩略图（D5 revoke 策略）
          devices.value = devices.value.filter(d => d.id !== data.device_id)
          removeThumbnail(data.device_id)
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

  // -------------------- 缩略图缓存（方案 36 D5） --------------------

  /**
   * 删除单台设备的缩略图条目并 revoke objectURL。
   * 设备从列表移除（断开动作 / SSE 断开事件）时调用。
   */
  function removeThumbnail(deviceId: string) {
    const entry = thumbnails.value[deviceId]
    if (entry) {
      URL.revokeObjectURL(entry.url)
      const next = { ...thumbnails.value }
      delete next[deviceId]
      thumbnails.value = next
    }
    if (thumbLoading.value[deviceId]) {
      const next = { ...thumbLoading.value }
      delete next[deviceId]
      thumbLoading.value = next
    }
    if (thumbFailed.value[deviceId]) {
      const next = { ...thumbFailed.value }
      delete next[deviceId]
      thumbFailed.value = next
    }
  }

  /** 获取一只并发槽（≥3 在途时排队），与 releaseThumbSlot 配对。 */
  function acquireThumbSlot(): Promise<void> {
    if (thumbActive < THUMBNAIL_CONCURRENCY) {
      thumbActive++
      return Promise.resolve()
    }
    return new Promise((resolve) => {
      thumbQueue.push(() => {
        thumbActive++
        resolve()
      })
    })
  }

  /** 释放一只并发槽，唤醒队首等待者。 */
  function releaseThumbSlot() {
    thumbActive--
    thumbQueue.shift()?.()
  }

  /**
   * 加载单台设备屏幕缩略图（点击加载 / 扫描后自动加载 / 重试共用）。
   *
   * - TTL 命中（30s 内已成功加载）直接返回，不重拉；force=true 绕过（刷新场景）；
   * - 同设备 in-flight 请求去重合并（重复点击不重复请求）；
   * - 失败不写入 TTL 缓存（下次点击直接重拉），仅置 thumbFailed 失败态；
   * - 换新前 revoke 旧 objectURL；本函数不抛出（失败转失败态）。
   */
  function loadThumbnail(deviceId: string, force = false): Promise<void> {
    const existing = thumbInFlight.get(deviceId)
    if (existing) return existing
    if (!force) {
      const entry = thumbnails.value[deviceId]
      if (entry && Date.now() - entry.ts < THUMBNAIL_TTL) return Promise.resolve()
    }
    const task = (async () => {
      thumbLoading.value = { ...thumbLoading.value, [deviceId]: true }
      if (thumbFailed.value[deviceId]) {
        const next = { ...thumbFailed.value }
        delete next[deviceId]
        thumbFailed.value = next
      }
      await acquireThumbSlot()
      try {
        const blob = await api.screenshot(deviceId)
        const url = URL.createObjectURL(blob)
        const old = thumbnails.value[deviceId]
        thumbnails.value = { ...thumbnails.value, [deviceId]: { url, ts: Date.now() } }
        if (old) URL.revokeObjectURL(old.url)
      } catch {
        thumbFailed.value = { ...thumbFailed.value, [deviceId]: true }
      } finally {
        const nextLoading = { ...thumbLoading.value }
        delete nextLoading[deviceId]
        thumbLoading.value = nextLoading
        releaseThumbSlot()
        thumbInFlight.delete(deviceId)
      }
    })()
    thumbInFlight.set(deviceId, task)
    return task
  }

  /** 批量加载缩略图（扫描后 ok 集合 / 刷新全部），并发池全局限 3。 */
  function loadThumbnails(deviceIds: string[], force = false): Promise<void[]> {
    return Promise.all(deviceIds.map((id) => loadThumbnail(id, force)))
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
    thumbnails,
    thumbLoading,
    thumbFailed,
    fetchDevices,
    connectDevice,
    disconnectDevice,
    scanSubnet,
    loadThumbnail,
    loadThumbnails,
    startSSE,
    stopSSE,
    installApk,
    getDevice,
  }
})
