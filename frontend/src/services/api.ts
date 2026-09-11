/**
 * HTTP API 服务
 * ===============
 *
 * 封装所有与后端的 HTTP 通信。
 * 使用 axios 作为 HTTP 客户端，baseURL 为 /api（通过 Vite 代理到后端 8000 端口）。
 *
 * API 分组：
 *   - 设备 API：listDevices, getDevice, installApk
 *   - 调试 API：createDebugSession, getDebugSession, getLogs, execShell, closeDebugSession
 *
 * 使用方式：
 *   import { api } from '@/services/api'
 *   const devices = await api.listDevices()
 */

import axios from 'axios'

// 创建 axios 实例，配置基础 URL 和超时
const client = axios.create({
  baseURL: '/api',
  timeout: 30000,  // 30 秒超时
})

/**
 * API 方法集合。
 * 每个方法对应后端的一个 RESTful 端点。
 */
export const api = {
  // ==================== 设备 API ====================

  /**
   * 列出所有连接的设备。
   * 对应：GET /api/devices
   */
  async listDevices() {
    const res = await client.get('/devices')
    return res.data
  },

  /**
   * 获取指定设备的详细信息。
   * 对应：GET /api/devices/{deviceId}
   */
  async getDevice(deviceId: string) {
    const res = await client.get(`/devices/${deviceId}`)
    return res.data
  },

  /**
   * 在设备上安装 APK。
   * 对应：POST /api/devices/{deviceId}/install?apk_path=...
   */
  async installApk(deviceId: string, apkPath: string) {
    const res = await client.post(`/devices/${deviceId}/install`, null, {
      params: { apk_path: apkPath },
    })
    return res.data
  },

  /**
   * 通过 TCP/IP 连接到设备。
   * 对应：POST /api/devices/connect?ip=...&port=...
   */
  async connectDevice(ip: string, port = 5555) {
    const res = await client.post('/devices/connect', null, {
      params: { ip, port },
    })
    return res.data as { success: boolean; device_id: string }
  },

  /**
   * 断开设备的 TCP/IP 连接。
   * 对应：POST /api/devices/{deviceId}/disconnect
   */
  async disconnectDevice(deviceId: string) {
    const res = await client.post(`/devices/${encodeURIComponent(deviceId)}/disconnect`)
    return res.data as { success: boolean }
  },

  // ==================== 调试 API ====================

  /**
   * 创建调试会话。
   * 对应：POST /api/debug/sessions?device_id=...&user_id=...
   */
  async createDebugSession(deviceId: string, userId: string) {
    const res = await client.post('/debug/sessions', null, {
      params: { device_id: deviceId, user_id: userId },
    })
    return res.data
  },

  /**
   * 获取调试会话信息。
   * 对应：GET /api/debug/sessions/{sessionId}
   */
  async getDebugSession(sessionId: string) {
    const res = await client.get(`/debug/sessions/${sessionId}`)
    return res.data
  },

  /**
   * 查询调试日志。
   * 对应：GET /api/debug/sessions/{sessionId}/logs?level=...&tag=...&limit=...
   */
  async getLogs(
    sessionId: string,
    level?: string | null,
    tag?: string | null,
    limit = 1000
  ) {
    const res = await client.get(`/debug/sessions/${sessionId}/logs`, {
      params: { level, tag, limit },
    })
    return res.data
  },

  /**
   * 导出调试日志为文件。
   * 对应：GET /api/debug/sessions/{sessionId}/logs/export?format=json|csv
   * 返回 Blob 数据，前端需要创建下载链接。
   */
  async exportLogs(
    sessionId: string,
    format: 'json' | 'csv' = 'json',
    level?: string | null,
    tag?: string | null,
    limit = 50000
  ): Promise<Blob> {
    const res = await client.get(`/debug/sessions/${sessionId}/logs/export`, {
      params: { format, level, tag, limit },
      responseType: 'blob',
    })
    return res.data as Blob
  },

  /**
   * 手动触发日志清理。
   * 对应：POST /api/debug/cleanup
   */
  async runCleanup() {
    const res = await client.post('/debug/cleanup')
    return res.data
  },

  /**
   * 清理指定会话的所有日志。
   * 对应：DELETE /api/debug/sessions/{sessionId}/logs
   */
  async cleanupSessionLogs(sessionId: string) {
    const res = await client.delete(`/debug/sessions/${sessionId}/logs`)
    return res.data
  },

  /**
   * 获取调试系统统计信息。
   * 对应：GET /api/debug/stats
   */
  async getDebugStats() {
    const res = await client.get('/debug/stats')
    return res.data
  },

  /**
   * 执行 shell 命令。
   * 对应：POST /api/debug/sessions/{sessionId}/shell?command=...
   */
  async execShell(sessionId: string, command: string) {
    const res = await client.post(`/debug/sessions/${sessionId}/shell`, null, {
      params: { command },
    })
    return res.data
  },

  /**
   * 关闭调试会话。
   * 对应：DELETE /api/debug/sessions/{sessionId}
   */
  async closeDebugSession(sessionId: string) {
    const res = await client.delete(`/debug/sessions/${sessionId}`)
    return res.data
  },

  // ==================== 截屏 API ====================

  /**
   * 获取设备截屏（PNG 格式）。
   * 对应：GET /api/devices/{deviceId}/screenshot
   * 返回 Blob 数据。
   */
  async screenshot(deviceId: string): Promise<Blob> {
    const res = await client.get(`/devices/${deviceId}/screenshot`, {
      responseType: 'blob',
    })
    return res.data as Blob
  },

  // ==================== 性能监控 API ====================

  /**
   * 获取设备历史性能指标。
   * 对应：GET /api/perf/{deviceId}/metrics?limit=...
   */
  async getPerfMetrics(deviceId: string, limit = 100) {
    const res = await client.get(`/perf/${encodeURIComponent(deviceId)}/metrics`, {
      params: { limit },
    })
    return res.data
  },

  /**
   * 启动性能监控。
   * 对应：POST /api/perf/{deviceId}/start?interval=...
   */
  async startPerfMonitoring(deviceId: string, interval = 1.0) {
    const res = await client.post(`/perf/${encodeURIComponent(deviceId)}/start`, null, {
      params: { interval },
    })
    return res.data
  },

  /**
   * 停止性能监控。
   * 对应：POST /api/perf/{deviceId}/stop
   */
  async stopPerfMonitoring(deviceId: string) {
    const res = await client.post(`/perf/${encodeURIComponent(deviceId)}/stop`)
    return res.data
  },

  // ==================== 应用管理 API ====================

  /**
   * 获取设备应用列表。
   * 对应：GET /api/apps/{deviceId}?include_system=...
   */
  async listApps(deviceId: string, includeSystem = false) {
    const res = await client.get(`/apps/${encodeURIComponent(deviceId)}`, {
      params: { include_system: includeSystem },
    })
    return res.data
  },

  /**
   * 获取应用详情。
   * 对应：GET /api/apps/{deviceId}/{package}
   */
  async getAppInfo(deviceId: string, packageName: string) {
    const res = await client.get(`/apps/${encodeURIComponent(deviceId)}/${packageName}`)
    return res.data
  },

  /**
   * 启动应用。
   * 对应：POST /api/apps/{deviceId}/{package}/launch
   */
  async launchApp(deviceId: string, packageName: string) {
    const res = await client.post(`/apps/${encodeURIComponent(deviceId)}/${packageName}/launch`)
    return res.data
  },

  /**
   * 强制停止应用。
   * 对应：POST /api/apps/{deviceId}/{package}/stop
   */
  async stopApp(deviceId: string, packageName: string) {
    const res = await client.post(`/apps/${encodeURIComponent(deviceId)}/${packageName}/stop`)
    return res.data
  },

  /**
   * 卸载应用。
   * 对应：POST /api/apps/{deviceId}/{package}/uninstall
   */
  async uninstallApp(deviceId: string, packageName: string) {
    const res = await client.post(`/apps/${encodeURIComponent(deviceId)}/${packageName}/uninstall`)
    return res.data
  },

  /**
   * 清除应用数据。
   * 对应：POST /api/apps/{deviceId}/{package}/clear-data
   */
  async clearAppData(deviceId: string, packageName: string) {
    const res = await client.post(`/apps/${encodeURIComponent(deviceId)}/${packageName}/clear-data`)
    return res.data
  },

  /**
   * 获取应用内存占用。
   * 对应：GET /api/apps/{deviceId}/{package}/memory
   */
  async getAppMemory(deviceId: string, packageName: string) {
    const res = await client.get(`/apps/${encodeURIComponent(deviceId)}/${packageName}/memory`)
    return res.data
  },

  // ==================== 网络监控 API ====================

  /**
   * 获取当前网络统计。
   * 对应：GET /api/network/{deviceId}/stats
   */
  async getNetworkStats(deviceId: string) {
    const res = await client.get(`/network/${encodeURIComponent(deviceId)}/stats`)
    return res.data
  },

  /**
   * 获取活跃连接列表。
   * 对应：GET /api/network/{deviceId}/connections?protocol=...
   */
  async getNetworkConnections(deviceId: string, protocol?: string) {
    const res = await client.get(`/network/${encodeURIComponent(deviceId)}/connections`, {
      params: { protocol },
    })
    return res.data
  },
}
