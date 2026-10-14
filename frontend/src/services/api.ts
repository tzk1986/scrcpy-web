/**
 * HTTP API 服务
 * ===============
 *
 * 封装所有与后端的 HTTP 通信。
 * 使用 axios 作为 HTTP 客户端，baseURL 为 /api（通过 Vite 代理到后端 8765 端口）。
 *
 * API 分组：
 *   - 设备 API：listDevices, getDevice, installApk, connectDevice, disconnectDevice, scanSubnet
 *   - 调试 API：createDebugSession, getDebugSession, getLogs, execShell, closeDebugSession
 *
 * 使用方式：
 *   import { api } from '@/services/api'
 *   const devices = await api.listDevices()
 */

import axios from 'axios'

/** 录制状态（后端 record/status 返回结构，方案 18 Step 3）。 */
export interface RecordStatus {
  recording: boolean
  reason: string
  rows: number
  oldest_ts: number | null
  newest_ts: number | null
}

/** 扫描中单台开放主机的一次连接尝试（方案 36，对应后端 ConnectAttempt）。 */
export interface ConnectAttempt {
  ip: string
  ok: boolean
  device_id: string | null
  reason: string | null   // 失败分类：unreachable / connect_failed
  message: string | null  // 失败摘要（异常短文本）
}

/** 网段扫描 + 批量连接结果（方案 36，对应后端 ScanResult）。 */
export interface ScanResult {
  label: string
  probed: number
  open_hosts: string[]
  connect_results: ConnectAttempt[]
  truncated: boolean
}

/** 设备端录屏会话（方案 37，对应后端 RecordingSession）。 */
export interface RecordingSession {
  id: string
  device_id: string
  started_at: number
  state: string // recording / stopping / pulling / done / failed
  remote_path: string
  local_path: string // done 后为主机落盘产物路径；空串=无产物
}

/** 单条 Service 运行时状态（方案 37 C4，对应后端 ServiceInfo）。 */
export interface RuntimeServiceInfo {
  name: string // "pkg/class" 组件名
  state: string // running / stopped / unknown
}

/** 应用运行时状态（方案 37 C4，对应后端 RuntimeInfo + parse_warning）。 */
export interface RuntimeInfo {
  pkg: string
  top_activity: string
  resumed: string
  services: RuntimeServiceInfo[]
  providers: string[]
  parse_warning: string
}

/** 设备端目录条目（方案 39，对应后端 FileEntry）。 */
export interface FileEntry {
  name: string
  is_dir: boolean
  is_symlink: boolean
  size: number
  mtime: string
  permissions: string
  link_target: string | null
}

/** 传输任务（方案 39，对应后端 TransferJob 公开形状）。 */
export interface FileJob {
  id: string
  device_id: string
  direction: 'download' | 'upload'
  paths: string[]
  total_size: number
  status: 'running' | 'done' | 'failed'
  substage: string | null
  error: string | null
}

/** 删除逐条结果（方案 39，对应后端 delete 返回项）。 */
export interface DeleteResult {
  path: string
  ok: boolean
  error: string | null
}

/** 单个设置项（方案 45 T2）：生效值 + 来源层次（出厂默认/用户设置/环境覆盖）。 */
export interface SettingEntry {
  value: number | string | boolean
  source: 'default' | 'user' | 'env'
}

/** GET /api/settings 合并视图（stream/recording 两节，键为设置项名）。 */
export interface SettingsView {
  stream: Record<string, SettingEntry>
  recording: Record<string, SettingEntry>
}

/** PUT /api/settings 补丁体：节级合并写入；节传 null = 重置该节为出厂默认。 */
export interface SettingsPatch {
  stream?: Record<string, unknown> | null
  recording?: Record<string, unknown> | null
}

/** 运行环境信息（方案 45 T6，对应后端 GET /api/system/runtime-env 只读透明化）。 */
export interface RuntimeEnvView {
  scrcpy_server_version: string // 方案 44 单源常量原值（如 "5.0.1"，展示层补 v 前缀）
  jar_path: string
  adb_path: string
  adb_version: string // adb --version 探测；失败为「未知」
  frozen: boolean // PyInstaller 冻结运行标记
}

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
      timeout: 40000, // 大于后端 adb.timeout(30s)，保证结构化错误先返回
    })
    return res.data as { success: boolean; device_id: string }
  },

  /**
   * 断开设备的 TCP/IP 连接。
   * 对应：POST /api/devices/{deviceId}/disconnect
   */
  async disconnectDevice(deviceId: string) {
    const res = await client.post(`/devices/${encodeURIComponent(deviceId)}/disconnect`, null, {
      timeout: 40000, // 大于后端 adb.timeout(30s)，保证结构化错误先返回
    })
    return res.data as { success: boolean }
  },

  /**
   * 网段扫描并批量连接（方案 36）。
   * 对应：POST /api/devices/scan?cidr=...&connect=...&port=...
   */
  async scanSubnet(cidr: string, connect = true, port = 5555): Promise<ScanResult> {
    const res = await client.post('/devices/scan', null, {
      params: { cidr, connect, port },
      // 终评 I-1 上界修正：探测 /22 ≈8s + 4 波 ×（预检 1.5s + connect 5s + info 5s）
      // ≈54s，40s 会先行中止导致「已连上却报失败 + 409 连锁」
      timeout: 60000,
    })
    return res.data as ScanResult
  },

  // ==================== 系统 API ====================

  /**
   * 请求服务优雅退出。
   * 对应：POST /api/system/shutdown
   */
  async shutdownSystem() {
    const res = await client.post('/system/shutdown')
    return res.data as { accepted: boolean; message?: string }
  },

  /**
   * 服务健康检查（退出确认轮询用）。
   * 对应：GET /health（位于 /api 前缀之外，需空 baseURL 越过）
   */
  async getHealth() {
    const res = await client.get('/health', { baseURL: '', timeout: 2000 })
    return res.data as { status: string }
  },

  /**
   * 运行环境信息（只读透明化，方案 45 T6-§4.1-③，不改动二进制获取方式）。
   * 对应：GET /api/system/runtime-env
   */
  async getRuntimeEnv(): Promise<RuntimeEnvView> {
    const res = await client.get<RuntimeEnvView>('/system/runtime-env')
    return res.data
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

  /**
   * 获取设备截屏（raw+gzip 换档链路，方案 29）。
   * 对应：GET /api/devices/{deviceId}/screenshot?format=raw
   * 后端以 X-Frame-Format 响应头标注帧格式：raw-rgba（设备端 raw 帧 +
   * gzip 透传，浏览器透明解压）或 png（raw 链路失败时后端单请求回退）。
   * X-Rotation 标注设备显示 rotation 0-3（方案 38 B5）：raw 链路前端
   * 画布/像素补偿依据；头缺失或非数字按 0（不旋转）防御。
   */
  async screenshotRaw(deviceId: string): Promise<{
    blob: Blob
    format: 'raw-rgba' | 'png'
    rotation: number
  }> {
    const res = await client.get(`/devices/${deviceId}/screenshot`, {
      params: { format: 'raw' },
      responseType: 'blob',
    })
    const format = res.headers['x-frame-format'] === 'raw-rgba' ? 'raw-rgba' : 'png'
    const rotation = Number(res.headers['x-rotation']) || 0
    return { blob: res.data as Blob, format, rotation }
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

  /**
   * 导出设备性能指标为文件（Blob）。
   * 对应：GET /api/perf/{deviceId}/export?format=...&source=...&limit=...
   * 返回 axios 响应：res.data 为 Blob，res.headers 携带
   * X-Export-Count / X-Export-Oldest-Ts / X-Export-Newest-Ts 元数据。
   */
  async exportPerfMetrics(
    deviceId: string,
    format: 'csv' | 'json' = 'csv',
    source: 'buffer' | 'cache' = 'buffer',
    limit = 50000
  ): Promise<import('axios').AxiosResponse<Blob>> {
    return client.get(`/perf/${encodeURIComponent(deviceId)}/export`, {
      params: { format, source, limit },
      responseType: 'blob',
    })
  },

  /**
   * 开启性能指标录制。
   * 对应：POST /api/perf/{deviceId}/record/start
   */
  async startPerfRecording(deviceId: string) {
    const res = await client.post(`/perf/${encodeURIComponent(deviceId)}/record/start`)
    return res.data as RecordStatus
  },

  /**
   * 停止性能指标录制。
   * 对应：POST /api/perf/{deviceId}/record/stop
   */
  async stopPerfRecording(deviceId: string) {
    const res = await client.post(`/perf/${encodeURIComponent(deviceId)}/record/stop`)
    return res.data as RecordStatus
  },

  /**
   * 查询性能指标录制状态。
   * 对应：GET /api/perf/{deviceId}/record/status
   */
  async getPerfRecordStatus(deviceId: string) {
    const res = await client.get(`/perf/${encodeURIComponent(deviceId)}/record/status`)
    return res.data as RecordStatus
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

  /**
   * 获取应用运行时状态（当前栈 / Resumed / Service / Provider）。
   * 对应：GET /api/apps/{deviceId}/{package}/runtime
   */
  async getAppRuntime(deviceId: string, packageName: string): Promise<RuntimeInfo> {
    const res = await client.get(
      `/apps/${encodeURIComponent(deviceId)}/${encodeURIComponent(packageName)}/runtime`
    )
    return res.data as RuntimeInfo
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

  /**
   * 导出设备网络统计为文件（Blob）。
   * 对应：GET /api/network/{deviceId}/export?format=...&source=...&limit=...
   * 返回 axios 响应：res.data 为 Blob，res.headers 携带
   * X-Export-Count / X-Export-Oldest-Ts / X-Export-Newest-Ts 元数据。
   */
  async exportNetworkStats(
    deviceId: string,
    format: 'csv' | 'json' = 'csv',
    source: 'buffer' | 'cache' = 'buffer',
    limit = 50000
  ): Promise<import('axios').AxiosResponse<Blob>> {
    return client.get(`/network/${encodeURIComponent(deviceId)}/export`, {
      params: { format, source, limit },
      responseType: 'blob',
    })
  },

  /**
   * 开启网络统计录制。
   * 对应：POST /api/network/{deviceId}/record/start
   */
  async startNetworkRecording(deviceId: string) {
    const res = await client.post(`/network/${encodeURIComponent(deviceId)}/record/start`)
    return res.data as RecordStatus
  },

  /**
   * 停止网络统计录制。
   * 对应：POST /api/network/{deviceId}/record/stop
   */
  async stopNetworkRecording(deviceId: string) {
    const res = await client.post(`/network/${encodeURIComponent(deviceId)}/record/stop`)
    return res.data as RecordStatus
  },

  /**
   * 查询网络统计录制状态。
   * 对应：GET /api/network/{deviceId}/record/status
   */
  async getNetworkRecordStatus(deviceId: string) {
    const res = await client.get(`/network/${encodeURIComponent(deviceId)}/record/status`)
    return res.data as RecordStatus
  },

  // ==================== 录屏 API（方案 37 B）====================

  /**
   * 启动设备端录屏。
   * 对应：POST /api/recording/{deviceId}/start
   * 单设备互斥：进行态会话冲突返回 409 RECORDING_IN_PROGRESS。
   */
  async startRecording(deviceId: string) {
    const res = await client.post(`/recording/${encodeURIComponent(deviceId)}/start`)
    return res.data as { session: RecordingSession }
  },

  /**
   * 停止录屏并收尾。
   * 对应：POST /api/recording/{deviceId}/stop
   * 返回终态会话（done 时携带主机落盘路径 local_path）。
   */
  async stopRecording(deviceId: string) {
    const res = await client.post(`/recording/${encodeURIComponent(deviceId)}/stop`)
    return res.data as { session: RecordingSession; file_size?: number | null }
  },

  /**
   * 查询当前录屏会话。
   * 对应：GET /api/recording/{deviceId}
   */
  async getRecording(deviceId: string) {
    const res = await client.get(`/recording/${encodeURIComponent(deviceId)}`)
    return res.data as { session: RecordingSession; file_size?: number | null }
  },

  /**
   * 列出进行态录屏会话（刷新恢复状态用）。
   * 对应：GET /api/recording
   * 注意：URL 必须无尾斜杠——SPA fallback catch-all 会 404 带尾斜杠的
   * 路径（/api/devices/ 同理，Task 8 评审实证硬约束）。
   */
  async listRecordings() {
    const res = await client.get('/recording')
    return res.data as { recordings: RecordingSession[] }
  },

  // ==================== 文件管理 API（方案 39）====================

  /**
   * 设备目录列表。
   * 对应：GET /api/devices/{deviceId}/files?path=
   */
  async listFiles(deviceId: string, path: string): Promise<FileEntry[]> {
    const res = await client.get(`/devices/${encodeURIComponent(deviceId)}/files`, {
      params: { path },
    })
    return res.data as FileEntry[]
  },

  /**
   * 下载文件（单=原文件、多=zip）。
   * 对应：GET /api/devices/{deviceId}/file/download?paths=..&paths=..
   * paths 手动拼重复查询串（axios 默认序列化为 paths[]=，后端 Query list
   * 需裸重复键）。超时关闭（大文件）；onProgress 为 HTTP 段真实百分比。
   * 返回 blob 与任务头 X-File-Job。
   */
  async downloadFiles(
    deviceId: string,
    paths: string[],
    onProgress?: (percent: number) => void
  ): Promise<{ blob: Blob; jobId: string | null }> {
    const q = paths.map((p) => `paths=${encodeURIComponent(p)}`).join('&')
    const res = await client.get<Blob>(
      `/devices/${encodeURIComponent(deviceId)}/file/download?${q}`,
      {
        responseType: 'blob',
        timeout: 0,
        onDownloadProgress: (e: { loaded: number; total?: number }) => {
          if (onProgress) {
            const total = e.total ?? 0
            if (total > 0) onProgress(Math.round((e.loaded / total) * 100))
          }
        },
      }
    )
    const jobId = (res.headers?.['x-file-job'] ?? null) as string | null
    return { blob: res.data, jobId }
  },

  /**
   * 上传单文件（多文件由调用方顺序循环——设备互斥单飞）。
   * 对应：POST /api/devices/{deviceId}/file/upload?dir=…（multipart 字段名 file）
   */
  async uploadFile(
    deviceId: string,
    dir: string,
    file: File,
    onProgress?: (percent: number) => void
  ): Promise<FileJob> {
    const form = new FormData()
    form.append('file', file)
    const res = await client.post<FileJob>(
      `/devices/${encodeURIComponent(deviceId)}/file/upload`,
      form,
      {
        params: { dir },
        timeout: 0,
        onUploadProgress: (e: { loaded: number; total?: number }) => {
          if (onProgress) {
            const total = e.total ?? file.size
            if (total > 0) onProgress(Math.round((e.loaded / total) * 100))
          }
        },
      }
    )
    return res.data
  },

  /**
   * 设备端重命名（同目录）。
   * 对应：POST /api/devices/{deviceId}/file/rename {path,new_name}
   */
  async renameFile(deviceId: string, path: string, newName: string): Promise<void> {
    await client.post(`/devices/${encodeURIComponent(deviceId)}/file/rename`, {
      path,
      new_name: newName,
    })
  },

  /**
   * 批量删除（逐条结果）。
   * 对应：POST /api/devices/{deviceId}/file/delete {paths}
   */
  async deleteFiles(deviceId: string, paths: string[]): Promise<DeleteResult[]> {
    const res = await client.post<{ results: DeleteResult[] }>(
      `/devices/${encodeURIComponent(deviceId)}/file/delete`,
      { paths }
    )
    return res.data.results
  },

  /**
   * 查询传输任务状态。
   * 对应：GET /api/devices/{deviceId}/file-jobs/{jobId}
   */
  async getFileJob(deviceId: string, jobId: string): Promise<FileJob> {
    const res = await client.get<FileJob>(
      `/devices/${encodeURIComponent(deviceId)}/file-jobs/${encodeURIComponent(jobId)}`
    )
    return res.data
  },

  // ==================== 设置 API（方案 45）====================

  /**
   * 读取设置合并视图（生效值 + 来源）。
   * 对应：GET /api/settings
   */
  async getSettings(): Promise<SettingsView> {
    const res = await client.get<SettingsView>('/settings')
    return res.data
  },

  /**
   * 写入用户设置（节级合并；节传 null = 重置该节为出厂默认）。
   * 对应：PUT /api/settings
   */
  async putSettings(patch: SettingsPatch): Promise<{ saved: boolean }> {
    const res = await client.put<{ saved: boolean }>('/settings', patch)
    return res.data
  },
}
