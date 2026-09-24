<!--
  Logcat 日志查看器
  ===================

  实时显示 Android 设备的 logcat 日志，类似 Chrome DevTools Console。

  功能：
    - 录制开关：开启/暂停接收新日志（默认暂停）
    - 日志级别过滤（V/D/I/W/E/F）
    - 关键词搜索（匹配 Tag 或 Message，不区分大小写）
    - 实时日志推送（通过 WebSocket）
    - 自动滚动：新日志时自动滚动到底部
    - 点击日志复制到剪贴板
    - 导出、清理、清空

  性能优化：
    - 使用 computed 缓存过滤结果
    - 搜索框使用防抖（300ms）减少频繁过滤
    - 预计算小写字符串加速匹配
    - 暂停录制时后端停止推送，减少客户端处理

  日志显示格式：
    [时间戳] [级别] [标签] 消息内容

  颜色编码：
    V (Verbose): 灰色
    D (Debug):   蓝色
    I (Info):    绿色
    W (Warning): 橙色
    E (Error):   红色
    F (Fatal):   紫色

  数据来源：
    使用 useDebugStore 管理日志数据和过滤条件。
    通过 WebSocket 接收实时日志推送。
-->
<template>
  <div class="logcat-view">
    <div class="toolbar">
      <!-- 录制开关 -->
      <el-button
        :type="debugStore.isRecording ? 'success' : 'info'"
        size="small"
        @click="toggleRecording"
        class="record-btn"
      >
        <span v-if="debugStore.isRecording">⏸ 暂停</span>
        <span v-else>▶ 开始</span>
      </el-button>

      <!-- 级别过滤 -->
      <el-select v-model="filterLevel" placeholder="级别" clearable size="small" class="filter-select">
        <el-option label="Verbose" value="V" />
        <el-option label="Debug" value="D" />
        <el-option label="Info" value="I" />
        <el-option label="Warning" value="W" />
        <el-option label="Error" value="E" />
        <el-option label="Fatal" value="F" />
      </el-select>

      <!-- 搜索框 -->
      <el-input
        v-model="searchInput"
        placeholder="搜索 Tag 或消息..."
        clearable
        size="small"
        class="search-input"
      />

      <!-- 操作按钮 -->
      <el-button size="small" @click="refresh">刷新</el-button>
      <el-button size="small" @click="clear">清空</el-button>
      <el-dropdown size="small" @command="exportLogs" trigger="click">
        <el-button size="small">导出</el-button>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item command="json">导出 JSON</el-dropdown-item>
            <el-dropdown-item command="csv">导出 CSV</el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
      <el-button size="small" @click="runCleanup" :loading="cleaning">清理</el-button>

      <!-- 自动滚动 -->
      <div class="auto-scroll-control">
        <el-switch v-model="autoScroll" size="small" />
        <span class="switch-label">自动滚动</span>
      </div>

      <!-- 统计信息 -->
      <div class="stats-control">
        <span v-if="filterSearch || filterLevel" class="filter-count">
          显示 {{ filteredLogs.length }} / {{ debugStore.logs.length }}
        </span>
        <span v-else class="filter-count">
          共 {{ debugStore.logs.length }} 条
        </span>
      </div>

      <!-- 数据库大小 -->
      <span v-if="dbStats" class="db-stats" :title="`数据库大小: ${dbStats.db_size_mb} MB`">
        DB: {{ dbStats.db_size_mb }} MB
      </span>
    </div>

    <!-- 暂停状态提示 -->
    <div v-if="!debugStore.isRecording" class="paused-banner">
      ⏸ 日志录制已暂停，当前显示 {{ debugStore.logs.length }} 条历史日志
    </div>

    <div class="log-list" ref="logListRef">
      <div class="log-entries">
        <div
          v-for="item in filteredLogs"
          :key="item.ts"
          :class="['log-entry', `level-${item.level.toLowerCase()}`]"
          @click="copyLog(item)"
          :title="'点击复制'"
        >
          <span class="timestamp">{{ formatTime(item.ts) }}</span>
          <span class="level">{{ item.level }}</span>
          <span class="tag" :title="item.tag">{{ item.tag }}</span>
          <span class="message">{{ item.message }}</span>
        </div>
        <div v-if="filteredLogs.length === 0" class="empty-state">
          <span v-if="!debugStore.isRecording">已暂停录制，无新日志</span>
          <span v-else>▶ 开始</span>
        </div>
      </div>
    </div>

    <div v-if="debugStore.wsConnected && debugStore.isRecording" class="status-indicator live">
      LIVE
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch, nextTick } from 'vue'
import { ElMessage } from 'element-plus'
import { useDebugStore } from '@/stores/debug'
import { api } from '@/services/api'

/** 目标设备 ID（从父组件传入）。 */
defineProps<{ deviceId: string }>()
const debugStore = useDebugStore()

/** 日志级别过滤条件。 */
const filterLevel = ref<string | null>(null)
/** 搜索输入（带防抖）。 */
const searchInput = ref('')
/** 实际生效的搜索关键词（防抖后）。 */
const filterSearch = ref<string | null>(null)
/** 日志列表容器 DOM 引用。 */
const logListRef = ref<HTMLElement>()
/** 是否自动滚动到底部。 */
const autoScroll = ref(true)
/** 数据库统计信息。 */
const dbStats = ref<{ db_size_mb: number } | null>(null)
/** 是否正在执行清理。 */
const cleaning = ref(false)

/** 搜索防抖定时器。 */
let searchTimer: ReturnType<typeof setTimeout> | null = null

/**
 * 计算属性：根据过滤条件过滤日志。
 * 先按级别过滤，再按搜索关键词过滤（匹配 Tag 或 Message）。
 */
const filteredLogs = computed(() => {
  const logs = debugStore.logs
  const level = filterLevel.value
  const search = filterSearch.value

  // 无过滤条件时直接返回
  if (!level && !search) return logs

  if (search) {
    const query = search.toLowerCase()
    if (level) {
      return logs.filter((l) =>
        l.level === level && (
          l.tag.toLowerCase().includes(query) ||
          l.message.toLowerCase().includes(query)
        )
      )
    }
    return logs.filter((l) =>
      l.tag.toLowerCase().includes(query) ||
      l.message.toLowerCase().includes(query)
    )
  }

  return logs.filter((l) => l.level === level)
})

// 挂载时不发起任何网络请求（实施项 5）：isRecording=false，
// 开启录制时才连接 WS、拉历史并发起后端采集
onMounted(() => {
  // 保留空挂载钩子：未来的 DOM 初始化逻辑放这里
})

// 监听过滤条件变化，通过 store 的 setFilter 同步到服务端
watch(filterLevel, () => {
  debugStore.setFilter(filterLevel.value, filterSearch.value)
})

// 搜索输入防抖（300ms）
watch(searchInput, (val) => {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    filterSearch.value = val || null
    debugStore.setFilter(filterLevel.value, val || null)
  }, 300)
})

// 监听日志变化，自动滚动到底部
// nextTick 确保 Vue 完成 v-for DOM 更新后再计算 scrollHeight
watch(() => debugStore.logs.length, async () => {
  if (autoScroll.value) {
    await nextTick()
    scrollToBottom()
  }
})

// 监听录制状态变化：开启时连接 WS 并拉历史（触发后端惰性启动采集），
// 关闭时彻底断开 WS（省连接、停采集，实施项 5）
watch(() => debugStore.isRecording, async (recording) => {
  if (recording) {
    await debugStore.connectWebSocket()
    await debugStore.fetchLogs()
    if (autoScroll.value) {
      await nextTick()
      scrollToBottom()
    }
    await loadDbStats()
  } else {
    await debugStore.disconnectWebSocket()
  }
})

/** 切换录制状态（开启/暂停）。 */
function toggleRecording() {
  debugStore.setRecording(!debugStore.isRecording)
}

/** 从后端重新获取日志。 */
async function refresh() {
  await debugStore.fetchLogs()
}

/** 清空本地日志缓存。 */
function clear() {
  debugStore.logs = []
}

/**
 * 导出日志为文件。
 * 从后端获取日志数据，创建 Blob 并触发浏览器下载。
 * 空数据（404 NO_DATA，未开始采集或过滤后为空）提示「暂无数据可导出」。
 */
async function exportLogs(format: 'json' | 'csv') {
  if (!debugStore.sessionId) return
  try {
    const blob = await api.exportLogs(
      debugStore.sessionId,
      format,
      filterLevel.value,
      filterSearch.value,
    )
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `logs_${debugStore.sessionId}.${format}`
    a.click()
    URL.revokeObjectURL(url)
  } catch (e) {
    if ((e as { response?: { status?: number } }).response?.status === 404) {
      ElMessage.warning('暂无数据可导出')
    } else {
      ElMessage.error(`导出失败: ${errText(e, '未知错误')}`)
    }
    console.error('Export failed:', e)
  }
}

function errText(e: unknown, fallback: string): string {
  const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
  return detail || fallback
}

/** 加载数据库统计信息。 */
async function loadDbStats() {
  try {
    dbStats.value = await api.getDebugStats()
  } catch (e) {
    console.error('Failed to load db stats:', e)
  }
}

/**
 * 执行日志清理。
 * 清除当前会话的所有日志（数据库 + 内存缓冲区），并刷新统计信息。
 */
async function runCleanup() {
  cleaning.value = true
  try {
    if (debugStore.sessionId) {
      // 清除当前会话的数据库日志
      await api.cleanupSessionLogs(debugStore.sessionId)
    }
    // 清除内存缓冲区，使界面立即更新
    debugStore.logs = []
    await loadDbStats()
  } catch (e) {
    console.error('Failed to run cleanup:', e)
  } finally {
    cleaning.value = false
  }
}

/** 滚动到底部。调用方需确保已在 nextTick 之后（DOM 已更新）。 */
function scrollToBottom() {
  if (logListRef.value) {
    logListRef.value.scrollTop = logListRef.value.scrollHeight
  }
}

/** 复制日志条目到剪贴板。 */
function copyLog(item: { ts: number; level: string; tag: string; message: string }) {
  const text = `${formatTime(item.ts)} [${item.level}] ${item.tag}: ${item.message}`
  navigator.clipboard.writeText(text).catch(e => console.error('Copy failed:', e))
}

/**
 * 格式化 Unix 时间戳为可读时间。
 * 格式：HH:MM:SS.mmm（只保留时间部分）。
 */
function formatTime(ts: number) {
  const date = new Date(ts * 1000)
  return date.toISOString().substr(11, 12)
}
</script>

<style scoped>
.logcat-view {
  display: flex;
  flex-direction: column;
  height: 100%;
  position: relative;
}

.toolbar {
  display: flex;
  gap: 0.5rem;
  padding: 0.5rem;
  border-bottom: 1px solid #eee;
  background: #fff;
  z-index: 1;
  flex-wrap: wrap;
  align-items: center;
}

.record-btn {
  min-width: 72px;
  font-weight: 500;
}

.filter-select {
  width: 120px;
}

.search-input {
  flex: 1;
  min-width: 150px;
  max-width: 300px;
}

.toolbar :deep(.el-input .el-input__wrapper) {
  height: 32px;
}

.auto-scroll-control {
  display: flex;
  align-items: center;
  gap: 6px;
  white-space: nowrap;
}

.switch-label {
  font-size: 12px;
  color: #606266;
  user-select: none;
}

.stats-control {
  margin-left: auto;
  padding: 0 8px;
  font-size: 11px;
  color: #909399;
  white-space: nowrap;
}

.filter-count {
  font-variant-numeric: tabular-nums;
}

.paused-banner {
  background: #fff3e0;
  color: #e65100;
  padding: 6px 12px;
  font-size: 12px;
  font-weight: 500;
  border-bottom: 1px solid #ffe0b2;
}

.log-list {
  flex: 1;
  overflow-y: auto;
  font-family: 'Consolas', 'Monaco', monospace;
  font-size: 12px;
}

.log-entries {
  display: flex;
  flex-direction: column;
}

.log-entry {
  padding: 3px 4px;
  border-bottom: 1px solid #f5f5f5;
  display: flex;
  gap: 8px;
  align-items: flex-start;
  box-sizing: border-box;
  cursor: pointer;
  line-height: 1.5;
}

.log-entry:hover {
  background: #f0f7ff;
}

.empty-state {
  padding: 20px;
  text-align: center;
  color: #909399;
  font-size: 12px;
}

.timestamp {
  color: #999;
  flex-shrink: 0;
  white-space: nowrap;
}

.level {
  width: 20px;
  text-align: center;
  font-weight: bold;
  flex-shrink: 0;
}

/* 日志级别颜色编码 */
.level-v { color: #999; }
.level-d { color: #0088ff; }
.level-i { color: #00aa00; }
.level-w { color: #ff8800; }
.level-e { color: #ff0000; }
.level-f { color: #ff00ff; }

.tag {
  color: #0088aa;
  flex-shrink: 0;
  min-width: 100px;
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.message {
  flex: 1;
  word-break: break-all;
  white-space: pre-wrap;
}

.status-indicator {
  position: absolute;
  top: 50px;
  right: 10px;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 10px;
  font-weight: bold;
  color: #fff;
  background: #67c23a;
  opacity: 0.8;
}

.status-indicator.live {
  animation: pulse 2s infinite;
}

@keyframes pulse {
  0% { opacity: 0.8; }
  50% { opacity: 1; }
  100% { opacity: 0.8; }
}

.db-stats {
  padding: 0 8px;
  font-size: 11px;
  color: #909399;
  cursor: help;
}
</style>
