<!--
  截图标注面板（方案 37 B7）
  ==========================

  截图预览弹窗：显示截图（视频流抓帧或设备端截图），支持画笔/箭头/矩形/文字
  叠加标注，可导出标注后的 PNG 或直接保存原图。

  入参（props）：
    - visible: 弹窗可见性（父组件控制，关闭经 close 事件回传）
    - image: 截图数据，Blob 或 URL 字符串二选一
        - Blob → 组件内部创建 object URL，弹窗关闭/替换时回收
        - string → 直接使用，由调用方管理生命周期
    - deviceId: 下载文件名中性化来源（沿用 DeviceDetail 既有规则）

  交互：
    - pointer 事件在画布上拖拽绘制；坐标按画布显示尺寸与像素尺寸比例换算
    - 文字工具：工具栏展开行内输入框 + 「确认」，确认后点击画布放置文字
      （方案裁决 4：不用 window.prompt，避免组件测试非确定性）
    - 撤销：命令栈出栈重绘；清空：命令栈归零重绘（重绘 = 底图 + 回放命令栈）
    - 导出 PNG：canvas.toBlob('image/png') → 下载 <中性名>_screenshot_annotated.png
    - 直接保存：下载原始截图 <中性名>_screenshot.png（无标注），弹窗打开期不回收 URL

  依赖：无外部依赖（原生 canvas API），Chrome-only（DEVELOPMENT.md 约束 1）。
-->
<template>
  <el-dialog
    v-model="dialogVisible"
    class="screenshot-preview-dialog"
    title="截图预览"
    width="760px"
    :close-on-click-modal="false"
  >
    <div class="screenshot-preview">
      <div class="toolbar">
        <el-button class="tool tool-pen" :class="{ 'tool-active': tool === 'pen' }" @click="tool = 'pen'">
          画笔
        </el-button>
        <el-button class="tool tool-arrow" :class="{ 'tool-active': tool === 'arrow' }" @click="tool = 'arrow'">
          箭头
        </el-button>
        <el-button class="tool tool-rect" :class="{ 'tool-active': tool === 'rect' }" @click="tool = 'rect'">
          矩形
        </el-button>
        <el-button class="tool tool-text" :class="{ 'tool-active': tool === 'text' }" @click="tool = 'text'">
          文字
        </el-button>
        <label class="color-picker" title="标注颜色">
          <input v-model="strokeColor" type="color" class="color-input" />
        </label>
        <span class="toolbar-sep" aria-hidden="true" />
        <el-button class="btn-undo" :disabled="commands.length === 0" @click="undo">撤销</el-button>
        <el-button class="btn-clear" :disabled="commands.length === 0" @click="clearAll">清空</el-button>
        <span class="toolbar-spacer" />
        <el-button class="btn-save" @click="saveOriginal">直接保存</el-button>
        <el-button class="btn-export" type="primary" @click="exportPng">导出 PNG</el-button>
      </div>

      <!-- 文字工具行内输入区：确认后点击画布放置 -->
      <div v-if="tool === 'text'" class="text-tool-row">
        <input
          v-model="textDraft"
          class="text-draft"
          placeholder="输入文字，点「确认」后点击画布放置"
          @keydown.enter.prevent="confirmText"
        />
        <el-button class="text-confirm" size="small" @click="confirmText">确认</el-button>
        <span class="text-tool-hint">待放置：{{ confirmedText || '（未确认）' }}</span>
      </div>

      <div class="canvas-wrap">
        <canvas
          ref="canvasRef"
          class="preview-canvas"
          @pointerdown.prevent="onPointerDown"
          @pointermove="onPointerMove"
          @pointerup="onPointerUp"
          @pointerleave="onPointerUp"
        />
      </div>
    </div>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'

const props = defineProps<{
  /** 弹窗可见性（父组件控制，关闭经 close 事件回传） */
  visible: boolean
  /** 截图数据：Blob（组件自建 object URL 并回收）或 URL 字符串（调用方管理生命周期） */
  image: Blob | string | null
  /** 设备序列号：下载文件名中性化来源 */
  deviceId: string
}>()

const emit = defineEmits<{ (e: 'close'): void }>()

/** 工具态枚举：画笔 / 箭头 / 矩形 / 文字。 */
type Tool = 'pen' | 'arrow' | 'rect' | 'text'

interface Point {
  x: number
  y: number
}

interface PenCmd {
  kind: 'pen'
  points: Point[]
  color: string
}

interface ArrowCmd {
  kind: 'arrow'
  from: Point
  to: Point
  color: string
}

interface RectCmd {
  kind: 'rect'
  from: Point
  to: Point
  color: string
}

interface TextCmd {
  kind: 'text'
  at: Point
  text: string
  color: string
}

/** 已提交标注命令（重绘回放与撤销/清空的单位）。 */
type AnnotationCmd = PenCmd | ArrowCmd | RectCmd | TextCmd

/** 拖拽中的未提交形状。 */
interface DragState {
  tool: 'pen' | 'arrow' | 'rect'
  start: Point
  current: Point
  /** 画笔轨迹点（含起点）。 */
  points: Point[]
}

const canvasRef = ref<HTMLCanvasElement | null>(null)

const tool = ref<Tool>('pen')
/** 标注颜色（画笔/箭头/矩形边框/文字共用）。 */
const strokeColor = ref('#ff3b30')
/** 已提交命令栈。 */
const commands = ref<AnnotationCmd[]>([])
/** 文字工具：输入框草稿与「确认」后的待放置文字。 */
const textDraft = ref('')
const confirmedText = ref('')
const drag = ref<DragState | null>(null)

/** 基础截图（object URL 加载得到的图片元素）。 */
const baseImage = ref<HTMLImageElement | null>(null)

/** 组件自建的 object URL（image 为 Blob 时；弹窗关闭/替换时回收）。 */
const objectUrl = ref<string | null>(null)
/** objectUrl 是否为组件自建（自建才回收，外部 URL 不碰）。 */
let ownsObjectUrl = false
/** 图片加载竞态序号：忽略过期加载结果。 */
let loadSeq = 0

/** el-dialog 可见性：父组件 visible prop 驱动，关闭请求经 close 事件回传。 */
const dialogVisible = computed({
  get: () => props.visible,
  set: (value: boolean) => {
    if (!value) emit('close')
  },
})

/** 回收组件自建的 object URL（外部传入的 URL 字符串不回收）。 */
function releaseObjectUrl() {
  if (ownsObjectUrl && objectUrl.value) URL.revokeObjectURL(objectUrl.value)
  ownsObjectUrl = false
  objectUrl.value = null
}

// 弹窗可见性与入参联动：打开且入参为 Blob 时创建 object URL，关闭/替换时回收。
watch(
  () => [props.visible, props.image] as const,
  ([visible, image]) => {
    releaseObjectUrl()
    if (!visible || !image) {
      baseImage.value = null
      return
    }
    if (image instanceof Blob) {
      objectUrl.value = URL.createObjectURL(image)
      ownsObjectUrl = true
    } else {
      objectUrl.value = image
    }
  },
  { immediate: true },
)

// object URL 变化 → 加载底图（竞态序号防止旧加载覆盖新图）。
watch(
  objectUrl,
  async (url) => {
    const seq = ++loadSeq
    if (!url) {
      baseImage.value = null
      return
    }
    try {
      const image = await loadImage(url)
      if (seq !== loadSeq) return
      baseImage.value = image
    } catch {
      if (seq === loadSeq) baseImage.value = null
    }
  },
  { immediate: true },
)

// 每次打开弹窗都是全新标注会话（commands 归零）。
watch(
  () => props.visible,
  (visible) => {
    if (visible) {
      commands.value = []
      drag.value = null
    }
  },
)

/** 加载 object URL 为图片元素（自然尺寸来自图片文件本体）。 */
function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const image = new Image()
    image.onload = () => resolve(image)
    image.onerror = () => reject(new Error(`截图加载失败: ${src}`))
    image.src = src
  })
}

function dist(a: Point, b: Point): number {
  return Math.hypot(b.x - a.x, b.y - a.y)
}

/** client 坐标 → 画布像素坐标（显示尺寸与像素尺寸按比例换算）。 */
function toCanvasPoint(e: { clientX: number; clientY: number }): Point | null {
  const canvas = canvasRef.value
  if (!canvas) return null
  const rect = canvas.getBoundingClientRect()
  if (rect.width === 0 || rect.height === 0) return null
  return {
    x: ((e.clientX - rect.left) / rect.width) * canvas.width,
    y: ((e.clientY - rect.top) / rect.height) * canvas.height,
  }
}

/** 箭头头部两翼（固定 14px 长、±30° 张角）。 */
function drawArrowHead(ctx: CanvasRenderingContext2D, from: Point, to: Point) {
  const angle = Math.atan2(to.y - from.y, to.x - from.x)
  const headLength = 14
  ctx.beginPath()
  ctx.moveTo(to.x, to.y)
  ctx.lineTo(
    to.x - headLength * Math.cos(angle - Math.PI / 6),
    to.y - headLength * Math.sin(angle - Math.PI / 6),
  )
  ctx.moveTo(to.x, to.y)
  ctx.lineTo(
    to.x - headLength * Math.cos(angle + Math.PI / 6),
    to.y - headLength * Math.sin(angle + Math.PI / 6),
  )
  ctx.stroke()
}

/** 单条标注命令渲染。 */
function drawCmd(ctx: CanvasRenderingContext2D, cmd: AnnotationCmd) {
  ctx.strokeStyle = cmd.color
  ctx.lineWidth = 4
  ctx.lineCap = 'round'
  switch (cmd.kind) {
    case 'pen': {
      ctx.beginPath()
      ctx.moveTo(cmd.points[0].x, cmd.points[0].y)
      for (const point of cmd.points.slice(1)) ctx.lineTo(point.x, point.y)
      ctx.stroke()
      break
    }
    case 'arrow': {
      ctx.beginPath()
      ctx.moveTo(cmd.from.x, cmd.from.y)
      ctx.lineTo(cmd.to.x, cmd.to.y)
      ctx.stroke()
      drawArrowHead(ctx, cmd.from, cmd.to)
      break
    }
    case 'rect': {
      ctx.strokeRect(
        Math.min(cmd.from.x, cmd.to.x),
        Math.min(cmd.from.y, cmd.to.y),
        Math.abs(cmd.to.x - cmd.from.x),
        Math.abs(cmd.to.y - cmd.from.y),
      )
      break
    }
    case 'text': {
      ctx.fillStyle = cmd.color
      ctx.font = '20px sans-serif'
      ctx.fillText(cmd.text, cmd.at.x, cmd.at.y)
      break
    }
  }
}

/** 全量重绘：清屏 → 底图 → 命令栈回放 → 拖拽中的未提交形状。 */
function redraw() {
  const canvas = canvasRef.value
  if (!canvas) return
  const ctx = canvas.getContext('2d')
  if (!ctx) return
  ctx.clearRect(0, 0, canvas.width, canvas.height)
  if (baseImage.value) ctx.drawImage(baseImage.value, 0, 0, canvas.width, canvas.height)
  for (const cmd of commands.value) drawCmd(ctx, cmd)
  const d = drag.value
  if (!d) return
  if (d.tool === 'pen') {
    drawCmd(ctx, { kind: 'pen', points: d.points.length ? d.points : [d.current], color: strokeColor.value })
  } else if (d.tool === 'arrow') {
    drawCmd(ctx, { kind: 'arrow', from: d.start, to: d.current, color: strokeColor.value })
  } else {
    drawCmd(ctx, { kind: 'rect', from: d.start, to: d.current, color: strokeColor.value })
  }
}

// 底图就绪后按自然尺寸规格化画布并重绘。
watch(
  baseImage,
  () => {
    const canvas = canvasRef.value
    const image = baseImage.value
    if (!canvas || !image) return
    canvas.width = image.naturalWidth
    canvas.height = image.naturalHeight
    redraw()
  },
  { immediate: true },
)

function onPointerDown(e: PointerEvent) {
  const point = toCanvasPoint(e)
  if (!point) return
  const active = tool.value
  if (active === 'text') {
    // 文字工具：未确认文案时不落字
    if (!confirmedText.value) return
    commands.value.push({ kind: 'text', at: point, text: confirmedText.value, color: strokeColor.value })
    redraw()
    return
  }
  drag.value = { tool: active, start: point, current: point, points: [point] }
  redraw()
}

function onPointerMove(e: PointerEvent) {
  const d = drag.value
  if (!d) return
  const point = toCanvasPoint(e)
  if (!point) return
  d.current = point
  if (d.tool === 'pen') d.points.push(point)
  redraw()
}

function onPointerUp() {
  const d = drag.value
  drag.value = null
  if (!d) return
  // 位移过小的拖拽视为误触不提交（避免噪点/零宽矩形）
  if (d.tool === 'pen') {
    if (d.points.length >= 2 && dist(d.points[0], d.current) >= 2) {
      commands.value.push({ kind: 'pen', points: [...d.points], color: strokeColor.value })
    }
  } else if (d.tool === 'arrow') {
    if (dist(d.start, d.current) >= 2) {
      commands.value.push({ kind: 'arrow', from: d.start, to: d.current, color: strokeColor.value })
    }
  } else if (Math.abs(d.current.x - d.start.x) >= 2 || Math.abs(d.current.y - d.start.y) >= 2) {
    commands.value.push({ kind: 'rect', from: d.start, to: d.current, color: strokeColor.value })
  }
  redraw()
}

/** 撤销：命令栈出栈并重绘。 */
function undo() {
  if (commands.value.length === 0) return
  commands.value.pop()
  drag.value = null
  redraw()
}

/** 清空：命令栈归零并重绘（回到纯底图）。 */
function clearAll() {
  commands.value = []
  drag.value = null
  redraw()
}

/** 文字工具「确认」：把输入框草稿提升为待放置文字。 */
function confirmText() {
  confirmedText.value = textDraft.value
}

onUnmounted(() => {
  releaseObjectUrl()
})

/** 设备序列号中性化（沿用 DeviceDetail 既有下载命名规则）。 */
const neutralName = computed(() => props.deviceId.replace(/[^a-zA-Z0-9._-]/g, '_'))
const rawFilename = computed(() => `${neutralName.value}_screenshot.png`)
const annotatedFilename = computed(() => `${neutralName.value}_screenshot_annotated.png`)

/** Blob 下载（RecordingPanel 同款范式）：a.download 触发后立即回收。 */
function triggerBlobDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

/** 直接保存：下载原始截图（无标注覆盖）；弹窗仍打开，底图 URL 不回收。 */
function saveOriginal() {
  if (!objectUrl.value) return
  const a = document.createElement('a')
  a.href = objectUrl.value
  a.download = rawFilename.value
  a.click()
}

/** 导出 PNG：画布（底图 + 标注）经 canvas.toBlob 产出后下载。 */
function exportPng() {
  const canvas = canvasRef.value
  if (!canvas) return
  canvas.toBlob((blob) => {
    if (!blob) return
    triggerBlobDownload(blob, annotatedFilename.value)
  }, 'image/png')
}

/**
 * 当前已提交标注数（供父组件判断是否存在未导出的标注；
 * 测试亦用它断言撤销/清空的命令栈重置语义）。
 */
defineExpose({ getAnnotationCount: () => commands.value.length })
</script>

<style scoped>
.screenshot-preview {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.tool-active {
  border-color: #409eff;
  color: #409eff;
}

.color-picker {
  display: inline-flex;
  align-items: center;
  cursor: pointer;
}

.color-input {
  width: 28px;
  height: 28px;
  padding: 0;
  border: none;
  background: none;
  cursor: pointer;
}

.toolbar-sep {
  width: 1px;
  height: 20px;
  background: #dcdfe6;
}

.toolbar-spacer {
  flex: 1;
}

.text-tool-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.text-draft {
  flex: 1;
  min-width: 0;
  padding: 6px 8px;
  border: 1px solid #dcdfe6;
  border-radius: 4px;
  font-size: 13px;
}

.text-tool-hint {
  font-size: 12px;
  color: #909399;
  white-space: nowrap;
}

.canvas-wrap {
  max-height: 60vh;
  overflow: auto;
  display: flex;
  justify-content: center;
  background: #f5f7fa;
  border-radius: 4px;
}

.preview-canvas {
  display: block;
  max-width: 100%;
  height: auto;
  cursor: crosshair;
  touch-action: none;
}
</style>