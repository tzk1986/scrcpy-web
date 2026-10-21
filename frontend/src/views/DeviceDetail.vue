<!--
  设备详情页
  ============

  展示单个设备的实时视频流和调试面板。

  布局（右侧面板）：
    ┌──────────────────────────────────────────┐
    │ 设备: xxx          [断开]                 │  ← 设备信息栏
    ├──────────────────────────┬───────────────┤
    │                          │               │
    │    VideoPlayer           │  调试面板      │
    │  (截屏视频 + 输入控制)     │  (可拖拽)     │
    │                          │               │
    └──────────────────────────┴───────────────┘

  布局（底部面板）：
    ┌──────────────────────────────────────────┐
    │ 设备: xxx          [断开]                 │
    ├──────────────────────────────────────────┤
    │                                          │
    │    VideoPlayer                           │
    │                                          │
    ├──────────────────────────────────────────┤
    │    调试面板（可拖拽）                      │
    └──────────────────────────────────────────┘

  功能：
    - 通过 VideoPlayer 组件显示设备视频流（周期性截屏）
    - 支持触摸/鼠标输入（点击、滑动、长按）
    - 调试面板可停靠（底部/右侧）、可拖拽调整大小
    - 创建调试会话（自动）
    - 断开按钮：关闭 WebSocket 和调试会话，返回列表页
    - 截图按钮（方案 37 B7）：抓帧 → ScreenshotPreview 预览弹窗标注/导出（不再直接下载）
    - 文件按钮（方案 39 T5）：跳转 /device/:id/files 文件管理独立页
    - 设置按钮（方案 45 T5）：跳转 /settings 独立设置页

  子组件关系：
    DeviceDetail.vue → VideoPlayer.vue
                     → DebugPanel.vue
                     → RecordingPanel.vue
                     → ScreenshotPreview.vue（截图预览弹窗）
                     → stores/debug.ts
-->
<template>
  <div class="device-detail">
    <div class="device-info">
      <h2>设备: {{ deviceId }}</h2>
      <div class="actions">
        <el-button class="btn-files" @click="goFiles">文件</el-button>
        <el-button @click="captureScreenshot">截图</el-button>
        <el-button data-test="device-settings-btn" @click="goSettings">设置</el-button>
        <el-button @click="disconnect">断开</el-button>
        <RecordingPanel :device-id="deviceId" />
      </div>
    </div>

    <div class="content" :class="{ 'column-layout': panelPosition === 'bottom' }">
      <VideoPlayer
        ref="videoPlayerRef"
        :device-id="deviceId"
        :device-width="deviceResolution[0]"
        :device-height="deviceResolution[1]"
        @first-frame="onFirstFrame"
      />

      <DebugPanel
        :device-id="deviceId"
        @position-change="panelPosition = $event"
      />
    </div>

    <!-- 截图预览弹窗（方案 37 B7）：标注 + 导出 PNG / 直接保存，下载在弹窗内进行 -->
    <ScreenshotPreview
      :visible="screenshotVisible"
      :image="screenshotImage"
      :device-id="deviceId"
      @close="screenshotVisible = false"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { api } from '@/services/api'
import DebugPanel from '@/components/debug/DebugPanel.vue'
import RecordingPanel from '@/components/stream/RecordingPanel.vue'
import ScreenshotPreview from '@/components/stream/ScreenshotPreview.vue'
import VideoPlayer from '@/components/stream/VideoPlayer.vue'
import { useDebugStore } from '@/stores/debug'
import { useDeviceStore } from '@/stores/device'

const props = defineProps<{ id: string }>()
const deviceId = props.id
const router = useRouter()

const debugStore = useDebugStore()
const deviceStore = useDeviceStore()
const deviceResolution = ref<[number, number]>([1080, 1920])
/** 调试面板停靠位置，用于切换布局方向。 */
const panelPosition = ref<'bottom' | 'right'>('right')
const videoPlayerRef = ref<InstanceType<typeof VideoPlayer>>()
/** 截图预览弹窗（方案 37 B7）：可见性 + 抓帧产物（Blob 交组件内部管理 object URL） */
const screenshotVisible = ref(false)
const screenshotImage = ref<Blob | string | null>(null)

/**
 * 组件挂载时初始化：
 *   1. 获取设备信息（分辨率）
 *   2. 创建调试会话（用于 logcat 和 shell）
 */
onMounted(async () => {
  console.log('[DeviceDetail] Component mounted, deviceId:', deviceId)
  try {
    const device = await api.getDevice(deviceId)
    console.log('[DeviceDetail] Device info:', device)
    if (device?.resolution) {
      deviceResolution.value = device.resolution
    }
  } catch {
    // 使用默认分辨率
  }

  // 方案 51 F2-02：会话创建失败须可见（503 上限专项文案，其余透传后端 message）
  try {
    await debugStore.createSession(deviceId, 'user-1')
  } catch (e: unknown) {
    const err = (
      e as { response?: { data?: { error?: { code?: string; message?: string } } } }
    )?.response?.data?.error
    if (err?.code === 'SESSION_LIMIT') {
      ElMessage.error('调试会话已达上限（32），请先关闭部分调试页面后重试')
    } else {
      ElMessage.error(err?.message || '调试会话创建失败')
    }
  }
})

/** 组件卸载时清理：关闭调试会话。 */
onUnmounted(() => {
  debugStore.closeSession()
})

/** 断开设备连接，返回 Dashboard。不用 history.back()：历史栈上一条
 *  未必是列表页（如经「查看录屏」/设置 + 返回设备进入），落点须显式。 */
function disconnect() {
  debugStore.closeSession()
  router.push('/')
}

/** 跳转设备文件管理独立页（方案 39 T5）。 */
function goFiles() {
  router.push({ name: 'device-files', params: { id: deviceId } })
}

/** 跳转独立设置页（方案 45 T5；T8 带 ?device=<device_id> 供覆盖卡聚焦）。 */
function goSettings() {
  router.push({ path: '/settings', query: { device: deviceId } })
}

/**
 * 视频流首帧抓帧（方案 40 消费点 2）：写入设备 store 缩略图缓存，
 * 列表页直接复用以 {url, ts} 结构，TTL 语义与 loadThumbnail 共用。
 */
function onFirstFrame(blob: Blob) {
  deviceStore.setThumbnailFromBlob(deviceId, blob)
}

/**
 * 截图按钮（方案 37 B7 改道）：优先取视频流渲染帧（方向天然正，方案 40 A）；
 * 无视频流时回退设备端截图并提示（方案 40 B）。产物交给 ScreenshotPreview
 * 预览弹窗标注与下载（不再直接触发下载，下载收敛到弹窗工具栏）。
 */
async function captureScreenshot() {
  const player = videoPlayerRef.value
  if (!player) return
  const { blob, source } = await player.captureSnapshot()
  if (source === 'fallback') {
    ElMessage.info('当前无视频流，已回退设备端截图')
  }
  screenshotImage.value = blob
  screenshotVisible.value = true
}
</script>

<style scoped>
.device-detail {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 60px);
}

.device-info {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 1rem;
  border-bottom: 1px solid #eee;
}

.actions {
  display: flex;
  gap: 8px;
}

/* 默认行布局（面板在右侧） */
.content {
  display: flex;
  flex: 1;
  overflow: hidden;
}

/* 列布局（面板在底部） */
.content.column-layout {
  flex-direction: column;
}
</style>
