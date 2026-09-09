<!--
  可停靠面板
  ===========

  通用的可停靠、可调整大小的面板组件。

  功能：
    - 支持底部（bottom）或右侧（right）停靠
    - 拖拽调整大小（高度或宽度）
    - 最小/最大尺寸限制
    - 通过 v-model:height 和 v-model:width 双向绑定尺寸

  Props:
    - position: 'bottom' | 'right'（停靠位置）
    - height: 初始高度（bottom 模式）
    - width: 初始宽度（right 模式）
    - minHeight: 最小高度（默认 200）
    - maxHeight: 最大高度（默认 800）
    - minWidth: 最小宽度（默认 300）
    - maxWidth: 最大宽度（默认 800）

  Slots:
    - header: 面板头部（拖拽手柄区域）
    - default: 面板内容

  使用方式：
    <DockPanel position="right" v-model:width="panelWidth">
      <template #header>Header</template>
      <div>Content</div>
    </DockPanel>
-->
<template>
  <div class="dock-panel" :class="[`position-${position}`]" :style="panelStyle">
    <!-- 拖拽调整大小的手柄 -->
    <div
      class="dock-resize-handle"
      :class="[`resize-${position}`]"
      @mousedown.prevent="startResize"
    ></div>

    <!-- 面板头部 -->
    <div v-if="$slots.header" class="dock-header" @mousedown.prevent="startResize">
      <slot name="header"></slot>
    </div>

    <!-- 面板内容 -->
    <div class="dock-content">
      <slot></slot>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'

const props = withDefaults(defineProps<{
  /** 停靠位置：底部或右侧 */
  position?: 'bottom' | 'right'
  /** 面板高度（bottom 模式） */
  height?: number
  /** 面板宽度（right 模式） */
  width?: number
  /** 最小高度 */
  minHeight?: number
  /** 最大高度 */
  maxHeight?: number
  /** 最小宽度 */
  minWidth?: number
  /** 最大宽度 */
  maxWidth?: number
}>(), {
  position: 'right',
  height: 400,
  width: 500,
  minHeight: 200,
  maxHeight: 800,
  minWidth: 300,
  maxWidth: 800,
})

const emit = defineEmits<{
  (e: 'update:height', value: number): void
  (e: 'update:width', value: number): void
}>()

/** 当前面板高度。 */
const currentHeight = ref(props.height)
/** 当前面板宽度。 */
const currentWidth = ref(props.width)

/**
 * 计算面板样式。
 * bottom 模式：设置高度，宽度 100%
 * right 模式：设置宽度，高度 100%
 */
const panelStyle = computed(() => {
  if (props.position === 'bottom') {
    return { height: `${currentHeight.value}px` }
  } else {
    return { width: `${currentWidth.value}px` }
  }
})

/**
 * 开始拖拽调整大小。
 * 注册 mousemove 和 mouseup 事件处理器。
 */
function startResize(e: MouseEvent) {
  const startY = e.clientY
  const startX = e.clientX
  const startHeight = currentHeight.value
  const startWidth = currentWidth.value

  function onMouseMove(e: MouseEvent) {
    if (props.position === 'bottom') {
      // 底部模式：向上拖增大，向下拖减小
      const delta = startY - e.clientY
      currentHeight.value = Math.max(props.minHeight, Math.min(props.maxHeight, startHeight + delta))
    } else {
      // 右侧模式：向左拖增大，向右拖减小
      const delta = startX - e.clientX
      currentWidth.value = Math.max(props.minWidth, Math.min(props.maxWidth, startWidth + delta))
    }
  }

  function onMouseUp() {
    document.removeEventListener('mousemove', onMouseMove)
    document.removeEventListener('mouseup', onMouseUp)
    document.body.style.cursor = ''
    document.body.style.userSelect = ''

    emit('update:height', currentHeight.value)
    emit('update:width', currentWidth.value)
  }

  // 设置光标和禁止文本选择
  document.body.style.cursor = props.position === 'bottom' ? 'ns-resize' : 'ew-resize'
  document.body.style.userSelect = 'none'

  document.addEventListener('mousemove', onMouseMove)
  document.addEventListener('mouseup', onMouseUp)
}
</script>

<style scoped>
.dock-panel {
  position: relative;
  background: #fff;
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
}

/* 底部停靠 */
.dock-panel.position-bottom {
  border-top: 2px solid #1976d2;
}

/* 右侧停靠 */
.dock-panel.position-right {
  border-left: 2px solid #1976d2;
}

/* 面板头部（也是拖拽手柄） */
.dock-header {
  flex-shrink: 0;
  cursor: grab;
  user-select: none;
}

.dock-header:active {
  cursor: grabbing;
}

/* 面板内容区域 */
.dock-content {
  flex: 1;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

/* 拖拽调整大小手柄 */
.dock-resize-handle {
  position: absolute;
  z-index: 10;
}

/* 底部模式：手柄在顶部 */
.dock-resize-handle.resize-bottom {
  top: -4px;
  left: 0;
  right: 0;
  height: 8px;
  cursor: ns-resize;
}

/* 右侧模式：手柄在左侧 */
.dock-resize-handle.resize-right {
  top: 0;
  bottom: 0;
  left: -4px;
  width: 8px;
  cursor: ew-resize;
}
</style>
