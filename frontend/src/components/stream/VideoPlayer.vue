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
    - 物理键盘镜像：点击视频区捕获焦点，按键/滚轮/IME/粘贴直通设备，
      Esc×2 退出，失焦补 up 防卡键；「文本」对话框与剪贴板双向（方案 54）

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
        @wheel.prevent="onWheel"
        @contextmenu.prevent
      ></canvas>

      <!-- 键盘捕获焦点目标（方案54 D1）：IME 候选窗需可编辑元素获焦，
           视觉隐藏但留在容器内（候选窗跟随光标位置，不可移出屏幕外） -->
      <input
        ref="captureInputRef"
        class="keyboard-capture"
        autocomplete="off"
        aria-label="键盘捕获"
        @keydown="onCaptureKeyDown"
        @keyup="onCaptureKeyUp"
        @compositionstart="onCompositionStart"
        @compositionend="onCompositionEnd"
        @paste="onCapturePaste"
        @focus="onCaptureFocus"
        @blur="onCaptureBlur"
      />

      <!-- 状态覆盖层（首次连接期显示；截图态与恢复期不显示，保留画面可见） -->
      <div
        v-if="state !== 'streaming' && state !== 'screenshot' && !recoveringBadge"
        class="status-overlay"
      >
        <div v-if="state === 'idle' || state === 'configuring'" class="status-text">正在连接...</div>
        <div v-if="state === 'error'" class="status-text error">
          连接错误: {{ error }}
          <el-button size="small" @click="reconnect">重新连接</el-button>
        </div>
        <div v-if="state === 'stopped'" class="status-text">已断开</div>
      </div>

      <!-- 恢复角标：已出过画面后的空闲/配置期，保留下层冻结帧可见，仅右上角小字提示 -->
      <div v-if="recoveringBadge" class="recovering-tip">正在恢复画面…</div>

      <!-- 截图模式提示徽标（方案 51 F1-06：ws 已连接、输入可用但帧走 HTTP，延迟较高） -->
      <div v-if="mode === 'screenshot' && state === 'streaming'" class="readonly-badge">
        截图预览 · 输入延迟较高
      </div>

      <!-- 键盘捕获角标（方案54）：点击即退出捕获 -->
      <div v-if="capturing" class="keyboard-badge" @click="exitCapture">
        键盘已捕获 · 连按两次 Esc 或点此退出
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
          <el-button
            size="small"
            :disabled="!wsConnected"
            :title="wsConnected ? '输入文本 / 剪贴板' : '连接未就绪，暂不可用'"
            @click="openInputDialog"
          >
            文本
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

    <!-- 输入文本对话框（方案54 D2/D3）：文本注入 + 剪贴板双向 + prefer-text 开关 -->
    <el-dialog v-model="inputDialogVisible" title="输入文本" width="420px">
      <textarea
        v-model="dialogText"
        class="dialog-textarea"
        rows="3"
        placeholder="输入要注入设备的文本"
      ></textarea>
      <div class="dialog-meta">
        <span>{{ dialogText.length }} 字符</span>
        <label class="prefer-text">
          <input type="checkbox" v-model="preferText" @change="applyPreferText" />
          字母改用文本注入
        </label>
      </div>
      <div class="dialog-actions">
        <el-button size="small" :disabled="!wsConnected" @click="injectDialogText">注入文本</el-button>
        <el-button size="small" :disabled="!wsConnected" @click="pasteToDevice">粘贴到设备</el-button>
        <el-button size="small" :disabled="!wsConnected" @click="pullClipboard(0)">取设备剪贴板</el-button>
        <el-button size="small" :disabled="!wsConnected" @click="pullClipboard(1)">取设备选中文本</el-button>
      </div>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { WebSocketService } from '@/services/websocket'
import { api } from '@/services/api'
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

/** 方案 40 消费点 2：h264 观看会话首帧渲染后抓帧上抛，供列表缩略图缓存 */
const emit = defineEmits<{ (e: 'first-frame', blob: Blob): void }>()

const containerRef = ref<HTMLElement>()
const canvasRef = ref<HTMLCanvasElement>()

const state = ref<VideoStreamState | H264StreamState | 'screenshot'>('idle')
/** 是否已进入过 streaming（首次连接用全屏提示，此后恢复期改用角标） */
const hasStreamedOnce = ref(false)
const fps = ref(0)
const frameCount = ref(0)
const droppedFrames = ref(0)
const error = ref<string | null>(null)
const mode = ref<'h264' | 'screenshot'>('screenshot')

// ===== 方案54：键盘捕获 / 输入文本对话框 / 剪贴板双向 =====

const captureInputRef = ref<HTMLInputElement>()
/** 键盘捕获中（隐藏输入获焦即置位，角标提示 + blur 退出） */
const capturing = ref(false)
/** ws 就绪态（setOpenHandler/setCloseHandler 维护；D4：未就绪禁用文本入口） */
const wsConnected = ref(false)
const inputDialogVisible = ref(false)
const dialogText = ref('')
/** D2 prefer-text 开关：字母注入路径（localStorage 持久化，默认关=keyevent） */
const PREFER_TEXT_KEY = 'osc.keyboardPreferText'
const preferText = ref(localStorage.getItem(PREFER_TEXT_KEY) === '1')
/** 800ms 内第二次 Esc 退出捕获；首次照常注入 ESC(111) */
let lastEscapeAt = 0

const stateLabel = computed(() => {
  switch (state.value) {
    case 'idle': return '连接中'
    case 'configuring': return '配置中'
    case 'streaming': return '直播中'
    case 'screenshot': return '截图模式'
    case 'error': return '错误'
    case 'stopped': return '已停止'
    default: return '未知'
  }
})

/** 恢复角标：已出过画面后又回到 idle/configuring（回切/重连的恢复期） */
const recoveringBadge = computed(
  () => hasStreamedOnce.value && (state.value === 'idle' || state.value === 'configuring'),
)

// state 的一切流转都汇于此处（h264 与截图两条链路的 onStateChange 均赋值 state），
// 首次进入 streaming 即置位 hasStreamedOnce；重复置位幂等。
// 方案 40 消费点 2：h264 首帧已绘制进渲染 canvas，此刻抓帧方向天然正，
// 上抛给列表页写入缩略图缓存；抓帧失败静默（不影响观看会话）。
watch(state, (s) => {
  if (s !== 'streaming' || hasStreamedOnce.value) return
  hasStreamedOnce.value = true
  if (mode.value === 'h264' && h264Stream) {
    h264Stream
      .captureFrame()
      .then((blob) => emit('first-frame', blob))
      .catch(() => {})
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
 * 近 3 窗总帧数达聚合下限且末窗有帧才回切（evaluateH264Recovery），
 * 防止「恢复几秒又卡死」的横跳；回切后设 30s 冷却，冷却内再次
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

/**
 * WS 重连成功处理器（重连 socket onopen 后由 WebSocketService 触发，R1）。
 *
 * 重连成功是确定性恢复信号：旧会话必死、服务端为新连接重建会话
 * （config + 初始 IDR）。截图模式下 suspend 探针依赖挂起期帧计数，
 * resume 退出挂起后探针无帧可测（帧恢复走解码路径）、mode 永久滞留
 * 截图——因此不修补频死的探针，而是直接复位回退状态机，经既有
 * config→streaming 路径完成回切（T3/T5 已评审机制保持不变；
 * 回退 watchdog 随 h264FallbackDone 复位重新武装，真死流仍会再回退）。
 */
function handleWsReconnect() {
  if (!h264Stream) return
  if (mode.value === 'screenshot') {
    console.warn('[VideoPlayer] WS reconnected: resetting fallback state, back to H264')
    stopRecoveryProbe()
    videoStream?.stop()
    videoStream = null
    h264FallbackDone = false
    h264StartedAt = Date.now()
    resumeCooldownUntil = 0
    attachH264Handlers()
    h264Stream.resume()
    startH264StatsReporting()
    mode.value = 'h264'
    // 恢复期 UI：进 configuring 显示「正在恢复画面…」角标（不黑屏、不透明覆盖）
    state.value = 'configuring'
  } else {
    // 非截图模式：resume 自带挂起守卫，仅挂起时真正恢复
    h264Stream.resume()
  }
}

/** 启动截图回退模式 */
function startScreenshotMode() {
  mode.value = 'screenshot'
  // 独立 'screenshot' 状态：覆盖层条件不含它，回退过程不闪现「正在连接」
  state.value = 'screenshot'
  videoStream = new VideoStream(props.deviceId, canvasRef.value!)

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

  videoStream.start()
}

onMounted(async () => {
  if (!canvasRef.value) return

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
  ws = new WebSocketService(wsUrl)
  ws.setErrorHandler((e) => {
    console.error('[VideoPlayer] WebSocket error:', e)
  })
  ws.setOpenHandler(() => {
    wsConnected.value = true
  })
  ws.setCloseHandler(() => {
    state.value = 'stopped'
    wsConnected.value = false
  })

  // 初始化输入控制器
  inputController = new InputController(
    ws,
    props.deviceWidth || 1080,
    props.deviceHeight || 1920
  )
  inputController.setPreferText(preferText.value)

  // 方案54 G3：包装消息通道，截流剪贴板应答（clipboard/clipboard_error）。
  // WebSocketService.setMessageHandler 为单回调覆盖式，h264/截图链路各自注册，
  // 此处接管注册入口：剪贴板 JSON 帧转 InputController，其余原样转发给后注册者。
  const rawSetMessageHandler = ws.setMessageHandler.bind(ws)
  let downstreamHandler: ((data: unknown) => void) | null = null
  ws.setMessageHandler = (handler: (data: unknown) => void) => {
    downstreamHandler = handler
    rawSetMessageHandler((data) => {
      if (typeof data === 'string') {
        try {
          const msg = JSON.parse(data) as { type?: string; text?: string }
          if (msg && (msg.type === 'clipboard' || msg.type === 'clipboard_error')) {
            inputController?.handleClipboardMessage({ type: msg.type, text: msg.text })
            return
          }
        } catch {
          /* 非 JSON 文本帧：原样转发 */
        }
      }
      downstreamHandler?.(data)
    })
  }

  // 输入事件驱动截屏（方案 27）：仅截图模式下转发给 VideoStream.notifyInput
  inputController.setInputHandler(() => {
    if (mode.value === 'screenshot') videoStream?.notifyInput()
  })

  // 根据浏览器支持选择视频流模式
  if (isWebCodecsSupported()) {
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
    // H264VideoStream.start 自带 resume 重连回调（挂起守卫、空转安全，
    // VideoPlayer.test 与 h264VideoStream.test 均有覆盖）；此处覆盖为组件级
    // 复合处理器（resume + 截图模式回退状态机复位，R1）。注意 reconnect()
    // 按钮会使 start() 重新注册，需再次覆盖（见下）。
    ws.setReconnectHandler(() => handleWsReconnect())
    ws.connect()

    startH264StatsReporting()
    stopStatsTimer = () => stopH264Stats()
  } else {
    startScreenshotMode()
    // 方案 51 F1-06：截图模式帧走 HTTP（VideoStream 不用 ws），ws 仅作输入
    // 通道——原分支从不 connect，非 WebCodecs 下输入全部静默丢弃。
    ws.connect()
  }

  // 挂载时 tab 可能已处于隐藏态（后台标签打开设备页），按当前可见性
  // 初始化一次降载状态，不必等首个 visibilitychange 事件才纠正。
  // 此时 h264Stream/inputController 均已就绪；若隐藏，onVisibilityChange
  // 会停掉刚启动的 fps 上报（startH264StatsReporting 内部先 stopH264Stats，
  // 不存在双重定时器）。
  if (document.hidden) {
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
    // 方案54 D1=A：点击视频区即捕获键盘
    captureInputRef.value?.focus()
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

// ===== 方案54：键盘捕获事件 =====

/**
 * 浏览器致命组合放行清单（不 preventDefault、不注入，交回浏览器）：
 * meta 组合一律放行；Ctrl+R/W/T/N/L/J、Ctrl+Shift+I/J（开发者工具）、F5/F12。
 */
function isBrowserReserved(e: KeyboardEvent): boolean {
  if (e.metaKey) return true
  if (e.code === 'F5' || e.code === 'F12') return true
  if (e.ctrlKey) {
    if (e.shiftKey) return e.code === 'KeyI' || e.code === 'KeyJ'
    return ['KeyR', 'KeyW', 'KeyT', 'KeyN', 'KeyL', 'KeyJ'].includes(e.code)
  }
  return false
}

function onCaptureKeyDown(e: KeyboardEvent) {
  if (isBrowserReserved(e)) return
  if (e.code === 'Escape' && !e.ctrlKey && !e.altKey) {
    const now = Date.now()
    if (now - lastEscapeAt < 800) {
      lastEscapeAt = 0
      exitCapture()
      return
    }
    lastEscapeAt = now
  }
  e.preventDefault()
  inputController?.handleKeyDown(e)
}

function onCaptureKeyUp(e: KeyboardEvent) {
  inputController?.handleKeyUp(e)
}

function onCaptureFocus() {
  capturing.value = true
}

function onCaptureBlur() {
  capturing.value = false
  inputController?.releaseAllKeys()
}

/** 角标点击/双击 Esc 显式退出（不依赖 blur 派发：未聚焦时 blur() 不触发事件） */
function exitCapture() {
  capturing.value = false
  inputController?.releaseAllKeys()
  captureInputRef.value?.blur()
}

function onCompositionStart() {
  inputController?.setComposing(true)
}

function onCompositionEnd(e: CompositionEvent) {
  inputController?.handleCompositionEnd(e.data ?? '')
}

/** 粘贴 → 设备剪贴板写入并粘贴（paste=true 中文主路径，见 API.md B.2） */
function onCapturePaste(e: ClipboardEvent) {
  e.preventDefault()
  const text = e.clipboardData?.getData('text') ?? ''
  if (text) inputController?.sendClipboardSet(text, true)
}

function onWheel(e: WheelEvent) {
  if (canvasRef.value && inputController) {
    inputController.handleWheel(e, canvasRef.value)
  }
}

// ===== 方案54：输入文本对话框 =====

function openInputDialog() {
  inputDialogVisible.value = true
}

function applyPreferText() {
  inputController?.setPreferText(preferText.value)
  localStorage.setItem(PREFER_TEXT_KEY, preferText.value ? '1' : '0')
}

function injectDialogText() {
  if (dialogText.value) inputController?.sendText(dialogText.value)
}

function pasteToDevice() {
  if (dialogText.value) inputController?.sendClipboardSet(dialogText.value, true)
}

/** 拉取设备剪贴板（copyKey=1 先注入复制键取选中文本）回填输入框 */
async function pullClipboard(copyKey: number) {
  const text = await inputController?.requestClipboard(copyKey)
  if (text === undefined) return
  if (text === null) {
    ElMessage.warning('设备剪贴板不可用或应答超时')
    return
  }
  dialogText.value = text
}

async function reconnect() {
  error.value = null
  state.value = 'idle'

  if (h264Stream) {
    h264Stream.start()
    // start() 重新注册了服务级 resume 回调，需再次覆盖为组件级复合处理器
    ws?.setReconnectHandler(() => handleWsReconnect())
  } else if (videoStream) {
    await videoStream.start()
  }
}

/**
 * 截图快照（方案 40 A，供 DeviceDetail 截图按钮经模板 ref 调用）。
 * h264 流观看中：抓渲染帧（方向与视频流一致，天然正）；
 * 其余状态（configuring/截图模式/已停止）：回退设备端截图
 * （后端判据重建后方向已修正，见方案 40 B）。
 */
defineExpose({
  async captureSnapshot(): Promise<{ blob: Blob; source: 'stream' | 'fallback' }> {
    if (mode.value === 'h264' && h264Stream && h264Stream.state === 'streaming') {
      const blob = await h264Stream.captureFrame()
      return { blob, source: 'stream' }
    }
    const blob = await api.screenshot(props.deviceId)
    return { blob, source: 'fallback' }
  },
})
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

/* 恢复角标（方案 19 实施项 4）：右上角半透明小字，不遮挡冻结帧、不拦截输入 */
.recovering-tip {
  position: absolute;
  top: 8px;
  right: 8px;
  padding: 4px 10px;
  font-size: 12px;
  color: #fff;
  background: rgba(0, 0, 0, 0.5);
  border-radius: 4px;
  z-index: 10;
  pointer-events: none;
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
  align-items: baseline;
  gap: 16px;
  font-size: 12px;
  color: #888;
}

/* 纯文本项不得嵌套 flex：line-height:normal 下含中文/纯拉丁行盒度量不同，
   内层 center 会使「FPS: 0」等纯拉丁项基线上移（0.7.14 覆盖重打修复） */
.stat-item {
  display: inline;
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

/* 键盘捕获焦点目标（方案54）：视觉隐藏但留在画面内，IME 候选窗跟随其位置 */
.keyboard-capture {
  position: absolute;
  left: 8px;
  bottom: 8px;
  width: 1px;
  height: 1px;
  opacity: 0;
  border: none;
  outline: none;
  padding: 0;
  z-index: 1;
}

/* 捕获角标（方案54）：右下角，点击退出捕获 */
.keyboard-badge {
  position: absolute;
  bottom: 8px;
  right: 8px;
  padding: 4px 10px;
  font-size: 12px;
  color: #67c23a;
  background: rgba(0, 0, 0, 0.65);
  border: 1px solid rgba(103, 194, 58, 0.5);
  border-radius: 4px;
  z-index: 10;
  cursor: pointer;
  user-select: none;
}

.dialog-textarea {
  width: 100%;
  box-sizing: border-box;
  resize: vertical;
  font: inherit;
  padding: 6px 8px;
  border: 1px solid #dcdfe6;
  border-radius: 4px;
}

.dialog-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin: 8px 0;
  font-size: 12px;
  color: #888;
}

.prefer-text {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  cursor: pointer;
}

.dialog-actions {
  display: flex;
  gap: 8px;
}
</style>
