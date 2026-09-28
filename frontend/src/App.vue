<!--
  应用根组件
  ============

  提供全局布局结构：
    - 顶部导航栏（el-header）：显示应用标题和主菜单
    - 主内容区（el-main）：通过 <router-view> 渲染当前路由对应的页面组件

  子组件关系：
    App.vue → router/index.ts → views/*.vue
-->
<template>
  <div id="app">
    <el-container>
      <el-header>
        <div class="header-content">
          <h1>OpenScrcpy</h1>
          <el-menu mode="horizontal" :router="true">
            <el-menu-item index="/">Dashboard</el-menu-item>
          </el-menu>
          <el-button
            text
            class="exit-btn"
            data-test="exit-btn"
            :loading="exiting"
            @click="onExitClick"
          >
            退出服务
          </el-button>
        </div>
      </el-header>
      <el-main>
        <router-view />
      </el-main>
    </el-container>
    <CommandPalette />
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import CommandPalette from '@/components/ui/CommandPalette.vue'
import { api } from '@/services/api'

const exiting = ref(false)
let exitPollTimer: ReturnType<typeof setInterval> | null = null
const EXIT_POLL_LIMIT = 60 // 500ms × 60 = 30s 上限

async function onExitClick() {
  try {
    await ElMessageBox.confirm(
      '确定要退出 OpenScrcpy 服务吗？退出后将释放端口，需重新启动程序才能继续使用。',
      '退出服务',
      { confirmButtonText: '退出', cancelButtonText: '取消', type: 'warning' }
    )
  } catch {
    return // 用户取消
  }
  try {
    const result = await api.shutdownSystem()
    if (!result.accepted) {
      ElMessage.warning('当前运行方式不支持在线退出，请手动停止服务')
      return
    }
    exiting.value = true
    ElMessage.info('服务正在退出…')
    startExitPolling()
  } catch {
    // 请求未送达即已断连：服务先于响应退出（如 stop.bat 抢先），视为已退出
    ElMessage.success('服务已退出，请关闭此页面')
  }
}

function startExitPolling() {
  let ticks = 0
  exitPollTimer = setInterval(async () => {
    ticks += 1
    try {
      await api.getHealth()
    } catch {
      stopExitPolling()
      ElMessage.success('服务已退出，请关闭此页面')
      return
    }
    if (ticks >= EXIT_POLL_LIMIT) {
      stopExitPolling()
      ElMessage.warning('服务仍在运行，请稍后再试或使用任务管理器结束 OpenScrcpy.exe')
    }
  }, 500)
}

function stopExitPolling() {
  if (exitPollTimer) {
    clearInterval(exitPollTimer)
    exitPollTimer = null
  }
}

onBeforeUnmount(stopExitPolling)
</script>

<style>
#app {
  font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
}

.header-content {
  display: flex;
  align-items: center;
  gap: 2rem;
}

.exit-btn {
  margin-left: auto;
}

h1 {
  margin: 0;
  font-size: 1.5rem;
}
</style>
