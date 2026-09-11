<!--
  应用管理视图
  ============

  管理 Android 设备上安装的应用：
    - 查看应用列表（区分系统/第三方）
    - 启动/停止应用
    - 卸载应用
    - 清除应用数据
    - 查看应用详情（版本、大小、内存）

  数据来源：
    REST API /api/apps/{device_id}

  功能：
    - 应用列表（el-table）
    - 搜索过滤（按包名）
    - 筛选（全部/用户/系统/运行中）
    - 操作按钮（启动/停止/更多）
-->

<template>
  <div class="apps-view">
    <!-- 工具栏 -->
    <div class="toolbar">
      <el-input
        v-model="searchQuery"
        placeholder="搜索包名..."
        prefix-icon="Search"
        clearable
        size="small"
        class="search-input"
      />

      <el-select v-model="filter" size="small" class="filter-select">
        <el-option label="用户应用" value="user" />
        <el-option label="全部应用" value="all" />
        <el-option label="系统应用" value="system" />
        <el-option label="运行中" value="running" />
      </el-select>

      <el-button size="small" :icon="Refresh" :loading="loading" @click="loadApps(true)">
        刷新
      </el-button>
    </div>

    <!-- 应用列表 -->
    <div class="app-list">
      <el-table
        :data="filteredApps"
        v-loading="loading"
        size="small"
        stripe
        height="100%"
        @row-click="showAppDetail"
        style="cursor: pointer;"
      >
        <el-table-column label="包名" min-width="200">
          <template #default="{ row }">
            <div class="app-name">{{ row.package_name }}</div>
            <div class="app-version">v{{ row.version_name || '-' }}</div>
          </template>
        </el-table-column>

        <el-table-column label="状态" width="90" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.is_running" type="success" size="small">运行中</el-tag>
            <el-tag v-else type="info" size="small">未运行</el-tag>
          </template>
        </el-table-column>

        <el-table-column label="操作" width="240" align="center">
          <template #default="{ row }">
            <div class="action-buttons">
              <el-button
                v-if="!row.is_running"
                type="success"
                size="default"
                @click.stop="launchApp(row.package_name)"
              >
                启动
              </el-button>
              <el-button
                v-else
                type="danger"
                size="default"
                @click.stop="stopApp(row.package_name)"
              >
                停止
              </el-button>
              <el-dropdown
                trigger="click"
                @command="(cmd: string) => handleCommand(cmd, row.package_name)"
              >
                <el-button type="info" size="default" @click.stop>更多 ▾</el-button>
                <template #dropdown>
                  <el-dropdown-menu>
                    <el-dropdown-item command="clear-data">清除数据</el-dropdown-item>
                    <el-dropdown-item command="uninstall" :disabled="row.is_system">卸载</el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>
            </div>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <!-- 状态栏 -->
    <div class="status-bar">
      <span v-if="filteredApps.length === apps.length">
        共 {{ apps.length }} 个应用
      </span>
      <span v-else>
        显示 {{ filteredApps.length }} / {{ apps.length }} 个应用
      </span>
      <span class="separator">|</span>
      <span>{{ runningCount }} 个运行中</span>
    </div>

    <!-- 应用详情对话框 -->
    <el-dialog
      v-model="detailDialogVisible"
      title="应用详情"
      width="600px"
      :close-on-click-modal="false"
    >
      <div v-if="selectedApp" class="app-detail">
        <!-- 基本信息 -->
        <div class="detail-section">
          <h4>基本信息</h4>
          <el-descriptions :column="2" border size="small">
            <el-descriptions-item label="包名" :span="2">{{ selectedApp.package_name }}</el-descriptions-item>
            <el-descriptions-item label="版本">{{ selectedApp.version_name || '-' }}</el-descriptions-item>
            <el-descriptions-item label="版本代码">{{ selectedApp.version_code }}</el-descriptions-item>
            <el-descriptions-item label="状态">
              <el-tag v-if="selectedApp.is_running" type="success" size="small">运行中</el-tag>
              <el-tag v-else type="info" size="small">未运行</el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="类型">
              <el-tag v-if="selectedApp.is_system" type="warning" size="small">系统应用</el-tag>
              <el-tag v-else type="success" size="small">用户应用</el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="安装时间">{{ selectedApp.install_time || '-' }}</el-descriptions-item>
            <el-descriptions-item label="更新时间">{{ selectedApp.update_time || '-' }}</el-descriptions-item>
          </el-descriptions>
        </div>

        <!-- 运行信息 -->
        <div class="detail-section">
          <h4>运行信息</h4>
          <el-descriptions :column="2" border size="small">
            <el-descriptions-item label="APK 大小">
              <span v-if="detailInfo.apk_size_mb">{{ detailInfo.apk_size_mb.toFixed(2) }} MB</span>
              <span v-else class="text-muted">-</span>
            </el-descriptions-item>
            <el-descriptions-item label="内存占用">
              <span v-if="detailInfo.memory_kb">{{ (detailInfo.memory_kb / 1024).toFixed(2) }} MB</span>
              <span v-else class="text-muted">-</span>
            </el-descriptions-item>
            <el-descriptions-item label="进程 ID" :span="2">
              <span v-if="detailInfo.pid">{{ detailInfo.pid }}</span>
              <span v-else class="text-muted">-</span>
            </el-descriptions-item>
          </el-descriptions>
        </div>

        <!-- 操作按钮 -->
        <div class="detail-section">
          <h4>操作</h4>
          <div class="action-buttons-grid">
            <el-button
              v-if="!selectedApp.is_running"
              type="success"
              @click="launchApp(selectedApp.package_name)"
            >
              启动应用
            </el-button>
            <el-button
              v-else
              type="danger"
              @click="stopApp(selectedApp.package_name)"
            >
              停止应用
            </el-button>
            <el-button
              type="warning"
              @click="handleCommand('clear-data', selectedApp.package_name)"
            >
              清除数据
            </el-button>
            <el-button
              type="danger"
              :disabled="selectedApp.is_system"
              @click="handleCommand('uninstall', selectedApp.package_name)"
            >
              卸载应用
            </el-button>
            <el-button
              type="info"
              @click="refreshDetail"
              :loading="detailLoading"
            >
              刷新信息
            </el-button>
          </div>
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '@/services/api'

interface AppInfo {
  package_name: string
  version_name: string
  version_code: number
  install_time: string
  update_time: string
  apk_size_mb: number
  is_system: boolean
  is_running: boolean
  pid: number | null
  memory_kb: number | null
}

const props = defineProps<{
  deviceId: string
}>()

const apps = ref<AppInfo[]>([])
const loading = ref(false)
const searchQuery = ref('')
const filter = ref<'user' | 'all' | 'system' | 'running'>('user')
/** 记录是否已加载过含系统应用的全量数据 */
const systemLoaded = ref(false)

// 应用详情对话框
const detailDialogVisible = ref(false)
const selectedApp = ref<AppInfo | null>(null)
const detailInfo = ref<Partial<AppInfo>>({})
const detailLoading = ref(false)

// 过滤后的应用列表
const filteredApps = computed(() => {
  let result = apps.value

  // 筛选
  if (filter.value === 'user') {
    result = result.filter(app => !app.is_system)
  } else if (filter.value === 'system') {
    result = result.filter(app => app.is_system)
  } else if (filter.value === 'running') {
    result = result.filter(app => app.is_running)
  }

  // 搜索
  if (searchQuery.value) {
    const query = searchQuery.value.toLowerCase()
    result = result.filter(app => app.package_name.toLowerCase().includes(query))
  }

  return result
})

// 运行中的应用数
const runningCount = computed(() => apps.value.filter(app => app.is_running).length)

onMounted(() => {
  loadApps()
})

// 监听筛选条件变化，无缓存时自动请求
watch(filter, () => {
  const needSystem = filter.value === 'all' || filter.value === 'system'
  // 需要系统应用但未加载，或完全无缓存，自动请求
  if (!systemLoaded.value && (needSystem || apps.value.length === 0)) {
    loadApps()
  }
})

async function loadApps(force = false) {
  const needSystem = filter.value === 'all' || filter.value === 'system'
  const hasFullCache = systemLoaded.value

  // 非强制刷新时，如果缓存满足当前需求，直接跳过
  if (!force && hasFullCache) {
    return
  }
  if (!force && !needSystem && apps.value.length > 0) {
    return
  }

  // 如果已有数据（缓存），先显示旧数据，后台刷新
  if (apps.value.length === 0) {
    loading.value = true
  }

  try {
    const data = await api.listApps(props.deviceId, needSystem)
    apps.value = data.apps

    if (needSystem) {
      systemLoaded.value = true
    }
  } catch (e) {
    console.error('[AppsView] Failed to load apps:', e)
    ElMessage.error('加载应用列表失败')
  } finally {
    loading.value = false
  }
}

async function launchApp(packageName: string) {
  try {
    await api.launchApp(props.deviceId, packageName)
    ElMessage.success(`已启动 ${packageName}`)
    await loadApps(true)
  } catch (e) {
    console.error('[AppsView] Failed to launch app:', e)
    ElMessage.error(`启动失败: ${packageName}`)
  }
}

async function stopApp(packageName: string) {
  try {
    await api.stopApp(props.deviceId, packageName)
    ElMessage.success(`已停止 ${packageName}`)
    await loadApps(true)
  } catch (e) {
    console.error('[AppsView] Failed to stop app:', e)
    ElMessage.error(`停止失败: ${packageName}`)
  }
}

async function handleCommand(command: string, packageName: string) {
  if (command === 'clear-data') {
    try {
      await ElMessageBox.confirm(
        `确定要清除 ${packageName} 的应用数据吗？此操作不可恢复。`,
        '确认清除数据',
        { type: 'warning' }
      )
      await api.clearAppData(props.deviceId, packageName)
      ElMessage.success(`已清除 ${packageName} 的数据`)
      await loadApps(true)
    } catch (e) {
      if (e !== 'cancel') {
        console.error('[AppsView] Failed to clear data:', e)
        ElMessage.error(`清除数据失败: ${packageName}`)
      }
    }
  } else if (command === 'uninstall') {
    try {
      await ElMessageBox.confirm(
        `确定要卸载 ${packageName} 吗？`,
        '确认卸载',
        { type: 'warning' }
      )
      await api.uninstallApp(props.deviceId, packageName)
      ElMessage.success(`已卸载 ${packageName}`)
      await loadApps(true)
    } catch (e) {
      if (e !== 'cancel') {
        console.error('[AppsView] Failed to uninstall:', e)
        ElMessage.error(`卸载失败: ${packageName}`)
      }
    }
  }
}

// 应用详情对话框功能
function showAppDetail(row: AppInfo) {
  selectedApp.value = row
  detailInfo.value = row
  detailDialogVisible.value = true
  refreshDetail()
}

async function refreshDetail() {
  if (!selectedApp.value) return

  detailLoading.value = true
  try {
    const detail = await api.getAppInfo(props.deviceId, selectedApp.value.package_name)
    // detailInfo 用于详情对话框显示，包含后端返回的最新信息
    detailInfo.value = detail
    // 更新列表中的应用信息，但保留原有的 is_running 状态
    const index = apps.value.findIndex(app => app.package_name === selectedApp.value?.package_name)
    if (index !== -1) {
      // 保留列表中的 is_running 状态，只更新其他字段
      const { is_running, ...detailWithoutStatus } = detail
      apps.value[index] = { ...apps.value[index], ...detailWithoutStatus }
      // selectedApp 也保留原有的 is_running 状态
      selectedApp.value = { ...selectedApp.value, ...detailWithoutStatus }
    }
  } catch (e) {
    console.error('[AppsView] Failed to load app detail:', e)
  } finally {
    detailLoading.value = false
  }
}
</script>

<style scoped>
.apps-view {
  display: flex;
  flex-direction: column;
  height: 100%;
  padding: 8px;
  gap: 8px;
}

.toolbar {
  display: flex;
  gap: 8px;
  align-items: center;
}

.search-input {
  flex: 1;
  max-width: 250px;
}

.filter-select {
  width: 120px;
}

.app-list {
  flex: 1;
  overflow: hidden;
}

.app-name {
  font-size: 12px;
  font-weight: 500;
  color: #303133;
  word-break: break-all;
}

.app-version {
  font-size: 11px;
  color: #909399;
  margin-top: 2px;
}

.status-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px;
  background: #f5f7fa;
  border-radius: 4px;
  font-size: 12px;
  color: #606266;
}

.separator {
  color: #dcdfe6;
}

.action-buttons {
  display: flex;
  gap: 8px;
  justify-content: center;
  align-items: center;
}

.action-buttons .el-button {
  min-width: 60px;
}

/* 应用详情对话框样式 */
.app-detail {
  padding: 0 10px;
}

.detail-section {
  margin-bottom: 20px;
}

.detail-section h4 {
  margin: 0 0 10px 0;
  font-size: 14px;
  font-weight: 600;
  color: #303133;
  border-bottom: 1px solid #e4e7ed;
  padding-bottom: 8px;
}

.detail-section .el-descriptions {
  margin-bottom: 10px;
}

.text-muted {
  color: #909399;
}

.action-buttons-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
  gap: 10px;
}

.action-buttons-grid .el-button {
  width: 100%;
}
</style>
