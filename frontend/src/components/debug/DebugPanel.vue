<!--
  调试面板
  =========

  可停靠调试面板，包含多个标签页：
    - Logcat: 实时日志查看器（LogcatView.vue）
    - Shell: 远程 shell 终端（ShellView.vue）
    - Network: 网络抓包（待实现）
    - Performance: 性能监控（待实现）
    - Apps: 应用管理（待实现）

  功能：
    - 底部/右侧停靠切换
    - 拖拽调整大小
    - 标签页切换
    - 快捷键支持

  Props:
    deviceId: 目标设备的 ADB 序列号

  子组件关系：
    DebugPanel.vue → DockPanel.vue
                   → LogcatView.vue
                   → ShellView.vue
-->
<template>
  <DockPanel
    :position="position"
    :width="panelWidth"
    :height="panelHeight"
    @update:width="panelWidth = $event"
    @update:height="panelHeight = $event"
  >
    <template #header>
      <div class="debug-header">
        <el-tabs v-model="activeTab" type="card" class="debug-tabs">
          <el-tab-pane label="Logcat" name="logcat" />
          <el-tab-pane label="Shell" name="shell" />
          <el-tab-pane label="Network" name="network" />
          <el-tab-pane label="Perf" name="perf" />
          <el-tab-pane label="Apps" name="apps" />
        </el-tabs>

        <div class="debug-actions">
          <el-tooltip :content="position === 'bottom' ? '移到右侧' : '移到底部'" placement="top">
            <el-button size="small" text @click="togglePosition">
              {{ position === 'bottom' ? '⇨' : '⇩' }}
            </el-button>
          </el-tooltip>
        </div>
      </div>
    </template>

    <!-- 动态渲染当前标签页的组件 -->
    <div class="tab-content">
      <LogcatView v-if="activeTab === 'logcat'" :device-id="deviceId" />
      <ShellView v-else-if="activeTab === 'shell'" :device-id="deviceId" />
      <div v-else class="coming-soon">
        {{ activeTab }} tab (coming soon)
      </div>
    </div>
  </DockPanel>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import DockPanel from '@/components/ui/DockPanel.vue'
import LogcatView from './LogcatView.vue'
import ShellView from './ShellView.vue'

/** 目标设备 ID（从父组件传入）。 */
defineProps<{ deviceId: string }>()

const emit = defineEmits<{
  (e: 'position-change', position: 'bottom' | 'right'): void
}>()

/** 停靠位置：底部或右侧。 */
const position = ref<'bottom' | 'right'>('right')
/** 面板宽度（right 模式）。 */
const panelWidth = ref(500)
/** 面板高度（bottom 模式）。 */
const panelHeight = ref(400)
/** 当前激活的标签页。 */
const activeTab = ref('logcat')

/**
 * 切换停靠位置。
 * 在底部和右侧之间切换，并通知父组件更新布局。
 */
function togglePosition() {
  position.value = position.value === 'bottom' ? 'right' : 'bottom'
  emit('position-change', position.value)
}

/**
 * 键盘快捷键处理。
 *   - Ctrl+` : 切换面板位置
 *   - Ctrl+L : 聚焦 Logcat 标签
 *   - Ctrl+Shift+` : 聚焦 Shell 标签
 */
function handleKeydown(e: KeyboardEvent) {
  if (e.ctrlKey && e.key === '`') {
    e.preventDefault()
    togglePosition()
  }
  if (e.ctrlKey && e.key === 'l') {
    e.preventDefault()
    activeTab.value = 'logcat'
  }
  if (e.ctrlKey && e.shiftKey && e.key === '`') {
    e.preventDefault()
    activeTab.value = 'shell'
  }
}

onMounted(() => {
  window.addEventListener('keydown', handleKeydown)
})

onUnmounted(() => {
  window.removeEventListener('keydown', handleKeydown)
})
</script>

<style scoped>
.debug-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 0.5rem;
  background: #f5f5f5;
  border-bottom: 1px solid #ddd;
}

.debug-tabs {
  flex: 1;
}

/* 移除 el-tabs 的底部边框，因为它在 header 中 */
:deep(.debug-tabs .el-tabs__header) {
  margin: 0;
  border-bottom: none;
}

:deep(.debug-tabs .el-tabs__nav-wrap::after) {
  display: none;
}

:deep(.debug-tabs .el-tabs__item) {
  height: 32px;
  line-height: 32px;
  padding: 0 12px;
  font-size: 12px;
}

.debug-actions {
  display: flex;
  gap: 0.25rem;
  flex-shrink: 0;
}

.tab-content {
  flex: 1;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.coming-soon {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: #999;
  font-size: 14px;
}
</style>
