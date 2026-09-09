<!--
  视频播放器组件
  ================

  显示设备实时视频流（H.264 WebCodecs 或截屏回退），并处理用户输入事件。

  布局：
    ┌─────────────────────────┐
    │                         │
    │    Canvas（视频画面）     │  ← 触摸/鼠标事件
    │                         │
    ├─────────────────────────┤
    │ [返回] [主页] [菜单]     │  ← Android 导航键
    │ FPS: 30  帧: 1200      │  ← 状态信息
    └─────────────────────────┘

  功能：
    - H.264 实时视频流（WebCodecs VideoDecoder，~30 FPS）
    - 截屏回退模式（~0.7 FPS，WebCodecs 不可用时）
    - 鼠标/触摸输入（点击、滑动、长按）
    - Android 导航键（返回、主页、菜单）
    - 实时 FPS 和帧数统计

  生命周期：
    onMounted: 初始化 WebSocket + VideoStream + InputController
    onUnmounted: 停止视频流，关闭 WebSocket
-->
<template>
  <div class="video-player">
    <!-- 视频画布 -->
    <div class="video-container" ref="containerRef">
      <canvas
        ref="canvasRef"
        class="video-canvas"
        @mousedown="onMouseDown"
        @mousemove="onMouseMove"
        @mouseup="onMouseUp"
        @mouseleave="onMouseUp"
        @touchstart.prevent="onTouchStart"
        @touchmove.prevent="onTouchMove"
        @touchend.prevent="onTouchEnd"
        @contextmenu.prevent
      ></canvas>

      <!-- 状态覆盖层 -->
      <div v-if="state !== 'streaming'" class="status-overlay">
        <div v-if="state === 'idle' || state === 'configuring'" class="status-text">正在连接...</div>
        <div v-if="state === 'error'" class="status-text error">
          连接错误: {{ error }}
          <el-button size="small" @click="reconnect">重新连接</el-button>
        </div>
        <div v-if="state === 'stopped'" class="status-text">已断开</div>
      </div>
    </div>

    <!-- 控制栏 -->
    <div class="controls">
      <!-- Android 导航键 -->
      <div class="nav-buttons">
        <el-button-group>
          <el-button size="small" @click="sendKey(KeyCode.BACK)" title="返回">
            ←
          </el-button>
          <el-button size="small" @click="sendKey(KeyCode.HOME)" title="主页">
            ●
          </el-button>
          <el-button size="small" @click="sendKey(KeyCode.APP_SWITCH)" title="多任务">
            ■
          </el-button>
          <el-button size="small" @click="sendKey(KeyCode.MENU)" title="菜单">
            ≡
          </el-button>
        </el-button-group>
      </div>

      <!-- 状态信息 -->
      <div class="stats">
        <span class="stat-item">模式: {{ mode }}</span>
        <span class="stat-item">FPS: {{ fps }}</span>
        <span class="stat-item">帧: {{ frameCount }}</span>
        <span class="stat-item" :class="state">
          {{ stateLabel }}
        </span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed } from 'vue'
import { WebSocketService } from '@/services/websocket'
import { VideoStream, type VideoStreamState } from '@/services/videoStream'
import { H264VideoStream, type H264StreamState } from '@/services/h264VideoStream'
import { InputController, KeyCode } from '@/services/inputController'

const props = defineProps<{
  deviceId: string
  deviceWidth?: number
  deviceHeight?: number
}>()

const containerRef = ref<HTMLElement>()
const canvasRef = ref<HTMLCanvasElement>()

const state = ref<VideoStreamState | H264StreamState>('idle')
const fps = ref(0)
const frameCount = ref(0)
const error = ref<string | null>(null)
const mode = ref<'h264' | 'screenshot'>('screenshot')

const stateLabel = computed(() => {
  switch (state.value) {
    case 'idle': return '连接中'
    case 'configuring': return '配置中'
    case 'streaming': return '直播中'
    case 'error': return '错误'
    case 'stopped': return '已停止'
  }
})

let ws: WebSocketService | null = null
let videoStream: VideoStream | null = null
let h264Stream: H264VideoStream | null = null
let inputController: InputController | null = null

/** 检测浏览器是否支持 WebCodecs VideoDecoder */
function isWebCodecsSupported(): boolean {
  return typeof VideoDecoder !== 'undefined' && typeof EncodedVideoChunk !== 'undefined'
}

onMounted(async () => {
  if (!canvasRef.value) return

  // 设置 canvas 初始尺寸（默认手机竖屏比例）
  canvasRef.value.width = props.deviceWidth || 1080
  canvasRef.value.height = props.deviceHeight || 1920

  // 初始化 WebSocket（用于视频流和输入事件）
  const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  ws = new WebSocketService(`${wsProtocol}//${window.location.host}/ws/video/${props.deviceId}`)
  ws.setErrorHandler((e) => {
    console.error('WebSocket error:', e)
  })
  ws.setCloseHandler(() => {
    state.value = 'stopped'
  })
  ws.connect()

  // 初始化输入控制器
  inputController = new InputController(
    ws,
    props.deviceWidth || 1080,
    props.deviceHeight || 1920
  )

  // 根据浏览器支持选择视频流模式
  if (isWebCodecsSupported()) {
    mode.value = 'h264'
    h264Stream = new H264VideoStream(ws, canvasRef.value)

    h264Stream.setStateChangeHandler((newState) => {
      state.value = newState
      if (newState === 'error') {
        error.value = h264Stream?.stats.error || 'Unknown error'
      }
    })

    h264Stream.setStatsUpdateHandler((stats) => {
      fps.value = stats.fps
      frameCount.value = stats.frameCount
    })

    h264Stream.start()
  } else {
    // 回退到截屏模式
    mode.value = 'screenshot'
    videoStream = new VideoStream(props.deviceId, canvasRef.value, ws)

    videoStream.setStateChangeHandler((newState) => {
      state.value = newState
      if (newState === 'error') {
        error.value = videoStream?.stats.error || 'Unknown error'
      }
    })

    videoStream.setStatsUpdateHandler((stats) => {
      fps.value = stats.fps
      frameCount.value = stats.frameCount
    })

    await videoStream.start()
  }
})

onUnmounted(() => {
  h264Stream?.stop()
  videoStream?.stop()
  ws?.close()
})

// ===== 输入事件处理 =====

function onMouseDown(e: MouseEvent) {
  if (canvasRef.value && inputController) {
    inputController.handleMouseDown(e, canvasRef.value)
  }
}

function onMouseMove(e: MouseEvent) {
  if (canvasRef.value && inputController) {
    inputController.handleMouseMove(e, canvasRef.value)
  }
}

function onMouseUp(e: MouseEvent) {
  if (canvasRef.value && inputController) {
    inputController.handleMouseUp(e, canvasRef.value)
  }
}

function onTouchStart(e: TouchEvent) {
  if (canvasRef.value && inputController) {
    inputController.handleTouchStart(e, canvasRef.value)
  }
}

function onTouchMove(e: TouchEvent) {
  if (canvasRef.value && inputController) {
    inputController.handleTouchMove(e, canvasRef.value)
  }
}

function onTouchEnd(e: TouchEvent) {
  if (canvasRef.value && inputController) {
    inputController.handleTouchEnd(e, canvasRef.value)
  }
}

function sendKey(keycode: number) {
  inputController?.sendKey(keycode)
}

async function reconnect() {
  error.value = null
  state.value = 'idle'

  if (h264Stream) {
    h264Stream.start()
  } else if (videoStream) {
    await videoStream.start()
  }
}
</script>

<style scoped>
.video-player {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-width: 0;
  min-height: 0;
  height: 100%;
  background: #1a1a1a;
}

.video-container {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
  overflow: hidden;
  min-height: 0;
}

.video-canvas {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
  cursor: pointer;
  touch-action: none;
  user-select: none;
}

.status-overlay {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.7);
  z-index: 10;
}

.status-text {
  color: #fff;
  text-align: center;
  font-size: 14px;
}

.status-text.error {
  color: #f56c6c;
}

.controls {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 16px;
  background: #2a2a2a;
  border-top: 1px solid #3a3a3a;
}

.nav-buttons {
  display: flex;
  align-items: center;
}

.stats {
  display: flex;
  gap: 16px;
  font-size: 12px;
  color: #888;
}

.stat-item {
  display: flex;
  align-items: center;
  gap: 4px;
}

.stat-item.streaming {
  color: #67c23a;
}

.stat-item.error {
  color: #f56c6c;
}

.stat-item.stopped {
  color: #909399;
}
</style>
