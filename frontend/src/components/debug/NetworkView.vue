<!--
  网络监控视图
  ============

  实时监控 Android 设备的网络状态：
    - WiFi 连接状态和 SSID
    - 网络流量统计（收发字节数、速率）
    - 流量曲线图（ECharts 折线图）
    - 活跃连接列表

  数据来源：
    REST API /api/network/{device_id}

  功能：
    - 实时指标卡片（WiFi/接收/发送/连接数）
    - 历史流量曲线图
    - 连接列表（TCP/UDP）
-->

<template>
  <div class="network-view">
    <!-- 工具栏：导出（暂存/缓存 × CSV/JSON）与录制开关（方案 18 Step 6） -->
    <div class="toolbar">
      <el-dropdown size="small" @command="handleExport" trigger="click">
        <el-button size="small">导出</el-button>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item command="buffer-csv">导出 CSV（暂存）</el-dropdown-item>
            <el-dropdown-item command="buffer-json">导出 JSON（暂存）</el-dropdown-item>
            <el-dropdown-item command="cache-csv" divided :disabled="!cacheExportable">
              导出 CSV（缓存{{ cacheExportable ? '' : '，需先开启录制' }}）
            </el-dropdown-item>
            <el-dropdown-item command="cache-json" :disabled="!cacheExportable">
              导出 JSON（缓存{{ cacheExportable ? '' : '，需先开启录制' }}）
            </el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
      <el-button
        :type="isRecording ? 'success' : 'info'"
        size="small"
        :loading="recordBusy"
        @click="toggleRecording"
      >
        <span v-if="isRecording">⏸ 停止录制</span>
        <span v-else>▶ 开始录制</span>
      </el-button>
      <div v-if="isRecording" class="status-indicator live rec-indicator" :title="recordReason">
        REC · {{ recordRows }} 行
      </div>
    </div>

    <!-- WiFi 状态栏 -->
    <div class="wifi-bar" :class="{ connected: wifiConnected }">
      <span class="wifi-icon">{{ wifiConnected ? '📶' : '📵' }}</span>
      <span class="wifi-ssid">{{ wifiSSID || '未连接' }}</span>
      <el-tag v-if="wifiConnected" type="success" size="small">已连接</el-tag>
      <el-tag v-else type="info" size="small">断开</el-tag>
    </div>

    <!-- 实时指标卡片 -->
    <div class="metrics-grid">
      <div class="metric-card">
        <div class="metric-label">↓ 接收速率</div>
        <div class="metric-value rx">
          {{ formatRate(currentStats.rx_rate_kbps) }}
        </div>
        <div class="metric-detail">
          总计: {{ formatBytes(currentStats.rx_bytes) }}
        </div>
      </div>

      <div class="metric-card">
        <div class="metric-label">↑ 发送速率</div>
        <div class="metric-value tx">
          {{ formatRate(currentStats.tx_rate_kbps) }}
        </div>
        <div class="metric-detail">
          总计: {{ formatBytes(currentStats.tx_bytes) }}
        </div>
      </div>

      <div class="metric-card">
        <div class="metric-label">活跃连接</div>
        <div class="metric-value">
          {{ currentStats.active_connections || 0 }}
        </div>
        <div class="metric-detail">
          总连接: {{ connections.length }}
        </div>
      </div>
    </div>

    <!-- 流量曲线图 -->
    <div class="chart-container">
      <div class="chart-title">网络流量 (KB/s)</div>
      <v-chart :option="chartOption" autoresize class="chart" />
    </div>

    <!-- 连接列表 -->
    <div class="connections-section">
      <div class="section-header">
        <span>活跃连接 ({{ connections.length }})</span>
        <el-select v-model="connectionFilter" size="small" class="filter-select">
          <el-option label="全部" value="all" />
          <el-option label="TCP" value="tcp" />
          <el-option label="UDP" value="udp" />
          <el-option label="已建立" value="established" />
        </el-select>
      </div>
      <div class="connection-list">
        <div v-for="(conn, i) in filteredConnections" :key="i" class="connection-item">
          <span class="protocol" :class="conn.protocol">{{ conn.protocol.toUpperCase() }}</span>
          <span class="addr">{{ conn.local_addr }}:{{ conn.local_port }}</span>
          <span class="arrow">→</span>
          <span class="addr">{{ conn.remote_addr }}:{{ conn.remote_port }}</span>
          <el-tag size="small" :type="getStateTagType(conn.state)">{{ conn.state }}</el-tag>
        </div>
        <div v-if="filteredConnections.length === 0" class="empty">
          无连接
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import type { AxiosResponse } from 'axios'
import { ElMessage } from 'element-plus'
import VChart from 'vue-echarts'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, LegendComponent } from 'echarts/components'
import { api } from '@/services/api'

use([CanvasRenderer, LineChart, GridComponent, TooltipComponent, LegendComponent])

interface NetworkStats {
  ts: number
  rx_bytes: number
  tx_bytes: number
  rx_rate_kbps: number
  tx_rate_kbps: number
  active_connections: number
  wifi_connected: boolean
  wifi_ssid: string | null
}

interface NetworkConnection {
  protocol: string
  local_addr: string
  local_port: number
  remote_addr: string
  remote_port: number
  state: string
  uid: number | null
}

const props = defineProps<{
  deviceId: string
}>()

const currentStats = ref<Partial<NetworkStats>>({})
const connections = ref<NetworkConnection[]>([])
const connectionFilter = ref<'all' | 'tcp' | 'udp' | 'established'>('all')

/** 录制状态（LIVE 徽标同源样式，方案 18 Step 6）。 */
const isRecording = ref(false)
const recordReason = ref('')
const recordRows = ref(0)
const recordBusy = ref(false)

// 缓存导出可用性：录制中（导出会先 flush 未落盘批）或已有落盘缓存行。
// recordRows 为跨批次聚合的缓存总行数，停止录制后仍可用于导出历史数据。
const cacheExportable = computed(() => isRecording.value || recordRows.value > 0)

/** 轮询中的一次性状态拉取失败不打断数据轮询（下次 fetchData 再试）。 */
let pendingStatusRefresh: Promise<void> | null = null

// 历史数据（最近 60 秒）
const rxHistory = ref<number[]>([])
const txHistory = ref<number[]>([])
const timeLabels = ref<string[]>([])

// WiFi 状态
const wifiConnected = computed(() => currentStats.value.wifi_connected || false)
const wifiSSID = computed(() => currentStats.value.wifi_ssid)

// 过滤后的连接列表
const filteredConnections = computed(() => {
  if (connectionFilter.value === 'all') return connections.value
  if (connectionFilter.value === 'tcp') {
    return connections.value.filter(c => c.protocol.startsWith('tcp'))
  }
  if (connectionFilter.value === 'udp') {
    return connections.value.filter(c => c.protocol === 'udp')
  }
  if (connectionFilter.value === 'established') {
    return connections.value.filter(c => c.state === 'ESTABLISHED')
  }
  return connections.value
})

// 流量曲线图配置
const chartOption = computed(() => ({
  tooltip: {
    trigger: 'axis',
  },
  legend: {
    data: ['接收', '发送'],
    top: 0,
  },
  grid: {
    left: '3%',
    right: '4%',
    bottom: '3%',
    top: '30',
    containLabel: true,
  },
  xAxis: {
    type: 'category',
    data: timeLabels.value,
    boundaryGap: false,
  },
  yAxis: {
    type: 'value',
    axisLabel: {
      formatter: '{value} KB/s',
    },
  },
  series: [
    {
      name: '接收',
      type: 'line',
      smooth: true,
      showSymbol: false,
      data: rxHistory.value,
      lineStyle: { color: '#409EFF' },
      areaStyle: {
        color: {
          type: 'linear',
          x: 0, y: 0, x2: 0, y2: 1,
          colorStops: [
            { offset: 0, color: 'rgba(64, 158, 255, 0.3)' },
            { offset: 1, color: 'rgba(64, 158, 255, 0.05)' },
          ],
        },
      },
    },
    {
      name: '发送',
      type: 'line',
      smooth: true,
      showSymbol: false,
      data: txHistory.value,
      lineStyle: { color: '#E6A23C' },
      areaStyle: {
        color: {
          type: 'linear',
          x: 0, y: 0, x2: 0, y2: 1,
          colorStops: [
            { offset: 0, color: 'rgba(230, 162, 60, 0.3)' },
            { offset: 1, color: 'rgba(230, 162, 60, 0.05)' },
          ],
        },
      },
    },
  ],
}))

let pollTimer: ReturnType<typeof setInterval> | null = null

onMounted(() => {
  startPolling()
})

onUnmounted(() => {
  stopPolling()
})

function startPolling() {
  fetchData()
  pollTimer = setInterval(fetchData, 2000)
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

async function fetchData() {
  try {
    // 获取网络统计
    const stats = await api.getNetworkStats(props.deviceId)
    currentStats.value = stats

    // 更新历史数据
    const now = new Date()
    const label = `${now.getMinutes()}:${now.getSeconds().toString().padStart(2, '0')}`
    timeLabels.value.push(label)
    rxHistory.value.push(stats.rx_rate_kbps)
    txHistory.value.push(stats.tx_rate_kbps)

    // 保留最近 60 个数据点
    if (timeLabels.value.length > 60) {
      timeLabels.value.shift()
      rxHistory.value.shift()
      txHistory.value.shift()
    }

    // 获取连接列表
    const connData = await api.getNetworkConnections(props.deviceId)
    connections.value = connData.connections
  } catch (e) {
    console.error('[NetworkView] Failed to fetch data:', e)
  }
  void refreshRecordStatus()
}

/** 拉取录制状态（失败静默保持现状，下一轮再试）。 */
async function refreshRecordStatus() {
  if (pendingStatusRefresh) return pendingStatusRefresh
  pendingStatusRefresh = api
    .getNetworkRecordStatus(props.deviceId)
    .then((status) => {
      isRecording.value = status.recording
      recordReason.value = status.reason
      recordRows.value = status.rows
    })
    .catch((e) => {
      console.error('[NetworkView] Failed to load record status:', e)
    })
    .finally(() => {
      pendingStatusRefresh = null
    })
  return pendingStatusRefresh
}

/** 录制开关：成功后刷新状态徽标。 */
async function toggleRecording() {
  recordBusy.value = true
  try {
    if (isRecording.value) {
      const s = await api.stopNetworkRecording(props.deviceId)
      ElMessage.success(`录制已停止，本次落盘 ${s.rows} 行`)
    } else {
      await api.startNetworkRecording(props.deviceId)
      ElMessage.success('录制已开启')
    }
    await refreshRecordStatus()
  } catch (e) {
    ElMessage.error(`录制操作失败: ${errText(e, '未知错误')}`)
  } finally {
    recordBusy.value = false
  }
}

type ExportCommand = 'buffer-csv' | 'buffer-json' | 'cache-csv' | 'cache-json'

/** 导出统计：blob 范式下载 + 行数/区间回显（方案 18 O2）。 */
async function handleExport(command: ExportCommand) {
  const [source, format] = command.split('-') as ['buffer' | 'cache', 'csv' | 'json']
  try {
    const res = await api.exportNetworkStats(props.deviceId, format, source)
    triggerDownload(res.data, `network_${props.deviceId}_${source}.${format}`)
    ElMessage.success(formatExportMessage(res))
  } catch (e) {
    if ((e as AxiosErrorLike).response?.status === 404) {
      ElMessage.warning('暂无数据可导出')
    } else {
      ElMessage.error(`导出失败: ${errText(e, '未知错误')}`)
    }
  }
}

interface AxiosErrorLike {
  response?: { status?: number }
}

/** 从响应头提取导出元数据（后端 X-Export-* 头）。 */
function formatExportMessage(res: AxiosResponse<Blob>): string {
  const count = res.headers['x-export-count']
  const oldest = res.headers['x-export-oldest-ts']
  const newest = res.headers['x-export-newest-ts']
  if (!count) return '导出完成'
  const range =
    oldest && newest
      ? `（${formatTs(Number(oldest))} – ${formatTs(Number(newest))}）`
      : ''
  return `导出完成：${count} 条${range}`
}

function formatTs(ts: number): string {
  if (!Number.isFinite(ts)) return '-'
  return new Date(ts * 1000).toLocaleTimeString()
}

function errText(e: unknown, fallback: string): string {
  const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
  return detail || fallback
}

/** Blob 下载（LogcatView 同款范式）。 */
function triggerDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

function formatRate(kbps: number | undefined): string {
  if (!kbps) return '0 KB/s'
  if (kbps >= 1024) {
    return `${(kbps / 1024).toFixed(1)} MB/s`
  }
  return `${kbps.toFixed(1)} KB/s`
}

function formatBytes(bytes: number | undefined): string {
  if (!bytes) return '0 B'
  if (bytes >= 1024 * 1024 * 1024) {
    return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`
  }
  if (bytes >= 1024 * 1024) {
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  }
  if (bytes >= 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`
  }
  return `${bytes} B`
}

function getStateTagType(state: string): '' | 'success' | 'warning' | 'info' | 'danger' {
  if (state === 'ESTABLISHED') return 'success'
  if (state === 'LISTEN') return 'info'
  if (state === 'TIME_WAIT' || state === 'CLOSE_WAIT') return 'warning'
  return 'info'
}
</script>

<style scoped>
.network-view {
  display: flex;
  flex-direction: column;
  height: 100%;
  padding: 8px;
  gap: 8px;
  overflow-y: auto;
}

.toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.rec-indicator {
  margin-left: auto;
}

.status-indicator {
  display: inline-flex;
  align-items: center;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 600;
  background: #909399;
  color: #fff;
}

.status-indicator.live {
  background: #67C23A;
  animation: pulse 2s infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.6; }
}

.wifi-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  background: #f5f7fa;
  border-radius: 6px;
}

.wifi-bar.connected {
  background: #f0f9ff;
}

.wifi-icon {
  font-size: 18px;
}

.wifi-ssid {
  flex: 1;
  font-size: 13px;
  font-weight: 500;
  color: #303133;
}

.metrics-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
}

.metric-card {
  background: #f5f7fa;
  border-radius: 8px;
  padding: 10px;
  text-align: center;
}

.metric-label {
  font-size: 11px;
  color: #606266;
  margin-bottom: 4px;
}

.metric-value {
  font-size: 20px;
  font-weight: 600;
  color: #303133;
}

.metric-value.rx {
  color: #409EFF;
}

.metric-value.tx {
  color: #E6A23C;
}

.metric-detail {
  font-size: 11px;
  color: #909399;
  margin-top: 4px;
}

.chart-container {
  background: #fff;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  padding: 12px;
}

.chart-title {
  font-size: 13px;
  font-weight: 500;
  color: #303133;
  margin-bottom: 8px;
}

.chart {
  height: 150px;
}

.connections-section {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 150px;
}

.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
  font-size: 13px;
  font-weight: 500;
  color: #303133;
}

.filter-select {
  width: 100px;
}

.connection-list {
  flex: 1;
  overflow-y: auto;
  border: 1px solid #e4e7ed;
  border-radius: 6px;
  padding: 4px;
}

.connection-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  font-size: 11px;
  font-family: monospace;
  border-bottom: 1px solid #f0f0f0;
}

.connection-item:last-child {
  border-bottom: none;
}

.protocol {
  padding: 2px 6px;
  border-radius: 3px;
  font-size: 10px;
  font-weight: 600;
  background: #e4e7ed;
}

.protocol.tcp {
  background: #d4e8ff;
  color: #0066cc;
}

.protocol.udp {
  background: #ffe4d4;
  color: #cc6600;
}

.addr {
  color: #606266;
}

.arrow {
  color: #909399;
}

.empty {
  padding: 20px;
  text-align: center;
  color: #909399;
  font-size: 12px;
}
</style>
