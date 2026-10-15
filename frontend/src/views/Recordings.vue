<!--
  录屏产物页（方案 46）
  ====================

  全局历史录屏列表：扫盘为准（GET /api/recording/files），支持播放（弹层
  <video> + 导出 webm）、下载（<a download>）、删除（二次确认）。
  设备页「查看录屏」经 ?device=<id> 进入时按设备过滤（拍板项 2）。

  关键口径：
    - 设备列原样展示后端解析出的 dev 段（sanitize 后形态，无别名映射，与
      Dashboard 口径一致）；旧格式产物显示「未知设备（旧版产物）」（拍板项 3）
    - 大小一位小数 MB（RecordingPanel 同款口径）
    - webm 导出能力自 RecordingPanel 迁入（复用 webmExporter：captureStream +
      MediaRecorder 前 10s 短片；不支持时按钮禁用降级）
    - 刷新时机：挂载一次 + 手动刷新按钮；不轮询（录制中会话由设备页管理）
-->
<template>
  <div class="recordings-page">
    <div class="page-header">
      <h2>录屏</h2>
      <div class="header-actions">
        <router-link v-if="backDeviceId" class="back-device" :to="`/device/${encodeURIComponent(backDeviceId)}`">
          <el-button class="btn-back-device" size="small">← 返回设备</el-button>
        </router-link>
        <el-button class="btn-refresh" @click="load">刷新</el-button>
      </div>
    </div>

    <div v-if="deviceFilter" class="filter-bar">
      <span>仅看 {{ deviceFilter }}</span>
      <el-button class="filter-clear" size="small" text @click="deviceFilter = ''">清除</el-button>
    </div>

    <div v-if="visibleFiles.length" class="file-list">
      <div v-for="f in visibleFiles" :key="f.filename" class="file-row">
        <span class="col-device">{{ f.device ?? '未知设备（旧版产物）' }}</span>
        <span class="col-time">{{ formatTime(f.created_at) }}</span>
        <span class="col-size">{{ formatSize(f.size_bytes) }}</span>
        <el-button class="btn-play" size="small" @click="openPlayer(f)">播放</el-button>
        <a class="col-download" :href="api.recordingFileUrl(f.filename)" :download="f.filename">下载</a>
        <el-button class="btn-delete" size="small" type="danger" text @click="remove(f)">删除</el-button>
      </div>
    </div>
    <div v-else class="empty-hint">
      暂无录屏。在设备页开始录制后，产物会出现在这里。
      <router-link to="/">回设备列表</router-link>
    </div>

    <el-dialog v-model="playerVisible" :title="playing?.filename" width="640px">
      <video ref="videoEl" class="player-video" :src="playingUrl" controls autoplay />
      <div class="player-actions">
        <a :href="playingUrl" :download="playing?.filename">下载</a>
        <el-button
          class="btn-webm"
          :disabled="!webmSupported || exporting"
          :title="webmSupported ? '' : '当前浏览器不支持 webm 录制导出'"
          @click="exportWebm"
        >
          {{ exporting ? '导出中…' : '导出 webm' }}
        </el-button>
      </div>
    </el-dialog>
  </div>
</template>

<script lang="ts">
/** 字节 → 一位小数 MB（面板既有口径，独立导出供测试直接断言）。 */
export function formatSizeMB(bytes: number): string {
  return `${(bytes / 1048576).toFixed(1)} MB`
}
</script>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, type RecordingFileItem } from '@/services/api'
import { exportWebmClip, isWebmRecorderSupported } from '@/utils/webmExporter'

const route = useRoute()

const files = ref<RecordingFileItem[]>([])
const deviceFilter = ref<string>(typeof route.query.device === 'string' ? route.query.device : '')
const playerVisible = ref(false)
const playing = ref<RecordingFileItem | null>(null)
const videoEl = ref<HTMLVideoElement | null>(null)
const exporting = ref(false)

/** 支持面在打开弹层时读取（浏览器状态可变化，测试可重开断言两分支）。 */
const webmSupported = ref(true)

const visibleFiles = computed(() =>
  deviceFilter.value
    ? files.value.filter(
        (f) => f.device && sanitizeDeviceId(deviceFilter.value) === f.device
      )
    : files.value
)

const playingUrl = computed(() =>
  playing.value ? api.recordingFileUrl(playing.value.filename) : ''
)

/** 从设备页跳转进入的入口设备（与过滤清除无关，只要经 ?device= 进入就可返回）。 */
const backDeviceId = computed(() =>
  typeof route.query.device === 'string' ? route.query.device : ''
)

/** 与后端 _sanitize_device 同规则：非法字符 → `-`（IP:port 查询匹配落盘名 dev 段）。 */
function sanitizeDeviceId(id: string): string {
  return id.replace(/[^A-Za-z0-9._-]/g, '-')
}

function formatSize(bytes: number): string {
  return formatSizeMB(bytes)
}

function formatTime(iso: string): string {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString()
}

async function load() {
  try {
    const res = await api.listRecordingFiles()
    files.value = res.files
  } catch {
    files.value = []
    ElMessage.error('录屏列表加载失败')
  }
}

function openPlayer(f: RecordingFileItem) {
  playing.value = f
  webmSupported.value = isWebmRecorderSupported()
  playerVisible.value = true
}

/** Blob 下载（RecordingPanel 同款范式）。 */
function triggerDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

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

async function remove(f: RecordingFileItem) {
  try {
    await ElMessageBox.confirm(`删除录屏 ${f.filename}？该操作不可恢复`, '删除录屏', {
      type: 'warning',
    })
  } catch {
    return // 取消
  }
  try {
    await api.deleteRecordingFile(f.filename)
    ElMessage.success('已删除')
    await load()
  } catch {
    ElMessage.error('删除失败，文件可能正被占用')
  }
}

onMounted(load)
</script>

<style scoped>
.recordings-page {
  padding: 16px;
}

.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.filter-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  padding: 6px 10px;
  background: #ecf5ff;
  border-radius: 4px;
  color: #409eff;
}

.file-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.file-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 10px;
  border: 1px solid #ebeef5;
  border-radius: 6px;
}

.col-device {
  flex: 1;
  min-width: 0;
  overflow-wrap: anywhere;
}

.col-time,
.col-size {
  color: #606266;
  font-variant-numeric: tabular-nums;
}

.empty-hint {
  padding: 32px 0;
  text-align: center;
  color: #909399;
}

.player-video {
  width: 100%;
  background: #000;
}

.player-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 10px;
}
</style>
