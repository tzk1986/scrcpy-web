<!--
  设备详情页
  ============

  展示单个设备的实时视频流和调试面板。

  布局：
    ┌──────────────────────────────────────────┐
    │ 设备: xxx          [断开]                 │  ← 设备信息栏
    ├─────────────────────────┬────────────────┤
    │                         │                │
    │    视频画布              │  调试面板       │
    │    (canvas)             │  (DebugPanel)  │
    │                         │                │
    └─────────────────────────┴────────────────┘

  功能：
    - 通过 WebSocket 接收 H.264 视频帧并渲染到 canvas
    - 创建调试会话（自动）
    - 断开按钮：关闭 WebSocket 和调试会话，返回列表页

  生命周期：
    onMounted: 创建调试会话 + 建立视频 WebSocket
    onUnmounted: 关闭 WebSocket + 关闭调试会话

  子组件关系：
    DeviceDetail.vue → DebugPanel.vue
                     → services/websocket.ts
                     → stores/debug.ts
-->
<template>
  <div class="device-detail">
    <div class="device-info">
      <h2>设备: {{ deviceId }}</h2>
      <el-button @click="disconnect">断开</el-button>
    </div>

    <div class="content">
      <div class="video-container">
        <canvas ref="canvas" width="1080" height="1920"></canvas>
      </div>

      <DebugPanel :device-id="deviceId" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import { WebSocketService } from '@/services/websocket'
import DebugPanel from '@/components/debug/DebugPanel.vue'
import { useDebugStore } from '@/stores/debug'

const props = defineProps<{ id: string }>()
const deviceId = props.id

const canvas = ref<HTMLCanvasElement>()
const debugStore = useDebugStore()
let videoWs: WebSocketService | null = null

/**
 * 组件挂载时初始化：
 *   1. 创建调试会话（用于 logcat 和 shell）
 *   2. 建立视频 WebSocket 连接
 *   3. 注册消息处理器：接收 Blob 数据并渲染到 canvas
 */
onMounted(async () => {
  await debugStore.createSession(deviceId, 'user-1')

  videoWs = new WebSocketService(`ws://localhost:8000/ws/video/${deviceId}`)
  videoWs.setMessageHandler((data) => {
    if (data instanceof Blob) {
      renderFrame(data)
    }
  })
  videoWs.connect()
})

/**
 * 组件卸载时清理：
 *   关闭 WebSocket 连接和调试会话。
 */
onUnmounted(() => {
  videoWs?.close()
  debugStore.closeSession()
})

/**
 * 将接收到的视频帧渲染到 canvas。
 * 流程：Blob → ObjectURL → Image → drawImage
 */
function renderFrame(blob: Blob) {
  const ctx = canvas.value?.getContext('2d')
  if (!ctx) return

  const img = new Image()
  img.onload = () => {
    ctx.drawImage(img, 0, 0)
    URL.revokeObjectURL(img.src)  // 释放 ObjectURL 内存
  }
  img.src = URL.createObjectURL(blob)
}

/**
 * 断开设备连接。
 * 关闭所有连接后返回列表页。
 */
function disconnect() {
  videoWs?.close()
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

.content {
  display: flex;
  flex: 1;
  overflow: hidden;
}

.video-container {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #000;
}

canvas {
  max-width: 100%;
  max-height: 100%;
}
</style>
