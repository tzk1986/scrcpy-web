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

    <!-- 活动告警徽标条（方案 24）：面板开着也能一眼看到异常 -->
    <div v-if="activeAlerts.length" class="alert-bar">
      <span v-for="a in activeAlerts" :key="a.id" class="alert-chip">
        {{ alertLabel(a.id) }} {{ a.value != null ? a.value.toFixed(1) : '-' }}
      </span>
    </div>

    <!-- 实时指标卡片 -->
    <div class="metrics-grid">
      <div class="metric-card">
        <div class="metric-label">CPU 使用率</div>
        <div class="metric-value" :class="{ warning: hasAlert('cpu_percent') }">
          {{ latestMetrics.cpu_percent?.toFixed(1) || '0.0' }}%
        </div>
        <div class="metric-bar">
          <div class="metric-bar-fill" :style="{ width: `${latestMetrics.cpu_percent || 0}%` }"></div>
        </div>
      </div>

      <div class="metric-card">
        <div class="metric-label">内存使用</div>
        <div class="metric-value" :class="{ warning: hasAlert('memory_percent') }">
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
        <div class="metric-value" :class="{ warning: hasAlert('fps') }">
          {{ latestMetrics.fps?.toFixed(0) || '-' }} FPS
        </div>
        <!-- gfxinfo 每 5 轮采样一次（后端降频），非 gfxinfo 轮 jank 恒 0，
             故 delta 覆盖约 5 秒窗口而非 1 秒，标签按「采样窗口」口径标注 -->
        <div class="metric-detail" v-if="latestMetrics.jank_count !== undefined && latestMetrics.jank_count > 0">
          卡顿: {{ latestMetrics.jank_count }} 帧/采样窗口
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

    <!-- 进程 CPU Top 8（方案 37 D5）：缺字段/空数组时整体隐藏 -->
    <div v-if="hasProcessTable" class="extra-section">
      <div class="extra-title">进程 CPU Top 8</div>
      <el-table :data="topProcesses" size="small" class="proc-table">
        <el-table-column prop="proc" label="进程" min-width="140" show-overflow-tooltip />
        <el-table-column prop="user" label="User %" min-width="90" />
        <el-table-column prop="kernel" label="Kernel %" min-width="90" />
      </el-table>
    </div>

    <!-- 电池展示条（方案 37 D5）：温度/电压/health 单项缺失隐藏，全缺失整条隐藏 -->
    <div v-if="hasBatteryBar" class="extra-section">
      <div class="extra-title">电池</div>
      <div class="battery-bar">
        <span v-if="batteryTempText !== null" class="battery-chip">{{ batteryTempText }}</span>
        <span v-if="batteryVoltageText !== null" class="battery-chip">{{ batteryVoltageText }}</span>
        <span v-if="batteryHealthText !== null" class="battery-chip">{{ batteryHealthText }}</span>
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
import { ElMessage } from 'element-plus'
import VChart from 'vue-echarts'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, DataZoomComponent } from 'echarts/components'
import { api } from '@/services/api'
import { WebSocketService } from '@/services/websocket'
import { alertLabel, createAlertNotifier, extractAlerts, type AlertItem } from '@/services/alerts'
import { runExport, errText, type ExportCommand } from '@/services/exportKit'

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
  alerts?: AlertItem[]
  // 方案 37 D5 新 4 键（后端 D3 透传）：可选——旧后端/旧数据可能整键缺失
  cpu_processes?: CpuProcessInfo[]
  battery_temp?: number | null
  battery_voltage?: number | null
  battery_health?: string
}

/** 进程级 CPU 明细（后端 sampler CpuProcessInfo 纯值 dict 化形态）。 */
interface CpuProcessInfo {
  proc: string
  user: number
  kernel: number
}

const props = defineProps<{
  deviceId: string
}>()

const metricsHistory = ref<PerfMetrics[]>([])
const wsConnected = ref(false)
let ws: WebSocketService | null = null

// 活动告警（方案 24）：WS 每帧携带当前快照，驱动徽标条与卡片警示色
const activeAlerts = ref<AlertItem[]>([])
const alertNotifier = createAlertNotifier()

function hasAlert(id: string): boolean {
  return activeAlerts.value.some((a) => a.id === id)
}

/** 录制状态（LIVE 徽标同源样式，方案 18 Step 6）。 */
const isRecording = ref(false)
const recordReason = ref('')
const recordRows = ref(0)
const recordBusy = ref(false)
let statusTimer: ReturnType<typeof setInterval> | null = null

// 缓存导出可用性：录制中（导出会先 flush 未落盘批）或已有落盘缓存行。
// recordRows 为跨批次聚合的缓存总行数，停止录制后仍可用于导出历史数据。
const cacheExportable = computed(() => isRecording.value || recordRows.value > 0)

// 最新指标
const latestMetrics = computed(() => {
  return metricsHistory.value[metricsHistory.value.length - 1] || ({} as PerfMetrics)
})

// 内存使用百分比
const memoryPercent = computed(() => {
  if (!latestMetrics.value.total_memory_mb) return 0
  return (latestMetrics.value.used_memory_mb / latestMetrics.value.total_memory_mb) * 100
})

// ---------------- 方案 37 D5：进程 CPU Top 8 + 电池展示条 ----------------

/** 进程 CPU Top 8：user+kernel 降序；缺失/空数组防御性空表（隐藏分段）。 */
const topProcesses = computed<CpuProcessInfo[]>(() => {
  const list = latestMetrics.value.cpu_processes
  if (!list || list.length === 0) return []
  return [...list].sort((a, b) => b.user + b.kernel - (a.user + a.kernel)).slice(0, 8)
})

const hasProcessTable = computed(() => topProcesses.value.length > 0)

/** 温度文本：摄氏原值（后端已 ÷10），toFixed(1) 展示；None/非有限值隐藏单项。 */
const batteryTempText = computed<string | null>(() => {
  const t = latestMetrics.value.battery_temp
  if (t == null || !Number.isFinite(t)) return null
  return `${t.toFixed(1)} °C`
})

/** 电压文本：mV 原值（0 为板级假值，如实展示）；None 隐藏单项。 */
const batteryVoltageText = computed<string | null>(() => {
  const v = latestMetrics.value.battery_voltage
  if (v == null) return null
  return `${v} mV`
})

/** health 文本：后端归一字符串直显；缺失/空串隐藏单项。 */
const batteryHealthText = computed<string | null>(() => {
  const h = latestMetrics.value.battery_health
  if (!h) return null
  return h
})

/** 电池条整体可见性：三项全缺时整条隐藏（旧后端降级）。 */
const hasBatteryBar = computed(
  () =>
    batteryTempText.value !== null ||
    batteryVoltageText.value !== null ||
    batteryHealthText.value !== null,
)

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
  refreshRecordStatus()
  // 轮询录制状态：捕获设备失联/配置热重载等外部终止（§3.6 状态机）
  statusTimer = setInterval(refreshRecordStatus, 3000)
})

onUnmounted(() => {
  disconnectWebSocket()
  if (statusTimer) {
    clearInterval(statusTimer)
    statusTimer = null
  }
})

/** 拉取录制状态（失败静默保持现状，下一轮再试）。 */
async function refreshRecordStatus() {
  try {
    const status = await api.getPerfRecordStatus(props.deviceId)
    isRecording.value = status.recording
    recordReason.value = status.reason
    recordRows.value = status.rows
  } catch (e) {
    console.error('[PerfView] Failed to load record status:', e)
  }
}

/** 录制开关：成功后刷新状态徽标。 */
async function toggleRecording() {
  recordBusy.value = true
  try {
    if (isRecording.value) {
      const s = await api.stopPerfRecording(props.deviceId)
      ElMessage.success(`录制已停止，本次落盘 ${s.rows} 行`)
    } else {
      await api.startPerfRecording(props.deviceId)
      ElMessage.success('录制已开启')
    }
    await refreshRecordStatus()
  } catch (e) {
    ElMessage.error(`录制操作失败: ${errText(e, '未知错误')}`)
  } finally {
    recordBusy.value = false
  }
}

/** 导出指标：blob 范式下载 + 行数/区间回显（方案 18 O2；逻辑下沉 exportKit，方案 51 F2-11）。 */
function handleExport(command: ExportCommand) {
  return runExport({ command, deviceId: props.deviceId, prefix: 'perf', fetch: api.exportPerfMetrics })
}

function connectWebSocket() {
  const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const wsUrl = `${wsProtocol}//${window.location.host}/ws/perf/${props.deviceId}`

  ws = new WebSocketService(wsUrl)

  ws.setMessageHandler((data) => {
    if (typeof data === 'string') {
      try {
        const metrics = JSON.parse(data) as PerfMetrics
        activeAlerts.value = extractAlerts(metrics.alerts)
        alertNotifier.update(activeAlerts.value)
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

  // 方案 51 F2-21：LIVE 徽标由真实 socket 事件驱动（open 首次/重连均触发），
  // 不再 connect() 后立即置 true——断线自动恢复期间状态与流一致
  ws.setOpenHandler(() => {
    wsConnected.value = true
  })

  ws.connect()
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

.toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.rec-indicator {
  margin-left: auto;
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

.alert-bar {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.alert-chip {
  padding: 2px 10px;
  border-radius: 10px;
  font-size: 12px;
  color: #e6a23c;
  background: #fdf6ec;
  border: 1px solid #f5dab1;
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

.extra-section {
  background: #fff;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  padding: 12px;
}

.extra-title {
  font-size: 13px;
  font-weight: 500;
  color: #303133;
  margin-bottom: 8px;
}

.battery-bar {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.battery-chip {
  padding: 2px 10px;
  border-radius: 10px;
  font-size: 12px;
  color: #409EFF;
  background: #ecf5ff;
  border: 1px solid #d9ecff;
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
