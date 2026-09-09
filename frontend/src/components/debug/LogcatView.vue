<!--
  Logcat 日志查看器
  ===================

  实时显示 Android 设备的 logcat 日志。

  功能：
    - 日志级别过滤（V/D/I/W/E/F）
    - 标签过滤（子串匹配）
    - 实时日志推送（通过 WebSocket）
    - 刷新按钮：从后端重新获取日志
    - 清空按钮：清除本地日志缓存
    - 自动滚动：新日志时自动滚动到底部

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

  性能注意：
    当日志量很大时（>1000 条），应考虑使用虚拟滚动
    （vue-virtual-scroller）以避免 DOM 节点过多导致卡顿。
-->
<template>
  <div class="logcat-view">
    <div class="toolbar">
      <el-select v-model="filterLevel" placeholder="Level" clearable size="small">
        <el-option label="Verbose" value="V" />
        <el-option label="Debug" value="D" />
        <el-option label="Info" value="I" />
        <el-option label="Warning" value="W" />
        <el-option label="Error" value="E" />
        <el-option label="Fatal" value="F" />
      </el-select>
      <el-input v-model="filterTag" placeholder="Tag filter" clearable size="small" />
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
      <el-switch
        v-model="autoScroll"
        active-text="自动滚动"
        size="small"
      />
      <span v-if="dbStats" class="db-stats" :title="`数据库大小: ${dbStats.db_size_mb} MB`">
        DB: {{ dbStats.db_size_mb }} MB
      </span>
    </div>

    <div class="log-list" ref="logListRef">
      <RecycleScroller
        ref="scrollerRef"
        :items="filteredLogs"
        :item-size="22"
        key-field="ts"
        v-slot="{ item }"
        class="scroller"
      >
        <div :class="['log-entry', `level-${item.level.toLowerCase()}`]">
          <span class="timestamp">{{ formatTime(item.ts) }}</span>
          <span class="level">{{ item.level }}</span>
          <span class="tag">{{ item.tag }}</span>
          <span class="message">{{ item.message }}</span>
        </div>
      </RecycleScroller>
    </div>

    <div v-if="debugStore.wsConnected" class="status-indicator live">
      LIVE
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch, nextTick } from 'vue'
import { RecycleScroller } from 'vue-virtual-scroller'
import 'vue-virtual-scroller/dist/vue-virtual-scroller.css'
import { useDebugStore } from '@/stores/debug'
import { api } from '@/services/api'

/** 目标设备 ID（从父组件传入）。 */
defineProps<{ deviceId: string }>()
const debugStore = useDebugStore()

/** 日志级别过滤条件。 */
const filterLevel = ref<string | null>(null)
/** 标签过滤条件（子串匹配）。 */
const filterTag = ref<string | null>(null)
/** 日志列表容器 DOM 引用。 */
const logListRef = ref<HTMLElement>()
/** 虚拟滚动组件引用。 */
const scrollerRef = ref<InstanceType<typeof RecycleScroller>>()
/** 是否自动滚动到底部。 */
const autoScroll = ref(true)
/** 数据库统计信息。 */
const dbStats = ref<{ db_size_mb: number } | null>(null)
/** 是否正在执行清理。 */
const cleaning = ref(false)

/**
 * 计算属性：根据过滤条件过滤日志。
 * 先按级别过滤，再按标签过滤。
 */
const filteredLogs = computed(() => {
  let logs = debugStore.logs
  if (filterLevel.value) {
    logs = logs.filter((l) => l.level === filterLevel.value)
  }
  if (filterTag.value) {
    logs = logs.filter((l) => l.tag.includes(filterTag.value!))
  }
  return logs
})

// 组件挂载时加载日志并建立 WebSocket 连接
onMounted(async () => {
  await debugStore.fetchLogs()
  await debugStore.connectWebSocket()
  await loadDbStats()
})

// 监听过滤条件变化，通过 store 的 setFilter 同步到服务端
watch(filterLevel, () => {
  debugStore.setFilter(filterLevel.value, filterTag.value)
})

watch(filterTag, () => {
  debugStore.setFilter(filterLevel.value, filterTag.value)
})

// 监听日志变化，自动滚动到底部
watch(() => debugStore.logs.length, () => {
  if (autoScroll.value) {
    scrollToBottom()
  }
})

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
 */
async function exportLogs(format: 'json' | 'csv') {
  if (!debugStore.sessionId) return
  try {
    const blob = await api.exportLogs(
      debugStore.sessionId,
      format,
      filterLevel.value,
      filterTag.value,
    )
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `logs_${debugStore.sessionId}.${format}`
    a.click()
    URL.revokeObjectURL(url)
  } catch (e) {
    console.error('Export failed:', e)
  }
}

/**
 * 加载数据库统计信息。
 */
async function loadDbStats() {
  try {
    dbStats.value = await api.getDebugStats()
  } catch (e) {
    console.error('Failed to load db stats:', e)
  }
}

/**
 * 执行日志清理。
 */
async function runCleanup() {
  cleaning.value = true
  try {
    await api.runCleanup()
    await loadDbStats()
  } catch (e) {
    console.error('Failed to run cleanup:', e)
  } finally {
    cleaning.value = false
  }
}

/** 滚动到底部（使用虚拟滚动器的 scrollToItem）。 */
async function scrollToBottom() {
  await nextTick()
  if (scrollerRef.value && filteredLogs.value.length > 0) {
    scrollerRef.value.scrollToItem(filteredLogs.value.length - 1)
  }
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
}

.log-list {
  flex: 1;
  overflow: hidden;
  font-family: 'Consolas', 'Monaco', monospace;
  font-size: 12px;
}

.scroller {
  height: 100%;
}

.log-entry {
  padding: 2px 4px;
  border-bottom: 1px solid #f5f5f5;
  display: flex;
  gap: 8px;
  height: 22px;
  align-items: center;
  box-sizing: border-box;
}

.log-entry:hover {
  background: #f9f9f9;
}

.timestamp {
  color: #999;
  flex-shrink: 0;
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
}

.message {
  flex: 1;
  word-break: break-all;
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
  margin-left: auto;
  padding: 0 8px;
  font-size: 11px;
  color: #909399;
  cursor: help;
}
</style>
