<!--
  调试面板
  =========

  右侧调试面板，包含多个标签页：
    - Logcat: 实时日志查看器（LogcatView.vue）
    - Shell: 远程 shell 终端（ShellView.vue）
    - Network: 网络抓包（待实现）
    - Performance: 性能监控（待实现）
    - Apps: 应用管理（待实现）

  使用 Element Plus 的 el-tabs 组件实现标签切换。

  Props:
    deviceId: 目标设备的 ADB 序列号

  子组件关系：
    DebugPanel.vue → LogcatView.vue
                   → ShellView.vue
-->
<template>
  <div class="debug-panel">
    <el-tabs v-model="activeTab" type="border-card">
      <el-tab-pane label="Logcat" name="logcat">
        <LogcatView :device-id="deviceId" />
      </el-tab-pane>
      <el-tab-pane label="Shell" name="shell">
        <ShellView :device-id="deviceId" />
      </el-tab-pane>
      <el-tab-pane label="Network" name="network">
        <div>Network tab (coming soon)</div>
      </el-tab-pane>
      <el-tab-pane label="Performance" name="perf">
        <div>Performance tab (coming soon)</div>
      </el-tab-pane>
      <el-tab-pane label="Apps" name="apps">
        <div>Apps tab (coming soon)</div>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import LogcatView from './LogcatView.vue'
import ShellView from './ShellView.vue'

/** 目标设备 ID（从父组件传入）。 */
defineProps<{ deviceId: string }>()

/** 当前激活的标签页（默认显示 Logcat）。 */
const activeTab = ref('logcat')
</script>

<style scoped>
.debug-panel {
  width: 500px;
  border-left: 1px solid #eee;
  display: flex;
  flex-direction: column;
}

:deep(.el-tabs) {
  height: 100%;
  display: flex;
  flex-direction: column;
}

:deep(.el-tabs__content) {
  flex: 1;
  overflow: auto;
}
</style>
