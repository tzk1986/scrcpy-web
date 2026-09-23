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
    - Page Visibility 降载：tab 隐藏时停 fps 上报与输入处理（方案 17 实施项 4）
    - 实验开关：URL ?swdecode=1 强制软解，诊断 Rockchip 硬解花屏

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

      <!-- 截图模式只读提示徽标 -->
      <div v-if="mode === 'screenshot' && state === 'streaming'" class="readonly-badge">
        只读预览 · 输入不可用
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
        <span v-if="mode === 'h264'" class="stat-item">丢帧: {{ droppedFrames }}</span>
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
import { H264VideoStream, type H264StreamOptions, type H264StreamState } from '@/services/h264VideoStream'
import {
  computeFallbackThresholds,
  evaluateH264Fallback,
  evaluateH264Recovery,
  RECOVERY_THRESHOLDS,
  type RecoverySample,
} from '@/services/videoFallback'
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
const droppedFrames = ref(0)
const error = ref<string | null>(null)
const mode = ref<'h264' | 'screenshot'>('screenshot')

const stateLabel = computed(() => {
  switch (state.value) {
    case 'idle': return '连接中'
    case 'configuring': return '配置中'
    case 'streaming': return '直播中'
    case 'error': return '错误'
    case 'stopped': return '已停止'
    default: return '未知'
  }
})

let ws: WebSocketService | null = null
let videoStream: VideoStream | null = null
let h264Stream: H264VideoStream | null = null
let inputController: InputController | null = null
/** 停止 H264 stats 上报定时器（onMounted 内赋值，onUnmounted 调用） */
let stopStatsTimer: (() => void) | null = null

// ===== H264 回退/回切状态机（顶层，供 onMounted/onUnmounted 共用） =====

/** H264 启动时间戳（每次回切重置，用于 hard-timeout 判定） */
let h264StartedAt = 0
/** 是否已执行过回退（回切时复位） */
let h264FallbackDone = false
/** 自适应码率 fps 上报定时器 */
let h264StatsTimer: number | null = null
/** 截图模式下 H264 恢复探测定时器 */
let probeTimer: number | null = null
/** 恢复探测的帧计数基准（计算窗口帧数差值） */
let lastProbeFrameCount = 0
/** 恢复探测采样序列 */
const recoverySamples: RecoverySample[] = []
/** 回切后防横跳冷却截止时间：冷却期内不再发起回切 */
let resumeCooldownUntil = 0

function stopH264Stats() {
  if (h264StatsTimer !== null) {
    clearInterval(h264StatsTimer)
    h264StatsTimer = null
  }
}

/** 每 2s 向服务端上报实测帧率（自适应码率决策）。码率重启宽限期内暂停。 */
function startH264StatsReporting() {
  stopH264Stats()
  h264StatsTimer = window.setInterval(() => {
    if (!h264Stream || !ws) return
    if (Date.now() < h264Stream.restartGraceUntil) return
    // 静止画面下几乎不出帧，实测 fps≈0 与拥塞同签名；仅 fps≥1 时上报
    const measured = h264Stream.stats.fps
    if (measured < 1) return
    ws.send({ op: 'stats', fps: measured })
  }, 2000)
}

/** 将组件 state/fps/frameCount 绑定到 h264 流（初次启动与回切后重挂） */
function attachH264Handlers() {
  if (!h264Stream) return
  h264Stream.setStateChangeHandler((newState) => {
    console.log('[VideoPlayer] H264 stream state changed:', newState)
    state.value = newState
    if (newState === 'error') {
      error.value = h264Stream?.stats.error || 'Unknown error'
    }
    checkH264Fallback()
  })
  h264Stream.setStatsUpdateHandler((stats) => {
    fps.value = stats.fps
    frameCount.value = stats.frameCount
    droppedFrames.value = stats.droppedFrames
    checkH264Fallback()
  })
}

/** H264 自动回退 watchdog（判据见 services/videoFallback.ts） */
function checkH264Fallback() {
  if (!h264Stream || h264FallbackDone) return
  // 码率重启宽限期内（服务端预告 restarting）黑屏 1-3s 属预期，暂停判定
  if (Date.now() < h264Stream.restartGraceUntil) return
  const s = h264Stream.stats
  const r = evaluateH264Fallback({
    state: s.state,
    frameCount: s.frameCount,
    lastFrameTime: s.lastFrameTime,
    startedAt: h264StartedAt,
    now: Date.now(),
  }, computeFallbackThresholds(h264Stream.keepaliveIntervalMs))
  if (!r.fallback) return
  h264FallbackDone = true
  console.warn('[VideoPlayer] H264 fallback triggered:', r.reason,
    'state:', s.state, 'frameCount:', s.frameCount, 'error:', s.error)
  stopH264Stats()
  // 保留实例与 ws 消息处理器，进入探测模式观察码流是否恢复
  h264Stream.suspend()
  startScreenshotMode()
  startRecoveryProbe()
}

function stopRecoveryProbe() {
  if (probeTimer !== null) {
    clearInterval(probeTimer)
    probeTimer = null
  }
  recoverySamples.length = 0
  lastProbeFrameCount = 0
}

/**
 * 截图模式下周期性探测 H264 码流恢复情况。
 * 连续多个窗口（RECOVERY_THRESHOLDS）帧数达标才回切，防止
 * 「恢复几秒又卡死」的横跳；回切后设 30s 冷却，冷却内再次
 * 回退则本轮不再自动回切。
 */
function startRecoveryProbe() {
  stopRecoveryProbe()
  probeTimer = window.setInterval(() => {
    if (!h264Stream || mode.value !== 'screenshot') return
    if (Date.now() < resumeCooldownUntil) return
    const ps = h264Stream.probeStats
    recoverySamples.push({ frames: ps.frameCount - lastProbeFrameCount, at: Date.now() })
    lastProbeFrameCount = ps.frameCount
    if (recoverySamples.length > RECOVERY_THRESHOLDS.WINDOWS + 3) recoverySamples.shift()
    if (!evaluateH264Recovery(recoverySamples, Date.now())) return

    console.warn('[VideoPlayer] H264 stream recovered, switching back from screenshot mode')
    stopRecoveryProbe()
    videoStream?.stop()
    videoStream = null
    h264FallbackDone = false
    h264StartedAt = Date.now()
    resumeCooldownUntil = Date.now() + 30_000
    attachH264Handlers()
    h264Stream.resume()
    startH264StatsReporting()
    mode.value = 'h264'
  }, RECOVERY_THRESHOLDS.WINDOW_MS)
}

/** 检测浏览器是否支持 WebCodecs VideoDecoder */
function isWebCodecsSupported(): boolean {
  return typeof VideoDecoder !== 'undefined' && typeof EncodedVideoChunk !== 'undefined'
}

/**
 * 读取解码硬件加速偏好（实验开关，方案 17 实施项 4）。
 * URL 加 ?swdecode=1 强制软解，用于诊断 .18（Rockchip）硬解花屏；
 * 不设置则返回 undefined，走浏览器默认。
 */
function readHardwareAcceleration(): HardwareAcceleration | undefined {
  return new URLSearchParams(window.location.search).get('swdecode') === '1'
    ? 'prefer-software'
    : undefined
}

/**
 * Page Visibility 降载（方案 17 实施项 4）。
 * tab 隐藏时：停 fps 上报（后台定时器被浏览器节流，上报数据失真会干扰
 * 服务端自适应码率决策）+ 停用输入处理；解码保留（继续收流）。
 * 恢复可见时重启 fps 上报并重新启用输入。
 */
function onVisibilityChange() {
  const hidden = document.hidden
  if (hidden) {
    stopH264Stats()
  } else if (h264Stream && !h264Stream.suspended) {
    startH264StatsReporting()
  }
  inputController?.setEnabled(!hidden)
}

/** 启动截图回退模式 */
function startScreenshotMode() {
  console.log('[VideoPlayer] Starting screenshot fallback mode')
  mode.value = 'screenshot'
  state.value = 'idle'
  videoStream = new VideoStream(props.deviceId, canvasRef.value!, ws!)

  videoStream.setStateChangeHandler((newState) => {
    console.log('[VideoPlayer] Screenshot stream state changed:', newState)
    state.value = newState
    if (newState === 'error') {
      error.value = videoStream?.stats.error || 'Unknown error'
    }
  })

  videoStream.setStatsUpdateHandler((stats) => {
    fps.value = stats.fps
    frameCount.value = stats.frameCount
  })

  videoStream.start()
}

onMounted(async () => {
  if (!canvasRef.value) return

  console.log('[VideoPlayer] Component mounted, deviceId:', props.deviceId)
  console.log('[VideoPlayer] Device resolution:', props.deviceWidth, 'x', props.deviceHeight)

  // 设置 canvas 初始尺寸（使用设备实际分辨率，保持比例）
  // CSS 尺寸由 width:100%; height:100%; object-fit:contain 控制，
  // 自适应容器大小并维持视频原始比例（含 letterboxing）。
  const initWidth = props.deviceWidth || 1080
  const initHeight = props.deviceHeight || 1920
  canvasRef.value.width = initWidth
  canvasRef.value.height = initHeight

  // Page Visibility 降载（方案 17 实施项 4）
  document.addEventListener('visibilitychange', onVisibilityChange)

  // 初始化 WebSocket（用于视频流和输入事件）
  const wsUrl = `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/ws/video/${props.deviceId}`
  console.log('[VideoPlayer] Creating WebSocket:', wsUrl)
  ws = new WebSocketService(wsUrl)
  ws.setErrorHandler((e) => {
    console.error('[VideoPlayer] WebSocket error:', e)
  })
  ws.setCloseHandler(() => {
    console.log('[VideoPlayer] WebSocket closed')
    state.value = 'stopped'
  })

  // 初始化输入控制器
  inputController = new InputController(
    ws,
    props.deviceWidth || 1080,
    props.deviceHeight || 1920
  )

  // 根据浏览器支持选择视频流模式
  console.log('[VideoPlayer] WebCodecs supported:', isWebCodecsSupported())
  if (isWebCodecsSupported()) {
    console.log('[VideoPlayer] Using H264 video stream mode')
    mode.value = 'h264'
    const hwAccel = readHardwareAcceleration()
    if (hwAccel) {
      console.warn('[VideoPlayer] swdecode=1: forcing software decode:', hwAccel)
    }
    const streamOpts: H264StreamOptions = { hardwareAcceleration: hwAccel }
    h264Stream = new H264VideoStream(ws, canvasRef.value, streamOpts)
    h264StartedAt = Date.now()
    h264FallbackDone = false
    resumeCooldownUntil = 0

    // 重要：先注册消息处理器，再建立 WebSocket 连接
    // 避免 config 消息在处理器注册前到达被丢弃
    attachH264Handlers()
    h264Stream.start()
    ws.connect()

    startH264StatsReporting()
    stopStatsTimer = () => stopH264Stats()
  } else {
    console.log('[VideoPlayer] WebCodecs NOT supported, using screenshot mode')
    startScreenshotMode()
  }

  // 挂载时 tab 可能已处于隐藏态（后台标签打开设备页），按当前可见性
  // 初始化一次降载状态，不必等首个 visibilitychange 事件才纠正。
  // 此时 h264Stream/inputController 均已就绪；若隐藏，onVisibilityChange
  // 会停掉刚启动的 fps 上报（startH264StatsReporting 内部先 stopH264Stats，
  // 不存在双重定时器）。
  if (document.hidden) {
    console.log('[VideoPlayer] Mounted while tab hidden, applying load-shedding')
    onVisibilityChange()
  }
})

onUnmounted(() => {
  document.removeEventListener('visibilitychange', onVisibilityChange)
  stopStatsTimer?.()
  stopRecoveryProbe()
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
  /* CSS 尺寸填满容器，object-fit:contain 保持视频原始比例并自动 letterbox */
  width: 100%;
  height: 100%;
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

.readonly-badge {
  position: absolute;
  top: 8px;
  left: 8px;
  padding: 4px 10px;
  font-size: 12px;
  color: #e6a23c;
  background: rgba(0, 0, 0, 0.65);
  border: 1px solid rgba(230, 162, 60, 0.5);
  border-radius: 4px;
  z-index: 5;
  pointer-events: none;
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
