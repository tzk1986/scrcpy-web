<!--
  性能监控视图
  ============

  实时显示 Android 设备的性能指标：
    - CPU 使用率
    - 内存使用情况
    - 帧率（FPS）
    - 当前前台 Activity

  数据来源：
    通过 WebSocket 接收实时性能数据（/ws/perf/{device_id}）。

  功能：
    - 实时指标卡片（CPU / 内存 / FPS / Activity）
    - 历史曲线图（ECharts 折线图）
    - 自动滚动（保留最近 60 秒数据）
-->

<template>
  <div class="perf-view">
    <!-- 实时指标卡片 -->
    <div class="metrics-grid">
      <div class="metric-card">
        <div class="metric-label">CPU 使用率</div>
        <div class="metric-value" :class="{ warning: latestMetrics.cpu_percent > 80 }">
          {{ latestMetrics.cpu_percent?.toFixed(1) || '0.0' }}%
        </div>
        <div class="metric-bar">
          <div class="metric-bar-fill" :style="{ width: `${latestMetrics.cpu_percent || 0}%` }"></div>
        </div>
      </div>

      <div class="metric-card">
        <div class="metric-label">内存使用</div>
        <div class="metric-value">
          {{ latestMetrics.used_memory_mb?.toFixed(0) || '0' }} MB
        </div>
        <div class="metric-bar">
          <div class="metric-bar-fill" :style="{ width: `${memoryPercent}%` }"></div>
        </div>
        <div class="metric-detail">
          / {{ latestMetrics.total_memory_mb?.toFixed(0) || '0' }} MB
        </div>
      </div>

      <div class="metric-card">
        <div class="metric-label">帧率</div>
        <div class="metric-value" :class="{ warning: latestMetrics.fps && latestMetrics.fps < 30 }">
          {{ latestMetrics.fps?.toFixed(0) || '-' }} FPS
        </div>
        <div class="metric-detail" v-if="latestMetrics.jank_count">
          卡顿: {{ latestMetrics.jank_count }} 帧
        </div>
      </div>

      <div class="metric-card wide">
        <div class="metric-label">当前应用</div>
        <div class="metric-value small">
          {{ latestMetrics.top_package || '未知' }}
        </div>
        <div class="metric-detail">
          {{ latestMetrics.current_activity || '-' }}
        </div>
      </div>
    </div>

    <!-- 历史曲线图 -->
    <div class="charts-grid">
      <div class="chart-container">
        <div class="chart-title">CPU 使用率 (%)</div>
        <v-chart :option="cpuChartOption" autoresize class="chart" />
      </div>

      <div class="chart-container">
        <div class="chart-title">内存使用 (MB)</div>
        <v-chart :option="memoryChartOption" autoresize class="chart" />
      </div>
    </div>

    <!-- 状态指示器 -->
    <div class="status-bar">
      <div v-if="wsConnected" class="status-indicator live">
        LIVE
      </div>
      <div v-else class="status-indicator">
        未连接
      </div>
      <div class="status-text">
        数据点: {{ metricsHistory.length }} / 60
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
import { GridComponent, TooltipComponent, DataZoomComponent } from 'echarts/components'
import { WebSocketService } from '@/services/websocket'

// 注册 ECharts 组件
use([CanvasRenderer, LineChart, GridComponent, TooltipComponent, DataZoomComponent])

interface PerfMetrics {
  ts: number
  cpu_percent: number
  total_memory_mb: number
  used_memory_mb: number
  fps: number | null
  jank_count: number
  current_activity: string
  top_package: string
}

const props = defineProps<{
  deviceId: string
}>()

const metricsHistory = ref<PerfMetrics[]>([])
const wsConnected = ref(false)
let ws: WebSocketService | null = null

// 最新指标
const latestMetrics = computed(() => {
  return metricsHistory.value[metricsHistory.value.length - 1] || ({} as PerfMetrics)
})

// 内存使用百分比
const memoryPercent = computed(() => {
  if (!latestMetrics.value.total_memory_mb) return 0
  return (latestMetrics.value.used_memory_mb / latestMetrics.value.total_memory_mb) * 100
})

// CPU 图表配置
const cpuChartOption = computed(() => ({
  tooltip: {
    trigger: 'axis',
    formatter: (params: any) => {
      const p = params[0]
      return `${p.axisValue}<br/>CPU: ${p.value.toFixed(1)}%`
    }
  },
  grid: {
    left: '3%',
    right: '4%',
    bottom: '3%',
    top: '10%',
    containLabel: true
  },
  xAxis: {
    type: 'category',
    data: metricsHistory.value.map((_, i) => `-${metricsHistory.value.length - i}s`),
    axisLabel: {
      interval: Math.floor(metricsHistory.value.length / 6)
    }
  },
  yAxis: {
    type: 'value',
    max: 100,
    axisLabel: {
      formatter: '{value}%'
    }
  },
  series: [{
    data: metricsHistory.value.map(m => m.cpu_percent),
    type: 'line',
    smooth: true,
    showSymbol: false,
    lineStyle: {
      width: 2,
      color: '#409EFF'
    },
    areaStyle: {
      color: {
        type: 'linear',
        x: 0,
        y: 0,
        x2: 0,
        y2: 1,
        colorStops: [
          { offset: 0, color: 'rgba(64, 158, 255, 0.3)' },
          { offset: 1, color: 'rgba(64, 158, 255, 0.05)' }
        ]
      }
    }
  }]
}))

// 内存图表配置
const memoryChartOption = computed(() => ({
  tooltip: {
    trigger: 'axis',
    formatter: (params: any) => {
      const p = params[0]
      return `${p.axisValue}<br/>内存: ${p.value.toFixed(0)} MB`
    }
  },
  grid: {
    left: '3%',
    right: '4%',
    bottom: '3%',
    top: '10%',
    containLabel: true
  },
  xAxis: {
    type: 'category',
    data: metricsHistory.value.map((_, i) => `-${metricsHistory.value.length - i}s`),
    axisLabel: {
      interval: Math.floor(metricsHistory.value.length / 6)
    }
  },
  yAxis: {
    type: 'value',
    axisLabel: {
      formatter: '{value} MB'
    }
  },
  series: [{
    data: metricsHistory.value.map(m => m.used_memory_mb),
    type: 'line',
    smooth: true,
    showSymbol: false,
    lineStyle: {
      width: 2,
      color: '#67C23A'
    },
    areaStyle: {
      color: {
        type: 'linear',
        x: 0,
        y: 0,
        x2: 0,
        y2: 1,
        colorStops: [
          { offset: 0, color: 'rgba(103, 194, 58, 0.3)' },
          { offset: 1, color: 'rgba(103, 194, 58, 0.05)' }
        ]
      }
    }
  }]
}))

onMounted(() => {
  connectWebSocket()
})

onUnmounted(() => {
  disconnectWebSocket()
})

function connectWebSocket() {
  const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const wsUrl = `${wsProtocol}//${window.location.host}/ws/perf/${props.deviceId}`

  ws = new WebSocketService(wsUrl)

  ws.setMessageHandler((data) => {
    if (typeof data === 'string') {
      try {
        const metrics = JSON.parse(data) as PerfMetrics
        metricsHistory.value.push(metrics)

        // 保留最近 60 秒数据
        if (metricsHistory.value.length > 60) {
          metricsHistory.value.shift()
        }
      } catch (e) {
        console.error('[PerfView] Failed to parse metrics:', e)
      }
    }
  })

  ws.setErrorHandler((error) => {
    console.error('[PerfView] WebSocket error:', error)
  })

  ws.setCloseHandler(() => {
    console.log('[PerfView] WebSocket closed')
    wsConnected.value = false
  })

  ws.connect()
  wsConnected.value = true
}

function disconnectWebSocket() {
  if (ws) {
    ws.close()
    ws = null
  }
  wsConnected.value = false
}
</script>

<style scoped>
.perf-view {
  display: flex;
  flex-direction: column;
  height: 100%;
  padding: 12px;
  gap: 12px;
  overflow-y: auto;
}

.metrics-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
}

.metric-card {
  background: #f5f7fa;
  border-radius: 8px;
  padding: 12px;
  text-align: center;
}

.metric-card.wide {
  grid-column: span 3;
  text-align: left;
}

.metric-label {
  font-size: 12px;
  color: #606266;
  margin-bottom: 4px;
}

.metric-value {
  font-size: 24px;
  font-weight: 600;
  color: #303133;
}

.metric-value.small {
  font-size: 16px;
}

.metric-value.warning {
  color: #E6A23C;
}

.metric-bar {
  height: 4px;
  background: #e4e7ed;
  border-radius: 2px;
  margin-top: 8px;
  overflow: hidden;
}

.metric-bar-fill {
  height: 100%;
  background: #409EFF;
  transition: width 0.3s ease;
}

.metric-detail {
  font-size: 11px;
  color: #909399;
  margin-top: 4px;
}

.charts-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  flex: 1;
  min-height: 200px;
}

.chart-container {
  background: #fff;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  padding: 12px;
  display: flex;
  flex-direction: column;
}

.chart-title {
  font-size: 13px;
  font-weight: 500;
  color: #303133;
  margin-bottom: 8px;
}

.chart {
  flex: 1;
  min-height: 150px;
}

.status-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 12px;
  background: #f5f7fa;
  border-radius: 6px;
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

.status-text {
  font-size: 12px;
  color: #606266;
}

/* 响应式 */
@media (max-width: 768px) {
  .metrics-grid {
    grid-template-columns: 1fr 1fr;
  }

  .metric-card.wide {
    grid-column: span 2;
  }

  .charts-grid {
    grid-template-columns: 1fr;
  }
}
</style>
