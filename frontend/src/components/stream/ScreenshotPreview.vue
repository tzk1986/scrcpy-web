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
      （方案裁决 4：不用 window.prompt，避免组件测试非确定性）；
      待放置时悬停画布显示半透明 ghost 预览，点击才落字
    - 命中编辑：点击已放置标注 → 选中（虚线框）并进入移动（拖动实时平移）；
      Delete/Backspace 删除选中；双击文字 → 原地二次编辑（确认不发新命令）；
      Escape 退出编辑/取消选中；命中检测走 toCanvasPoint 比例换算坐标
    - 字号/线宽按「相对图宽比例」存储（annotationModel），渲染时 ×canvas.width 换算
    - 参数行：6 预设色板 + 自定义色 + 线宽/字号滑块（原生 range，按当前工具显隐），
      右侧标签显示换算后像素值；画布滚轮也可调参（×1.1/÷1.1）
    - 快捷键（window 监听，弹窗可见期间生效）：数字键 1-N 切工具（按 TOOL_KEYS 表）、
      Ctrl/Cmd+Z 撤销、Ctrl/Cmd+Shift+Z 重做、Ctrl/Cmd+S 导出；输入框聚焦时仅放行 Escape
    - 撤销/重做双栈：undo 出栈入 redo，redo 回放，落新命令清 redo 栈；
      清空：双栈归零重绘（重绘 = 底图 + 回放命令栈）
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
        <!-- 工具组 -->
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
        <span class="toolbar-sep" aria-hidden="true" />
        <!-- 操作组（T5 在此组内加「复制」按钮） -->
        <el-button class="btn-undo" :disabled="commands.length === 0" @click="undo">撤销</el-button>
        <el-button class="btn-redo" :disabled="redoStack.length === 0" @click="redo">重做</el-button>
        <el-button class="btn-clear" :disabled="commands.length === 0" @click="clearAll">清空</el-button>
        <span class="toolbar-spacer" />
        <!-- 保存/导出组 -->
        <el-button class="btn-save" @click="saveOriginal">直接保存</el-button>
        <el-button class="btn-export" type="primary" @click="exportPng">导出 PNG</el-button>
      </div>

      <!-- 参数行：预设色板 + 自定义色 + 线宽/字号滑块（按当前工具显隐） -->
      <div class="param-row">
        <button
          v-for="preset in PRESET_COLORS"
          :key="preset"
          type="button"
          class="color-swatch"
          :class="{ 'swatch-active': strokeColor === preset }"
          :style="{ background: preset }"
          :title="`标注颜色 ${preset}`"
          @click="strokeColor = preset"
        />
        <label class="color-picker" title="自定义标注颜色">
          <input v-model="strokeColor" type="color" class="color-input" />
        </label>
        <template v-if="showLineSlider">
          <input
            type="range"
            class="line-width-slider"
            :min="LINE_RATIO_MIN"
            :max="LINE_RATIO_MAX"
            :step="0.0005"
            :value="lineRatio"
            @input="onLineSlider"
          />
          <span class="param-value">{{ Math.round(lineRatio * canvasWidth) }}px</span>
        </template>
        <template v-if="showFontSlider">
          <input
            type="range"
            class="font-size-slider"
            :min="FONT_RATIO_MIN"
            :max="FONT_RATIO_MAX"
            :step="0.001"
            :value="fontRatio"
            @input="onFontSlider"
          />
          <span class="param-value">{{ Math.round(fontRatio * canvasWidth) }}px</span>
        </template>
      </div>

      <!-- 文字工具行内输入区：确认后点击画布放置 -->
      <div v-if="tool === 'text'" class="text-tool-row">
        <input
          v-model="textDraft"
          class="text-draft"
          placeholder="输入文字，点「确认」后点击画布放置"
          @keydown.enter.prevent="onTextEnter"
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
          @pointerleave="onPointerLeave"
          @dblclick="onDblclick"
          @wheel.prevent="onWheel"
        />
      </div>
    </div>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import {
  FONT_RATIO_DEFAULT,
  FONT_RATIO_MAX,
  FONT_RATIO_MIN,
  LINE_RATIO_DEFAULT,
  LINE_RATIO_MAX,
  LINE_RATIO_MIN,
  TOOL_KEYS,
  cmdBBox,
  fontPx,
  hitTest,
  linePx,
  translateCmd,
  type AnnotationCmd,
  type Point,
  type Tool,
} from './annotationModel'

const props = defineProps<{
  /** 弹窗可见性（父组件控制，关闭经 close 事件回传） */
  visible: boolean
  /** 截图数据：Blob（组件自建 object URL 并回收）或 URL 字符串（调用方管理生命周期） */
  image: Blob | string | null
  /** 设备序列号：下载文件名中性化来源 */
  deviceId: string
}>()

const emit = defineEmits<{ (e: 'close'): void }>()

/** 拖拽中的未提交形状（本轮仅画笔/箭头/矩形走拖拽路径）。 */
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
/** 撤销出栈的命令（重做栈；落新命令即清空，redo 出栈回放）。 */
const redoStack = ref<AnnotationCmd[]>([])
/** 文字工具：输入框草稿与「确认」后的待放置文字。 */
const textDraft = ref('')
const confirmedText = ref('')
const drag = ref<DragState | null>(null)
/** 悬停点（文字工具 ghost 预览位置；pointermove 无拖拽/移动时更新，移出画布清空）。 */
const hoverPoint = ref<Point | null>(null)
/** 当前选中的命令索引（点击命中 → 可拖动/Delete 删除）。 */
const selectedIndex = ref<number | null>(null)
/** 移动中的命令（pointerdown 命中进入；pointermove 实时平移，pointerup 结束）。 */
const moveState = ref<{ index: number; last: Point } | null>(null)
/** 双击文字进入的编辑态（confirmText 原地更新该命令，不新增）。 */
const editingIndex = ref<number | null>(null)

/** 新标注默认字号比例（字号滑块/滚轮调整；文字命令携带入栈）。 */
const fontRatio = ref(FONT_RATIO_DEFAULT)
/** 新标注默认线宽比例（线宽滑块/滚轮调整；笔划命令携带入栈）。 */
const lineRatio = ref(LINE_RATIO_DEFAULT)
/** 底图像素宽度（参数行 px 标签换算用；底图就绪时赋值）。 */
const canvasWidth = ref(0)

/** 预设色板（红/橙/黄/绿/蓝/黑）。 */
const PRESET_COLORS = ['#ff3b30', '#ff9500', '#ffcc00', '#34c759', '#007aff', '#000000'] as const

/** 参数行滑块显隐分组（T4 引入新工具时把对应工具补进相应表）。 */
const FONT_TOOLS: readonly Tool[] = ['text']
const LINE_TOOLS: readonly Tool[] = ['pen', 'arrow', 'rect']
const showFontSlider = computed(() => FONT_TOOLS.includes(tool.value))
const showLineSlider = computed(() => LINE_TOOLS.includes(tool.value))

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

// 每次打开弹窗都是全新标注会话（commands/redo/交互态/文字态/工具/参数一并归零）。
watch(
  () => props.visible,
  (visible) => {
    if (visible) {
      commands.value = []
      redoStack.value = []
      drag.value = null
      moveState.value = null
      selectedIndex.value = null
      editingIndex.value = null
      hoverPoint.value = null
      textDraft.value = ''
      confirmedText.value = ''
      tool.value = 'pen'
      fontRatio.value = FONT_RATIO_DEFAULT
      lineRatio.value = LINE_RATIO_DEFAULT
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

/** 数值钳制到 [min, max]（滑块与滚轮调参共用）。 */
function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value))
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

/** measure 回调（命中检测/包围盒文字宽用）：按换算后字号测量像素宽。 */
function measureText(text: string, px: number): number {
  const ctx = canvasRef.value?.getContext('2d')
  if (!ctx) return text.length * 12
  ctx.font = `${px}px sans-serif`
  return ctx.measureText(text).width
}

/** 箭头头部两翼（头长 = 线宽像素 ×3.5、±30° 张角；默认 4px 线宽 → 14px，与原固定值一致）。 */
function drawArrowHead(ctx: CanvasRenderingContext2D, from: Point, to: Point, headLength: number) {
  const angle = Math.atan2(to.y - from.y, to.x - from.x)
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

/** 单条标注命令渲染（线宽/字号为比例值，按 canvasWidth 换算像素——显示与导出同源）。 */
function drawCmd(ctx: CanvasRenderingContext2D, cmd: AnnotationCmd, canvasWidth: number) {
  switch (cmd.kind) {
    case 'pen': {
      ctx.strokeStyle = cmd.color
      ctx.lineWidth = linePx(cmd.lineWidth, canvasWidth)
      ctx.lineCap = 'round'
      ctx.beginPath()
      ctx.moveTo(cmd.points[0].x, cmd.points[0].y)
      for (const point of cmd.points.slice(1)) ctx.lineTo(point.x, point.y)
      ctx.stroke()
      break
    }
    case 'arrow': {
      const strokePx = linePx(cmd.lineWidth, canvasWidth)
      ctx.strokeStyle = cmd.color
      ctx.lineWidth = strokePx
      ctx.lineCap = 'round'
      ctx.beginPath()
      ctx.moveTo(cmd.from.x, cmd.from.y)
      ctx.lineTo(cmd.to.x, cmd.to.y)
      ctx.stroke()
      drawArrowHead(ctx, cmd.from, cmd.to, strokePx * 3.5)
      break
    }
    case 'rect': {
      ctx.strokeStyle = cmd.color
      ctx.lineWidth = linePx(cmd.lineWidth, canvasWidth)
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
      ctx.font = `${fontPx(cmd.fontSize, canvasWidth)}px sans-serif`
      ctx.fillText(cmd.text, cmd.at.x, cmd.at.y)
      break
    }
  }
}

/** 全量重绘：清屏 → 底图 → 命令栈回放 → 拖拽预览 → ghost 预览 → 选中虚线框。 */
function redraw() {
  const canvas = canvasRef.value
  if (!canvas) return
  const ctx = canvas.getContext('2d')
  if (!ctx) return
  const canvasWidth = canvas.width
  ctx.clearRect(0, 0, canvas.width, canvas.height)
  if (baseImage.value) ctx.drawImage(baseImage.value, 0, 0, canvas.width, canvas.height)
  for (const cmd of commands.value) drawCmd(ctx, cmd, canvasWidth)
  const d = drag.value
  if (d) {
    if (d.tool === 'pen') {
      drawCmd(
        ctx,
        {
          kind: 'pen',
          points: d.points.length ? d.points : [d.current],
          color: strokeColor.value,
          lineWidth: lineRatio.value,
        },
        canvasWidth,
      )
    } else if (d.tool === 'arrow') {
      drawCmd(
        ctx,
        { kind: 'arrow', from: d.start, to: d.current, color: strokeColor.value, lineWidth: lineRatio.value },
        canvasWidth,
      )
    } else {
      drawCmd(
        ctx,
        { kind: 'rect', from: d.start, to: d.current, color: strokeColor.value, lineWidth: lineRatio.value },
        canvasWidth,
      )
    }
  }
  // ghost 预览：文字工具已确认文案且悬停画布 → 半透明预览（点击才落字）
  const hover = hoverPoint.value
  if (tool.value === 'text' && confirmedText.value && hover) {
    ctx.save()
    ctx.globalAlpha = 0.5
    drawCmd(
      ctx,
      { kind: 'text', at: hover, text: confirmedText.value, color: strokeColor.value, fontSize: fontRatio.value },
      canvasWidth,
    )
    ctx.restore()
  }
  // 选中虚线框（cmdBBox 外扩 4px；画完复位虚线）
  const selected = selectedIndex.value
  if (selected !== null && selected < commands.value.length) {
    const box = cmdBBox(commands.value[selected], canvasWidth, measureText)
    ctx.setLineDash([6, 4])
    ctx.strokeStyle = '#409eff'
    ctx.lineWidth = 1
    ctx.strokeRect(box.x - 4, box.y - 4, box.width + 8, box.height + 8)
    ctx.setLineDash([])
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
    canvasWidth.value = canvas.width
    redraw()
  },
  { immediate: true },
)

function onPointerDown(e: PointerEvent) {
  const point = toCanvasPoint(e)
  if (!point) return
  const canvas = canvasRef.value
  // 命中已有命令：选中并进入移动（拖动实时平移，pointerup 结束）
  const hit = canvas ? hitTest(commands.value, point, canvas.width, measureText) : null
  if (hit !== null) {
    selectedIndex.value = hit
    moveState.value = { index: hit, last: point }
    drag.value = null
    redraw()
    return
  }
  selectedIndex.value = null
  const active = tool.value
  if (active === 'text') {
    // 文字工具：未确认文案时不落字
    if (!confirmedText.value) return
    pushCmd({
      kind: 'text',
      at: point,
      text: confirmedText.value,
      color: strokeColor.value,
      fontSize: fontRatio.value,
    })
    redraw()
    return
  }
  // 本轮仅画笔/箭头/矩形走拖拽路径（椭圆等新工具后续任务引入后再扩）
  if (active !== 'pen' && active !== 'arrow' && active !== 'rect') return
  drag.value = { tool: active, start: point, current: point, points: [point] }
  redraw()
}

function onPointerMove(e: PointerEvent) {
  const point = toCanvasPoint(e)
  if (!point) return
  const move = moveState.value
  if (move) {
    const cmd = commands.value[move.index]
    if (cmd) {
      translateCmd(cmd, point.x - move.last.x, point.y - move.last.y)
      move.last = point
    }
    redraw()
    return
  }
  const d = drag.value
  if (d) {
    d.current = point
    if (d.tool === 'pen') d.points.push(point)
    redraw()
    return
  }
  // 无拖拽/移动：更新悬停点（文字工具 ghost 预览）
  hoverPoint.value = point
  redraw()
}

function onPointerUp() {
  moveState.value = null
  const d = drag.value
  drag.value = null
  if (!d) return
  // 位移过小的拖拽视为误触不提交（避免噪点/零宽矩形）
  if (d.tool === 'pen') {
    if (d.points.length >= 2 && dist(d.points[0], d.current) >= 2) {
      pushCmd({ kind: 'pen', points: [...d.points], color: strokeColor.value, lineWidth: lineRatio.value })
    }
  } else if (d.tool === 'arrow') {
    if (dist(d.start, d.current) >= 2) {
      pushCmd({
        kind: 'arrow',
        from: d.start,
        to: d.current,
        color: strokeColor.value,
        lineWidth: lineRatio.value,
      })
    }
  } else if (Math.abs(d.current.x - d.start.x) >= 2 || Math.abs(d.current.y - d.start.y) >= 2) {
    pushCmd({
      kind: 'rect',
      from: d.start,
      to: d.current,
      color: strokeColor.value,
      lineWidth: lineRatio.value,
    })
  }
  redraw()
}

/** 指针移出画布：清 ghost 悬停点并结束拖拽/移动（沿用 pointerup 提交语义）。 */
function onPointerLeave() {
  hoverPoint.value = null
  onPointerUp()
  redraw()
}

/** 双击画布：命中文字命令 → 进入原地编辑（输入框预填，确认后写回该命令）。 */
function onDblclick(e: MouseEvent) {
  const point = toCanvasPoint(e)
  const canvas = canvasRef.value
  if (!point || !canvas) return
  const hit = hitTest(commands.value, point, canvas.width, measureText)
  if (hit === null) return
  const cmd = commands.value[hit]
  if (cmd.kind !== 'text') return
  editingIndex.value = hit
  selectedIndex.value = hit
  tool.value = 'text'
  textDraft.value = cmd.text
  confirmedText.value = cmd.text
  redraw()
}

/** 落新命令：入栈并清空 redo 栈（新分支从当前状态出发，旧 redo 不可达）。 */
function pushCmd(cmd: AnnotationCmd) {
  commands.value.push(cmd)
  redoStack.value = []
}

/** 栈变化后：越界的选中/编辑索引归零（被移除的命令不再可选）。 */
function dropDanglingIndexes() {
  if (selectedIndex.value !== null && selectedIndex.value >= commands.value.length) selectedIndex.value = null
  if (editingIndex.value !== null && editingIndex.value >= commands.value.length) editingIndex.value = null
}

/** 撤销：命令栈出栈入 redo 栈并重绘。 */
function undo() {
  const cmd = commands.value.pop()
  if (!cmd) return
  redoStack.value.push(cmd)
  drag.value = null
  moveState.value = null
  dropDanglingIndexes()
  redraw()
}

/** 重做：redo 栈出栈回放并重绘。 */
function redo() {
  const cmd = redoStack.value.pop()
  if (!cmd) return
  commands.value.push(cmd)
  drag.value = null
  moveState.value = null
  redraw()
}

/** 清空：命令栈与 redo 栈双双归零、交互态复位并重绘（回到纯底图）。 */
function clearAll() {
  commands.value = []
  redoStack.value = []
  drag.value = null
  moveState.value = null
  selectedIndex.value = null
  editingIndex.value = null
  redraw()
}

/** 删除当前选中的命令（splice 出栈；redo 栈清空——分支不再可达）。 */
function deleteSelected() {
  const idx = selectedIndex.value
  if (idx === null || idx >= commands.value.length) return
  commands.value.splice(idx, 1)
  redoStack.value = []
  selectedIndex.value = null
  if (editingIndex.value !== null && editingIndex.value >= commands.value.length) editingIndex.value = null
  redraw()
}

/** 文字工具「确认」：编辑态 → 原地更新该命令文案（无新增）；否则提升草稿为待放置文字。 */
function confirmText() {
  const idx = editingIndex.value
  if (idx !== null && idx < commands.value.length) {
    const cmd = commands.value[idx]
    if (cmd.kind === 'text') cmd.text = textDraft.value
    confirmedText.value = textDraft.value
    editingIndex.value = null
    redraw()
    return
  }
  confirmedText.value = textDraft.value
}

/** 文字输入回车：过滤 IME 组合态（中文候选选词回车不得确认落字）。 */
function onTextEnter(e: KeyboardEvent) {
  if (!e.isComposing) confirmText()
}

/** 线宽滑块输入：取值钳制到上下限后作为新笔划默认线宽。 */
function onLineSlider(e: Event) {
  lineRatio.value = clamp(Number((e.target as HTMLInputElement).value), LINE_RATIO_MIN, LINE_RATIO_MAX)
}

/** 字号滑块输入：取值钳制到上下限后作为新文字默认字号。 */
function onFontSlider(e: Event) {
  fontRatio.value = clamp(Number((e.target as HTMLInputElement).value), FONT_RATIO_MIN, FONT_RATIO_MAX)
}

/** 画布滚轮调参：上滚放大 ×1.1、下滚缩小 ÷1.1（clamp 后重绘，拖拽预览随动）。 */
function onWheel(e: WheelEvent) {
  e.preventDefault()
  const factor = e.deltaY < 0 ? 1.1 : 1 / 1.1
  if (showFontSlider.value) {
    fontRatio.value = clamp(fontRatio.value * factor, FONT_RATIO_MIN, FONT_RATIO_MAX)
  } else if (showLineSlider.value) {
    lineRatio.value = clamp(lineRatio.value * factor, LINE_RATIO_MIN, LINE_RATIO_MAX)
  }
  redraw()
}

/**
 * 弹窗快捷键（window 级监听）：
 *   - Ctrl/Cmd+Z 撤销；Ctrl/Cmd+Shift+Z 重做；Ctrl/Cmd+S 导出 PNG
 *   - Escape：编辑中 → 退出编辑；否则取消拖拽/移动并清选中
 *   - Delete/Backspace：删除选中命令（无选中不拦截默认）
 *   - 数字键 1-N（按 TOOL_KEYS 表序）切工具
 * 输入框/文本域聚焦时仅放行 Escape（编辑态退出用），其余按键不劫持打字。
 */
function onKeydown(e: KeyboardEvent) {
  if (!props.visible) return
  const target = e.target as HTMLElement | null
  if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA') && e.key !== 'Escape') {
    return
  }
  if (e.key === 'Escape') {
    if (editingIndex.value !== null) {
      editingIndex.value = null
      return
    }
    if (drag.value || moveState.value || selectedIndex.value !== null) {
      drag.value = null
      moveState.value = null
      selectedIndex.value = null
      redraw()
    }
    return
  }
  const ctrl = e.ctrlKey || e.metaKey
  if (ctrl && e.key.toLowerCase() === 'z') {
    e.preventDefault()
    if (e.shiftKey) redo()
    else undo()
    return
  }
  if (ctrl && e.key.toLowerCase() === 's') {
    e.preventDefault()
    exportPng()
    return
  }
  if (!ctrl && (e.key === 'Delete' || e.key === 'Backspace')) {
    if (selectedIndex.value !== null) {
      e.preventDefault()
      deleteSelected()
    }
    return
  }
  // Ctrl/Cmd+C → 复制到剪贴板：T5 接线（本轮占位，不拦截浏览器默认行为）。
  if (!ctrl && e.key >= '1' && e.key <= String(TOOL_KEYS.length)) {
    tool.value = TOOL_KEYS[Number(e.key) - 1]
  }
}

// 弹窗可见期间挂 window 快捷键监听，关闭时移除（onUnmounted 兜底）。
watch(
  () => props.visible,
  (visible) => {
    if (visible) window.addEventListener('keydown', onKeydown)
    else window.removeEventListener('keydown', onKeydown)
  },
  { immediate: true },
)

onUnmounted(() => {
  releaseObjectUrl()
  window.removeEventListener('keydown', onKeydown)
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
 *
 * getCommands / getRedoCount：命令栈快照与 redo 栈长度；
 * getDefaultFontRatio / getDefaultLineRatio：滑块/滚轮调参后的默认比例；
 * getSelectedIndex：当前选中索引（命中选中/移动/删除断言用）。
 *
 * （均为测试断言暴露，沿用 getAnnotationCount 先例。）
 */
defineExpose({
  getAnnotationCount: () => commands.value.length,
  getCommands: () => commands.value,
  getRedoCount: () => redoStack.value.length,
  getDefaultFontRatio: () => fontRatio.value,
  getDefaultLineRatio: () => lineRatio.value,
  getSelectedIndex: () => selectedIndex.value,
})
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

.param-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.color-swatch {
  width: 22px;
  height: 22px;
  padding: 0;
  border: 2px solid transparent;
  border-radius: 4px;
  cursor: pointer;
}

.swatch-active {
  border-color: #409eff;
  box-shadow: inset 0 0 0 1px #fff;
}

.line-width-slider,
.font-size-slider {
  width: 120px;
  margin: 0;
}

.param-value {
  min-width: 44px;
  font-size: 12px;
  color: #909399;
  white-space: nowrap;
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
  /* 竖屏长图按比例封顶（防 flex stretch 把元素盒压扁位图拉伸，方案 37 B7 修复项） */
  max-height: 60vh;
  width: auto;
  height: auto;
  cursor: crosshair;
  touch-action: none;
}
</style>