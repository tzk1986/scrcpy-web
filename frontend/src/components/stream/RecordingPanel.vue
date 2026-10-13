<!--
  录屏面板（方案 37 B5）
  =====================

  设备端 screenrecord 录屏的前端控制面板。

  三态：
    idle      — 「录屏」按钮（挂载时经 getRecording 恢复会话态）
    recording — 红点 + mm:ss 计时 + 「停止」按钮
    done      — <video> 预览 + 下载链接（均指向 GET /api/recording/{id}/file）
              + 「导出 webm」按钮（B6：captureStream + MediaRecorder 抽
              min(时长, 10s) 短片，fps 10 控体积；浏览器不支持时禁用降级）
              + 「重新录制」按钮（方案 42 §4：直接再 start，终态可被新会话
              覆盖、互斥语义不变）+ 产物大小信息行（后端 file_size）

  关键行为：
    - 挂载恢复：getRecording 返回 recording 会话时按 started_at 续计时；
      返回 done + local_path 时直接进入预览态（页面刷新后仍可下载）
    - 409 互斥：后端单设备会话互斥，冲突时 ElMessage.error 提示
    - 卸载时若仍在录制：仅提示服务端 60 秒上限兜底收尾，不调 stop API
      （设备端 screenrecord 自带 --time-limit，服务端自愈巡检也会收尾）
-->
<template>
  <div class="recording-panel">
    <!-- 空闲态：录屏入口 -->
    <el-button v-if="state === 'idle'" class="record-start" @click="start">录屏</el-button>

    <!-- 录制中：红点 + 计时 + 停止 -->
    <div v-if="state === 'recording'" class="recording-active">
      <span class="record-dot" aria-label="录制中" />
      <span class="record-timer">{{ formatElapsed(elapsed) }}</span>
      <el-button class="record-stop" @click="stop">停止</el-button>
    </div>

    <!-- 完成：视频预览 + 下载 + webm 短片导出 -->
    <div v-if="state === 'done'" class="recording-done">
      <video ref="videoEl" class="record-preview" :src="fileUrl" controls />
      <a class="record-download" :href="fileUrl" :download="downloadName">下载录像</a>
      <el-button
        class="export-webm"
        :disabled="!webmSupported || exporting"
        :title="webmSupported ? '' : '当前浏览器不支持 webm 录制导出'"
        @click="exportWebm"
      >
        {{ exporting ? '导出中…' : '导出 webm' }}
      </el-button>
      <el-button class="record-again" @click="start">重新录制</el-button>
      <div v-if="fileSizeText" class="record-info">{{ fileSizeText }}</div>
    </div>
  </div>
</template>

<script lang="ts">
/** 秒 → mm:ss 展示（独立导出供测试直接断言）。 */
export function formatElapsed(totalSeconds: number): string {
  const s = Math.max(0, Math.floor(totalSeconds))
  const mm = String(Math.floor(s / 60)).padStart(2, '0')
  const ss = String(s % 60).padStart(2, '0')
  return `${mm}:${ss}`
}
</script>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '@/services/api'
import { exportWebmClip, isWebmRecorderSupported } from '@/utils/webmExporter'

const props = defineProps<{ deviceId: string }>()

type PanelState = 'idle' | 'recording' | 'done'

const state = ref<PanelState>('idle')
const elapsed = ref(0)
const sessionId = ref('')
const videoEl = ref<HTMLVideoElement | null>(null)
const exporting = ref(false)

const fileSize = ref(0)

/** 产物大小信息行文案（方案 42 §4；未知/为 0 时不显示）。 */
const fileSizeText = computed(
  () => (fileSize.value > 0 ? `约 ${(fileSize.value / 1048576).toFixed(1)} MB` : '')
)

let timer: ReturnType<typeof setInterval> | null = null

/** 产物下载/预览 URL（B4 file 端点，路径无尾斜杠）。 */
const fileUrl = computed(() => `/api/recording/${props.deviceId}/file`)
const downloadName = computed(
  () =>
    `openscrcpy_${props.deviceId.replace(/[^a-zA-Z0-9._-]/g, '_')}${sessionId.value ? `_${sessionId.value}` : ''}.mp4`
)

/** 浏览器是否支持 webm 录制导出（不支持时禁用按钮降级，不硬抛）。 */
const webmSupported = computed(() => isWebmRecorderSupported())

/** Blob 下载（LogcatView/PerfView 同款范式）：a.download 触发后立即 revoke。 */
function triggerDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

/**
 * 导出 webm 短片段：把预览 video 前 min(时长, 10s) 录成 clip.webm
 * （exporter 内 fps 10 抽帧控体积，spec §B2），成功后触发下载。
 */
async function exportWebm() {
  const el = videoEl.value
  if (!el || exporting.value || !webmSupported.value) return
  exporting.value = true
  try {
    const blob = await exportWebmClip(el)
    triggerDownload(blob, 'clip.webm')
  } catch {
    ElMessage.error('webm 导出失败')
  } finally {
    exporting.value = false
  }
}

function startTimer(seed = 0) {
  stopTimer()
  elapsed.value = seed
  timer = setInterval(() => {
    elapsed.value += 1
  }, 1000)
}

function stopTimer() {
  if (timer !== null) {
    clearInterval(timer)
    timer = null
  }
}

/** 启动录屏；409（单设备互斥）时提示并保持空闲。 */
async function start() {
  try {
    const res = await api.startRecording(props.deviceId)
    sessionId.value = res.session?.id ?? ''
    fileSize.value = 0
    state.value = 'recording'
    startTimer()
  } catch (err) {
    const httpErr = err as {
      response?: { status?: number; data?: { error?: { message?: string } } }
    }
    if (httpErr.response?.status === 409) {
      ElMessage.error(
        httpErr.response.data?.error?.message || '该设备已有进行中的录屏会话，请先停止当前录制后再试'
      )
    } else {
      ElMessage.error('录屏启动失败')
    }
  }
}

/** 停止并收尾；done + local_path → 预览态，否则回空闲并提示。 */
async function stop() {
  try {
    const res = await api.stopRecording(props.deviceId)
    const session = res?.session
    if (session?.state === 'done' && session.local_path) {
      sessionId.value = session.id ?? ''
      fileSize.value = res?.file_size ?? 0
      state.value = 'done'
    } else {
      state.value = 'idle'
      ElMessage.error('录制已结束但未生成录像文件')
    }
  } catch {
    state.value = 'idle'
    ElMessage.error('停止录屏失败')
  } finally {
    stopTimer()
  }
}

onMounted(async () => {
  try {
    const res = await api.getRecording(props.deviceId)
    const session = res?.session
    if (session?.state === 'recording') {
      sessionId.value = session.id ?? ''
      // 按 started_at 续计时：floor 只计已完整流逝的秒数（与走秒自增
      // 口径一致，也避免秒内小数造成展示抖动）
      const seed = Math.max(
        0,
        Math.floor(Date.now() / 1000 - (session.started_at ?? Date.now() / 1000))
      )
      startTimer(seed)
      state.value = 'recording'
    } else if (session?.state === 'done' && session.local_path) {
      sessionId.value = session.id ?? ''
      fileSize.value = res?.file_size ?? 0
      state.value = 'done'
    }
  } catch {
    state.value = 'idle' // 无会话 / 产物已被清理（后端收敛 404）：静默复位（方案 42 §4）
  }
})

onUnmounted(() => {
  stopTimer()
  // 不主动 stop：设备端 --time-limit 与服务端自愈巡检兜底收尾
  if (state.value === 'recording') {
    ElMessage.info('录制仍在继续，服务端将按 60 秒上限自动收尾')
  }
})
</script>

<style scoped>
.recording-panel {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.recording-active {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.record-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: #f56c6c;
  animation: pulse 1.2s ease-in-out infinite;
}

@keyframes pulse {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.35;
  }
}

.record-timer {
  font-variant-numeric: tabular-nums;
  color: #f56c6c;
}

.recording-done {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.record-preview {
  width: 200px;
  height: 112px;
  background: #000;
  border-radius: 4px;
}

.record-info {
  font-size: 12px;
  color: #909399;
}
</style>
