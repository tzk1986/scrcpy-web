/**
 * 调试状态管理（Pinia Store）
 * ==============================
 *
 * 管理调试会话的全局状态：
 *   - sessionId: 当前活跃的调试会话 ID
 *   - logs: 日志条目列表（内存缓冲区，最多 50000 条）
 *   - filter: 日志过滤条件（level, tag）
 *   - connected: 是否已连接到调试会话
 *
 * 提供的方法：
 *   - createSession(): 创建新的调试会话
 *   - fetchLogs(): 从后端获取日志
 *   - execShell(): 执行 shell 命令
 *   - closeSession(): 关闭调试会话
 *   - appendLog(): 向日志缓冲区追加一条记录
 *
 * 使用方式（在 Vue 组件中）：
 *   import { useDebugStore } from '@/stores/debug'
 *   const debugStore = useDebugStore()
 *   await debugStore.createSession(deviceId, userId)
 */

import { defineStore } from 'pinia'
import { ref } from 'vue'
import { api } from '@/services/api'

/**
 * 日志条目接口。
 * 与后端 LogEntry 数据类对应。
 */
export interface LogEntry {
  ts: number      // Unix 时间戳（秒）
  level: string   // 日志级别：V/D/I/W/E/F
  pid: number     // 进程 ID
  tid: number     // 线程 ID
  tag: string     // 日志标签
  message: string // 日志消息
}

/**
 * 调试 Store。
 * 管理调试会话的生命周期和日志数据。
 */
export const useDebugStore = defineStore('debug', () => {
  const sessionId = ref<string | null>(null)
  const logs = ref<LogEntry[]>([])
  const filter = ref({
    level: null as string | null,
    tag: null as string | null,
  })
  const connected = ref(false)

  /**
   * 创建调试会话。
   * 设置 sessionId 和 connected 状态。
   */
  async function createSession(deviceId: string, userId: string) {
    const res = await api.createDebugSession(deviceId, userId)
    sessionId.value = res.session_id
    connected.value = true
    return res
  }

  /**
   * 从后端获取日志。
   * 应用当前的过滤条件（level, tag）。
   */
  async function fetchLogs(limit = 1000) {
    if (!sessionId.value) return
    const res = await api.getLogs(
      sessionId.value,
      filter.value.level,
      filter.value.tag,
      limit
    )
    logs.value = res.logs
  }

  /**
   * 执行 shell 命令。
   */
  async function execShell(cmd: string) {
    if (!sessionId.value) return
    return await api.execShell(sessionId.value, cmd)
  }

  /**
   * 关闭调试会话。
   * 清理状态（sessionId, logs, connected）。
   */
  async function closeSession() {
    if (!sessionId.value) return
    await api.closeDebugSession(sessionId.value)
    sessionId.value = null
    logs.value = []
    connected.value = false
  }

  /**
   * 向日志缓冲区追加一条记录。
   * 超过 50000 条时，移除最旧的记录（FIFO）。
   */
  function appendLog(entry: LogEntry) {
    logs.value.push(entry)
    if (logs.value.length > 50000) {
      logs.value.shift()
    }
  }

  return {
    sessionId,
    logs,
    filter,
    connected,
    createSession,
    fetchLogs,
    execShell,
    closeSession,
    appendLog,
  }
})
