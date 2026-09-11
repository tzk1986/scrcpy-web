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
