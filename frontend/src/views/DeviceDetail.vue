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

  子组件关系：
    DeviceDetail.vue → VideoPlayer.vue
                     → DebugPanel.vue
                     → stores/debug.ts
-->
<template>
  <div class="device-detail">
    <div class="device-info">
      <h2>设备: {{ deviceId }}</h2>
      <el-button @click="disconnect">断开</el-button>
    </div>

    <div class="content" :class="{ 'column-layout': panelPosition === 'bottom' }">
      <VideoPlayer
        :device-id="deviceId"
        :device-width="deviceResolution[0]"
        :device-height="deviceResolution[1]"
      />

      <DebugPanel
        :device-id="deviceId"
        @position-change="panelPosition = $event"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import { api } from '@/services/api'
import DebugPanel from '@/components/debug/DebugPanel.vue'
import VideoPlayer from '@/components/stream/VideoPlayer.vue'
import { useDebugStore } from '@/stores/debug'

const props = defineProps<{ id: string }>()
const deviceId = props.id

const debugStore = useDebugStore()
const deviceResolution = ref<[number, number]>([1080, 1920])
/** 调试面板停靠位置，用于切换布局方向。 */
const panelPosition = ref<'bottom' | 'right'>('right')

/**
 * 组件挂载时初始化：
 *   1. 获取设备信息（分辨率）
 *   2. 创建调试会话（用于 logcat 和 shell）
 */
onMounted(async () => {
  try {
    const device = await api.getDevice(deviceId)
    if (device?.resolution) {
      deviceResolution.value = device.resolution
    }
  } catch {
    // 使用默认分辨率
  }

  await debugStore.createSession(deviceId, 'user-1')
})

/** 组件卸载时清理：关闭调试会话。 */
onUnmounted(() => {
  debugStore.closeSession()
})

/** 断开设备连接，返回列表页。 */
function disconnect() {
  debugStore.closeSession()
  window.history.back()
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
