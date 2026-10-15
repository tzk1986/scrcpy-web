<!--
  截图标注面板（方案 37 B7）
  ==========================

  截图预览弹窗：显示截图（视频流抓帧或设备端截图），支持画笔/箭头/矩形/椭圆/
  荧光笔/文字/序号/马赛克 8 种叠加标注（渲染由 annotationModel.drawCmd 承载），
  可导出标注后的 PNG 或直接保存原图。

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
    - 序号工具：点击画布立即落编号气泡（编号 = 现有最大序号 +1，删除中间不重号）
    - 马赛克：拖拽出区域（两轴各 ≥2px 才提交），拖拽中显示虚线矩形轮廓，
      提交后以块状马赛克覆盖（底图未就绪时静默不画）；无颜色参数（参数行隐藏色板）
    - 命中编辑：点击已放置标注 → 选中（虚线框）并进入移动（拖动实时平移）；
      选中后显示 8 向缩放手柄（方案 47 T3：角先边后命中、Shift 等比、
      每帧从按下快照重建实时预览、松手有变化才落一步 transform）；
      按下命中优先序：手柄 > 命令体 > 空白；悬停手柄切换对应缩放光标；
      Delete/Backspace 删除选中；双击文字 → 原地二次编辑（确认不发新命令）；
      Escape 退出编辑/取消选中并结束拖拽/移动/缩放；命中检测走 toCanvasPoint 比例换算坐标
    - 字号/线宽按「相对图宽比例」存储（annotationModel），渲染时 ×canvas.width 换算
    - 参数行：6 预设色板 + 自定义色 + 线宽/字号滑块（原生 range，按当前工具显隐），
      右侧标签显示换算后像素值；画布滚轮也可调参（×1.1/÷1.1）
    - 快捷键（window 监听，弹窗可见期间生效）：数字键 1-N 切工具（按 TOOL_KEYS 表）、
      Ctrl/Cmd+Z 撤销、Ctrl/Cmd+Shift+Z 重做、Ctrl/Cmd+S 导出、Ctrl/Cmd+C 复制到剪贴板；
      输入框聚焦时仅放行 Escape
    - 撤销/重做双栈：undo 出栈入 redo，redo 回放，落新命令清 redo 栈；
      清空：双栈归零重绘（重绘 = 底图 + 回放命令栈）
    - 复制到剪贴板：canvas.toBlob → ClipboardItem('image/png') → clipboard.write
      （API 缺失特性检测静默返回；权限/焦点等写入失败静默不抛）
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
        <!-- 工具组（顺序与 TOOL_KEYS 表一致：数字键 1-8 对应） -->
        <el-button class="tool tool-pen" :class="{ 'tool-active': tool === 'pen' }" @click="tool = 'pen'">
          画笔
        </el-button>
        <el-button class="tool tool-arrow" :class="{ 'tool-active': tool === 'arrow' }" @click="tool = 'arrow'">
          箭头
        </el-button>
        <el-button class="tool tool-rect" :class="{ 'tool-active': tool === 'rect' }" @click="tool = 'rect'">
          矩形
        </el-button>
        <el-button class="tool tool-ellipse" :class="{ 'tool-active': tool === 'ellipse' }" @click="tool = 'ellipse'">
          椭圆
        </el-button>
        <el-button
          class="tool tool-highlight"
          :class="{ 'tool-active': tool === 'highlight' }"
          @click="tool = 'highlight'"
        >
          荧光笔
        </el-button>
        <el-button class="tool tool-text" :class="{ 'tool-active': tool === 'text' }" @click="tool = 'text'">
          文字
        </el-button>
        <el-button class="tool tool-number" :class="{ 'tool-active': tool === 'number' }" @click="tool = 'number'">
          序号
        </el-button>
        <el-button class="tool tool-mosaic" :class="{ 'tool-active': tool === 'mosaic' }" @click="tool = 'mosaic'">
          马赛克
        </el-button>
        <span class="toolbar-sep" aria-hidden="true" />
        <!-- 编辑组（撤销/重做/清空） -->
        <el-button class="btn-undo" :disabled="history.length === 0" @click="undo">撤销</el-button>
        <el-button class="btn-redo" :disabled="redoHistory.length === 0" @click="redo">重做</el-button>
        <el-button class="btn-clear" :disabled="commands.length === 0" @click="clearAll">清空</el-button>
        <span class="toolbar-spacer" />
        <!-- 输出组（复制/保存/导出） -->
        <el-button class="btn-copy" @click="copyToClipboard">复制</el-button>
        <el-button class="btn-save" @click="saveOriginal">直接保存</el-button>
        <el-button class="btn-export" type="primary" @click="exportPng">导出 PNG</el-button>
      </div>

      <!-- 参数行：预设色板 + 自定义色 + 线宽/字号滑块（按当前工具显隐；马赛克无颜色参数） -->
      <div class="param-row">
        <template v-if="showColorPicker">
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
        </template>
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
import { ElMessage } from 'element-plus'
import {
  FONT_RATIO_DEFAULT,
  FONT_RATIO_MAX,
  FONT_RATIO_MIN,
  LINE_RATIO_DEFAULT,
  LINE_RATIO_MAX,
  LINE_RATIO_MIN,
  TOOL_KEYS,
  cmdBBox,
  drawCmd,
  hitHandle,
  hitTest,
  nextNumberIndex,
  resizeHandles,
  transformCmd,
  translateCmd,
  type AnnotationCmd,
  type BBox,
  type Point,
  type ResizeHandleId,
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

/** 拖拽中的未提交形状（画笔/箭头/矩形/椭圆/荧光笔/马赛克走拖拽路径）。 */
interface DragState {
  tool: 'pen' | 'arrow' | 'rect' | 'ellipse' | 'highlight' | 'mosaic'
  start: Point
  current: Point
  /** 画笔/荧光笔轨迹点（含起点）。 */
  points: Point[]
}

/** 操作历史步骤（方案 47 A2）：cmd/before/after/snapshot 均为深拷贝快照。 */
type HistoryStep =
  | { kind: 'add'; cmd: AnnotationCmd }
  | { kind: 'delete'; cmd: AnnotationCmd; index: number }
  | { kind: 'transform'; index: number; before: AnnotationCmd; after: AnnotationCmd }
  | { kind: 'edit-text'; index: number; before: AnnotationCmd; after: AnnotationCmd }
  | { kind: 'clear'; snapshot: AnnotationCmd[] }

const canvasRef = ref<HTMLCanvasElement | null>(null)

const tool = ref<Tool>('pen')
/** 标注颜色（画笔/箭头/矩形/椭圆/荧光笔/文字/序号共用；马赛克无颜色参数）。 */
const strokeColor = ref('#ff3b30')
/** 已提交命令栈（渲染源；history 步骤以深拷贝快照与之隔离，见方案 47 T2）。 */
const commands = ref<AnnotationCmd[]>([])
/**
 * 操作历史（方案 47 R1）：步骤粒度 = 一次用户操作而非一条命令——
 * add 新绘 / delete 删除 / transform 移位与缩放落定 / edit-text 双击改文案 / clear 清空。
 * 步骤内一律存深拷贝快照，undo 反向回放、redo 正向回放。
 */
const history = ref<HistoryStep[]>([])
/** 已撤销步骤的重放分支（落新步即清空，同旧 redoStack 语义）。 */
const redoHistory = ref<HistoryStep[]>([])
/** 文字工具：输入框草稿与「确认」后的待放置文字。 */
const textDraft = ref('')
const confirmedText = ref('')
const drag = ref<DragState | null>(null)
/** 悬停点（文字工具 ghost 预览位置；pointermove 无拖拽/移动时更新，移出画布清空）。 */
const hoverPoint = ref<Point | null>(null)
/** 当前选中的命令索引（点击命中 → 可拖动/Delete 删除）。 */
const selectedIndex = ref<number | null>(null)
/**
 * 移动中的命令（pointerdown 命中进入；pointermove 实时平移，pointerup 结束）。
 * before=pointerdown 快照、moved=是否发生过位移——仅位移落定才产 transform 步。
 */
const moveState = ref<{ index: number; last: Point; before: AnnotationCmd; moved: boolean } | null>(null)
/**
 * 缩放中的命令（方案 47 T3）：pointerdown 命中选中命令手柄进入。
 * origin=按下时手柄位置、anchor=对角/对边锚点（缩放期间不动）、
 * before=按下快照（每帧从快照重建再 transformCmd，避免累计误差与翻转抖动）。
 */
const resizeState = ref<{
  index: number
  handle: ResizeHandleId
  origin: Point
  anchor: Point
  before: AnnotationCmd
} | null>(null)
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

/** 参数行滑块显隐分组（全 stroke 拖动类工具共用线宽；文字/序号共用字号）。 */
const FONT_TOOLS: readonly Tool[] = ['text', 'number']
const LINE_TOOLS: readonly Tool[] = ['pen', 'arrow', 'rect', 'ellipse', 'highlight', 'mosaic']
const showFontSlider = computed(() => FONT_TOOLS.includes(tool.value))
const showLineSlider = computed(() => LINE_TOOLS.includes(tool.value))
/** 马赛克无颜色参数：色板与自定义色隐藏。 */
const showColorPicker = computed(() => tool.value !== 'mosaic')

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
      history.value = []
      redoHistory.value = []
      drag.value = null
      moveState.value = null
      resizeState.value = null
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

/** 单条标注命令渲染由 annotationModel.drawCmd 承载（T4 迁出，含 mosaic 底图参数）。 */

/** 全量重绘：清屏 → 底图 → 命令栈回放 → 拖拽预览 → ghost 预览 → 选中虚线框。 */
function redraw() {
  const canvas = canvasRef.value
  if (!canvas) return
  const ctx = canvas.getContext('2d')
  if (!ctx) return
  const canvasWidth = canvas.width
  ctx.clearRect(0, 0, canvas.width, canvas.height)
  if (baseImage.value) ctx.drawImage(baseImage.value, 0, 0, canvas.width, canvas.height)
  for (const cmd of commands.value) drawCmd(ctx, cmd, canvasWidth, baseImage.value)
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
        baseImage.value,
      )
    } else if (d.tool === 'highlight') {
      drawCmd(
        ctx,
        {
          kind: 'highlight',
          points: d.points.length ? d.points : [d.current],
          color: strokeColor.value,
          lineWidth: lineRatio.value,
        },
        canvasWidth,
        baseImage.value,
      )
    } else if (d.tool === 'arrow') {
      drawCmd(
        ctx,
        { kind: 'arrow', from: d.start, to: d.current, color: strokeColor.value, lineWidth: lineRatio.value },
        canvasWidth,
        baseImage.value,
      )
    } else if (d.tool === 'ellipse') {
      drawCmd(
        ctx,
        { kind: 'ellipse', from: d.start, to: d.current, color: strokeColor.value, lineWidth: lineRatio.value },
        canvasWidth,
        baseImage.value,
      )
    } else if (d.tool === 'mosaic') {
      // 马赛克拖拽预览：虚线矩形轮廓（松手提交后重绘才显示真实马赛克）
      ctx.save()
      ctx.setLineDash([4, 4])
      ctx.strokeStyle = '#606266'
      ctx.lineWidth = 1
      ctx.strokeRect(
        Math.min(d.start.x, d.current.x),
        Math.min(d.start.y, d.current.y),
        Math.abs(d.current.x - d.start.x),
        Math.abs(d.current.y - d.start.y),
      )
      ctx.restore()
    } else {
      drawCmd(
        ctx,
        { kind: 'rect', from: d.start, to: d.current, color: strokeColor.value, lineWidth: lineRatio.value },
        canvasWidth,
        baseImage.value,
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
      baseImage.value,
    )
    ctx.restore()
  }
  // 选中虚线框（cmdBBox 外扩 4px；画完复位虚线）+ 8 向缩放手柄方块（方案 47 T3）
  const selected = selectedIndex.value
  if (selected !== null && selected < commands.value.length) {
    const box = cmdBBox(commands.value[selected], canvasWidth, measureText)
    ctx.setLineDash([6, 4])
    ctx.strokeStyle = '#409eff'
    ctx.lineWidth = 1
    ctx.strokeRect(box.x - 4, box.y - 4, box.width + 8, box.height + 8)
    ctx.setLineDash([])
    const half = Math.max(3, canvasWidth * 0.004)
    ctx.fillStyle = '#409eff'
    for (const hd of resizeHandles(box)) {
      ctx.fillRect(hd.x - half, hd.y - half, half * 2, half * 2)
    }
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
  // 命中选中命令的缩放手柄：进入缩放（按下优先序 手柄 > 命令体 > 空白，方案 47 T3）
  const grabbed = hitSelectedHandle(point)
  if (grabbed) {
    moveState.value = null
    drag.value = null
    resizeState.value = {
      index: grabbed.index,
      handle: grabbed.handle,
      origin: grabbed.origin,
      anchor: grabbed.anchor,
      before: snapCmd(commands.value[grabbed.index]),
    }
    redraw()
    return
  }
  // 命中已有命令：选中并进入移动（拖动实时平移，pointerup 结束）
  const hit = canvas ? hitTest(commands.value, point, canvas.width, measureText) : null
  if (hit !== null) {
    selectedIndex.value = hit
    moveState.value = { index: hit, last: point, before: snapCmd(commands.value[hit]), moved: false }
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
  if (active === 'number') {
    // 序号工具：点击立即落字（编号 = 现有最大序号 +1，删除中间不重号）
    pushCmd({
      kind: 'number',
      at: point,
      index: nextNumberIndex(commands.value),
      color: strokeColor.value,
      fontSize: fontRatio.value,
    })
    redraw()
    return
  }
  // 全 stroke 拖动类工具走拖拽路径（pen/arrow/rect/ellipse/highlight/mosaic）
  if (
    active !== 'pen' &&
    active !== 'arrow' &&
    active !== 'rect' &&
    active !== 'ellipse' &&
    active !== 'highlight' &&
    active !== 'mosaic'
  ) {
    return
  }
  drag.value = { tool: active, start: point, current: point, points: [point] }
  redraw()
}

function onPointerMove(e: PointerEvent) {
  const point = toCanvasPoint(e)
  if (!point) return
  const rs = resizeState.value
  if (rs) {
    // 逐轴倍率 = (指针-锚点)/(按下手柄-锚点)；边手柄所在轴分量为 0 → 该轴恒 1
    let sx = rs.origin.x !== rs.anchor.x ? (point.x - rs.anchor.x) / (rs.origin.x - rs.anchor.x) : 1
    let sy = rs.origin.y !== rs.anchor.y ? (point.y - rs.anchor.y) / (rs.origin.y - rs.anchor.y) : 1
    if (e.shiftKey) {
      // 等比：角手柄取绝对值较大轴（带符号，允许翻越锚点），边手柄取其可控轴
      let f: number
      if (rs.handle === 'e' || rs.handle === 'w') f = sx
      else if (rs.handle === 'n' || rs.handle === 's') f = sy
      else f = Math.abs(sx) >= Math.abs(sy) ? sx : sy
      sx = f
      sy = f
    }
    // 每帧从按下快照重建：同一帧内多次移动不累计，倍率始终相对初始包围盒
    const fresh = snapCmd(rs.before)
    transformCmd(fresh, sx, sy, rs.anchor)
    commands.value[rs.index] = fresh
    redraw()
    return
  }
  const move = moveState.value
  if (move) {
    const cmd = commands.value[move.index]
    if (cmd) {
      const dx = point.x - move.last.x
      const dy = point.y - move.last.y
      if (dx !== 0 || dy !== 0) {
        translateCmd(cmd, dx, dy)
        move.moved = true
      }
      move.last = point
    }
    redraw()
    return
  }
  const d = drag.value
  if (d) {
    d.current = point
    if (d.tool === 'pen' || d.tool === 'highlight') d.points.push(point)
    redraw()
    return
  }
  // 无拖拽/移动/缩放：更新悬停点（文字工具 ghost 预览）；悬停选中手柄时切换缩放光标（T3）
  hoverPoint.value = point
  const hovered = hitSelectedHandle(point)
  const canvas = canvasRef.value
  if (canvas) canvas.style.cursor = hovered ? HANDLE_CURSORS[hovered.handle] : ''
  redraw()
}

function onPointerUp() {
  const rs = resizeState.value
  resizeState.value = null
  if (rs) {
    // 缩放落定：与按下快照全等则视为无操作（原地按手柄/回位不产步）
    const cmd = commands.value[rs.index]
    if (cmd && !sameCmd(rs.before, cmd)) {
      commitStep({ kind: 'transform', index: rs.index, before: rs.before, after: snapCmd(cmd) })
    }
    redraw()
  }
  const move = moveState.value
  moveState.value = null
  if (move?.moved) {
    const cmd = commands.value[move.index]
    if (cmd && !sameCmd(move.before, cmd)) {
      commitStep({ kind: 'transform', index: move.index, before: move.before, after: snapCmd(cmd) })
    }
  }
  const d = drag.value
  drag.value = null
  if (!d) return
  // 位移过小的拖拽视为误触不提交（避免噪点/零宽图形；马赛克要求两轴各 ≥2px）
  if (d.tool === 'pen') {
    if (d.points.length >= 2 && dist(d.points[0], d.current) >= 2) {
      pushCmd({ kind: 'pen', points: [...d.points], color: strokeColor.value, lineWidth: lineRatio.value })
    }
  } else if (d.tool === 'highlight') {
    if (d.points.length >= 2 && dist(d.points[0], d.current) >= 2) {
      pushCmd({ kind: 'highlight', points: [...d.points], color: strokeColor.value, lineWidth: lineRatio.value })
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
  } else if (d.tool === 'mosaic') {
    if (Math.abs(d.current.x - d.start.x) >= 2 && Math.abs(d.current.y - d.start.y) >= 2) {
      pushCmd({ kind: 'mosaic', from: d.start, to: d.current, lineWidth: lineRatio.value })
    }
  } else if (d.tool === 'ellipse') {
    if (Math.abs(d.current.x - d.start.x) >= 2 || Math.abs(d.current.y - d.start.y) >= 2) {
      pushCmd({
        kind: 'ellipse',
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

/**
 * 命令深拷贝快照。手写而不用 structuredClone：ref 深层 reactive 下取出的命令是
 * Proxy，structuredClone 对 Proxy 抛 DataCloneError（happy-dom/vitest 实证）。
 */
function snapCmd(cmd: AnnotationCmd): AnnotationCmd {
  switch (cmd.kind) {
    case 'pen':
    case 'highlight':
      return { ...cmd, points: cmd.points.map((p) => ({ x: p.x, y: p.y })) }
    case 'arrow':
    case 'rect':
    case 'ellipse':
    case 'mosaic':
      return { ...cmd, from: { x: cmd.from.x, y: cmd.from.y }, to: { x: cmd.to.x, y: cmd.to.y } }
    case 'text':
    case 'number':
      return { ...cmd, at: { x: cmd.at.x, y: cmd.at.y } }
  }
}

/** 手柄悬停光标映射（对角向共用 nwse/nesw，横竖边 ns/ew）。 */
const HANDLE_CURSORS: Record<ResizeHandleId, string> = {
  nw: 'nwse-resize',
  se: 'nwse-resize',
  ne: 'nesw-resize',
  sw: 'nesw-resize',
  n: 'ns-resize',
  s: 'ns-resize',
  e: 'ew-resize',
  w: 'ew-resize',
}

/** 缩放锚点：角手柄取对角，边手柄取对边中点（缩放期间不动）。 */
function resizeAnchor(bbox: BBox, handle: ResizeHandleId): Point {
  const cx = bbox.x + bbox.width / 2
  const cy = bbox.y + bbox.height / 2
  switch (handle) {
    case 'nw':
      return { x: bbox.x + bbox.width, y: bbox.y + bbox.height }
    case 'ne':
      return { x: bbox.x, y: bbox.y + bbox.height }
    case 'sw':
      return { x: bbox.x + bbox.width, y: bbox.y }
    case 'se':
      return { x: bbox.x, y: bbox.y }
    case 'n':
      return { x: cx, y: bbox.y + bbox.height }
    case 's':
      return { x: cx, y: bbox.y }
    case 'e':
      return { x: bbox.x, y: cy }
    case 'w':
      return { x: bbox.x + bbox.width, y: cy }
  }
}

/** 选中命令的手柄命中（缩放按下与悬停光标共用）：返回索引/手柄/按下原点/锚点；无选中或未命中 null。 */
function hitSelectedHandle(point: Point): { index: number; handle: ResizeHandleId; origin: Point; anchor: Point } | null {
  const idx = selectedIndex.value
  const canvas = canvasRef.value
  if (idx === null || !canvas || idx >= commands.value.length) return null
  const box = cmdBBox(commands.value[idx], canvas.width, measureText)
  const handles = resizeHandles(box)
  const handle = hitHandle(point, handles, canvas.width)
  if (!handle) return null
  const origin = handles.find((h) => h.id === handle)
  if (!origin) return null
  return { index: idx, handle, origin: { x: origin.x, y: origin.y }, anchor: resizeAnchor(box, handle) }
}

/** 落定判定：命令与快照 JSON 全等即无变化（回位拖动/原地按手柄不产 transform 步）。 */
function sameCmd(a: AnnotationCmd, b: AnnotationCmd): boolean {
  return JSON.stringify(a) === JSON.stringify(b)
}

/** 落一个操作步骤：入 history 并清 redoHistory（新分支从当前状态出发，旧重放不可达）。 */
function commitStep(step: HistoryStep) {
  history.value.push(step)
  redoHistory.value = []
}

/** 落新命令：入渲染栈并记一步 add（快照存深拷贝，与后续原地 mutate 隔离）。 */
function pushCmd(cmd: AnnotationCmd) {
  commands.value.push(cmd)
  commitStep({ kind: 'add', cmd: snapCmd(cmd) })
}

/** 栈变化后：越界的选中/编辑索引归零（被移除的命令不再可选）。 */
function dropDanglingIndexes() {
  if (selectedIndex.value !== null && selectedIndex.value >= commands.value.length) selectedIndex.value = null
  if (editingIndex.value !== null && editingIndex.value >= commands.value.length) editingIndex.value = null
}

/** 撤销：末步反向回放（add→移除；delete→原位插回；transform/edit-text→还原 before；clear→整体回放快照）。 */
function undo() {
  const step = history.value.pop()
  if (!step) return
  redoHistory.value.push(step)
  switch (step.kind) {
    case 'add':
      commands.value.pop()
      break
    case 'delete':
      commands.value.splice(step.index, 0, snapCmd(step.cmd))
      break
    case 'transform':
    case 'edit-text':
      commands.value[step.index] = snapCmd(step.before)
      break
    case 'clear':
      commands.value = step.snapshot.map((c) => snapCmd(c))
      break
  }
  drag.value = null
  moveState.value = null
  resizeState.value = null
  dropDanglingIndexes()
  redraw()
}

/** 重做：步正向回放（与 undo 对称）。 */
function redo() {
  const step = redoHistory.value.pop()
  if (!step) return
  history.value.push(step)
  switch (step.kind) {
    case 'add':
      commands.value.push(snapCmd(step.cmd))
      break
    case 'delete':
      commands.value.splice(step.index, 1)
      break
    case 'transform':
    case 'edit-text':
      commands.value[step.index] = snapCmd(step.after)
      break
    case 'clear':
      commands.value = []
      break
  }
  drag.value = null
  moveState.value = null
  resizeState.value = null
  dropDanglingIndexes()
  redraw()
}

/** 清空：记一步 clear（快照全量，可一步撤销）后归零、交互态复位并重绘。 */
function clearAll() {
  if (commands.value.length > 0) {
    commitStep({ kind: 'clear', snapshot: commands.value.map((c) => snapCmd(c)) })
  }
  commands.value = []
  drag.value = null
  moveState.value = null
  resizeState.value = null
  selectedIndex.value = null
  editingIndex.value = null
  redraw()
}

/** 删除当前选中的命令：记一步 delete（undo 原位插回）。 */
function deleteSelected() {
  const idx = selectedIndex.value
  if (idx === null || idx >= commands.value.length) return
  const removed = commands.value.splice(idx, 1)[0]
  commitStep({ kind: 'delete', cmd: snapCmd(removed), index: idx })
  selectedIndex.value = null
  if (editingIndex.value !== null && editingIndex.value >= commands.value.length) editingIndex.value = null
  redraw()
}

/**
 * 文字工具「确认」：编辑态 → 原地更新该命令文案并记一步 edit-text（文案未变不落步）；
 * 否则提升草稿为待放置文字。
 */
function confirmText() {
  const idx = editingIndex.value
  if (idx !== null && idx < commands.value.length) {
    const cmd = commands.value[idx]
    if (cmd.kind === 'text' && cmd.text !== textDraft.value) {
      const before = snapCmd(cmd)
      cmd.text = textDraft.value
      commitStep({ kind: 'edit-text', index: idx, before, after: snapCmd(cmd) })
    }
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
 *   - Ctrl/Cmd+Z 撤销；Ctrl/Cmd+Shift+Z 重做；Ctrl/Cmd+S 导出 PNG；
 *     Ctrl/Cmd+C 复制到剪贴板
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
    if (drag.value || moveState.value || resizeState.value || selectedIndex.value !== null) {
      drag.value = null
      moveState.value = null
      resizeState.value = null
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
  if (ctrl && e.key.toLowerCase() === 'c') {
    e.preventDefault()
    copyToClipboard()
    return
  }
  if (!ctrl && (e.key === 'Delete' || e.key === 'Backspace')) {
    if (selectedIndex.value !== null) {
      e.preventDefault()
      deleteSelected()
    }
    return
  }
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
 * 复制到剪贴板：画布 → PNG Blob → ClipboardItem('image/png') → clipboard.write（Chrome-only）。
 * 点击反馈三态 toast：成功 / 写入失败（权限/焦点）/ API 缺失（如非安全上下文，不触 toBlob）。
 */
function copyToClipboard() {
  const canvas = canvasRef.value
  if (!canvas) return
  if (!navigator.clipboard?.write) {
    ElMessage.warning('当前浏览器环境不支持复制图片到剪贴板')
    return
  }
  canvas.toBlob(async (blob) => {
    if (!blob) return
    try {
      await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })])
      ElMessage.success('已复制到剪贴板')
    } catch {
      ElMessage.error('复制失败，请检查页面焦点或浏览器权限')
    }
  }, 'image/png')
}

/**
 * 当前已提交标注数（供父组件判断是否存在未导出的标注；
 * 测试亦用它断言撤销/清空的命令栈重置语义）。
 *
 * getCommands / getRedoCount / getHistoryCount：命令栈快照与操作历史/重放步数；
 * getDefaultFontRatio / getDefaultLineRatio：滑块/滚轮调参后的默认比例；
 * getSelectedIndex：当前选中索引（命中选中/移动/删除断言用）。
 *
 * （均为测试断言暴露，沿用 getAnnotationCount 先例。）
 */
defineExpose({
  getAnnotationCount: () => commands.value.length,
  getCommands: () => commands.value,
  getRedoCount: () => redoHistory.value.length,
  getHistoryCount: () => history.value.length,
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

/* 选中态：填充高亮（走 el-button 的 CSS 变量以压过组件默认样式，含 hover） */
.tool-active {
  --el-button-bg-color: #409eff;
  --el-button-border-color: #409eff;
  --el-button-text-color: #fff;
  --el-button-hover-bg-color: #66b1ff;
  --el-button-hover-border-color: #66b1ff;
  --el-button-hover-text-color: #fff;
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
