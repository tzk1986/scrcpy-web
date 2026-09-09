<!--
  设备管理面板（Dashboard）
  ===========================

  应用的主页面，展示所有通过 ADB 连接的设备列表。

  功能：
    - 显示设备列表（表格形式）：设备 ID、型号、系统版本、分辨率、电量、状态
    - 添加设备按钮：通过 TCP/IP 连接新设备（弹窗输入 IP 和端口）
    - 断开按钮：断开设备的 TCP/IP 连接
    - 连接按钮：跳转到设备详情页（/device/:id）
    - 刷新按钮：重新扫描设备
    - SSE 实时监听：设备连接/断开时自动更新列表

  数据来源：
    使用 useDeviceStore 管理设备列表状态。
    组件挂载时自动调用 fetchDevices() 和 startSSE()。

  子组件关系：
    Dashboard.vue → stores/device.ts → services/api.ts
-->
<template>
  <div class="dashboard">
    <div class="header">
      <h2>设备管理</h2>
      <div class="header-actions">
        <el-button type="primary" @click="showConnectDialog">
          <el-icon><Plus /></el-icon>
          添加设备
        </el-button>
        <el-button @click="refresh" :loading="store.loading">
          <el-icon><Refresh /></el-icon>
          刷新
        </el-button>
      </div>
    </div>

    <!-- 连接提示 -->
    <el-alert
      v-if="store.devices.length === 0 && !store.loading"
      title="暂无设备"
      type="info"
      :closable="false"
      class="empty-tip"
    >
      <p>当前没有检测到设备。</p>
      <p>
        请通过 USB 连接设备，或点击右上角
        <strong>"添加设备"</strong>
        通过 TCP/IP 连接。
      </p>
    </el-alert>

    <el-table :data="store.devices" v-loading="store.loading">
      <el-table-column prop="id" label="设备ID" min-width="180" />
      <el-table-column prop="model" label="型号" min-width="120" />
      <el-table-column prop="os_version" label="系统版本" min-width="100" />
      <el-table-column label="分辨率" min-width="100">
        <template #default="{ row }">
          {{ row.resolution[0] }}x{{ row.resolution[1] }}
        </template>
      </el-table-column>
      <el-table-column prop="battery" label="电量" min-width="80" />
      <el-table-column prop="status" label="状态" min-width="80">
        <template #default="{ row }">
          <el-tag :type="row.status === 'online' ? 'success' : 'info'" size="small">
            {{ row.status === 'online' ? '在线' : row.status }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="200" fixed="right">
        <template #default="{ row }">
          <el-button
            v-if="isTcpDevice(row.id)"
            size="small"
            type="danger"
            plain
            @click="handleDisconnect(row.id)"
            :loading="disconnectingId === row.id"
          >
            断开
          </el-button>
          <el-button size="small" type="primary" @click="connect(row.id)">
            连接
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 添加设备对话框 -->
    <el-dialog
      v-model="dialogVisible"
      title="添加设备"
      width="420px"
      :close-on-click-modal="false"
      @closed="resetDialog"
    >
      <el-form
        ref="formRef"
        :model="form"
        :rules="formRules"
        label-width="80px"
      >
        <el-form-item label="IP 地址" prop="ip">
          <el-input
            v-model="form.ip"
            placeholder="例如：192.168.1.100"
            clearable
            @keyup.enter="handleConnect"
          />
        </el-form-item>
        <el-form-item label="端口" prop="port">
          <el-input-number
            v-model="form.port"
            :min="1"
            :max="65535"
            placeholder="5555"
            style="width: 100%"
          />
        </el-form-item>
        <el-form-item>
          <div class="form-tip">
            <p>提示：设备需要先开启无线调试。</p>
            <p>在设备上执行：<code>adb tcpip 5555</code></p>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="handleConnect" :loading="connecting">
          连接
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted, reactive } from 'vue'
import { useRouter } from 'vue-router'
import { Plus, Refresh } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'
import { useDeviceStore } from '@/stores/device'

const store = useDeviceStore()
const router = useRouter()

// ===== 对话框状态 =====
const dialogVisible = ref(false)
const connecting = ref(false)
const disconnectingId = ref<string | null>(null)
const formRef = ref<FormInstance>()

const form = reactive({
  ip: '',
  port: 5555,
})

const formRules: FormRules = {
  ip: [
    { required: true, message: '请输入 IP 地址', trigger: 'blur' },
    {
      pattern: /^(\d{1,3}\.){3}\d{1,3}$/,
      message: '请输入有效的 IP 地址',
      trigger: 'blur',
    },
  ],
  port: [
    { required: true, message: '请输入端口', trigger: 'blur' },
  ],
}

// ===== 生命周期 =====
onMounted(async () => {
  await store.fetchDevices()
  store.startSSE()
})

onUnmounted(() => {
  store.stopSSE()
})

// ===== 方法 =====

/** 刷新设备列表 */
function refresh() {
  store.fetchDevices()
}

/** 跳转到设备详情页 */
function connect(deviceId: string) {
  router.push(`/device/${encodeURIComponent(deviceId)}`)
}

/** 显示添加设备对话框 */
function showConnectDialog() {
  dialogVisible.value = true
}

/** 重置对话框表单 */
function resetDialog() {
  form.ip = ''
  form.port = 5555
  formRef.value?.resetFields()
}

/** 处理连接设备 */
async function handleConnect() {
  if (!formRef.value) return

  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return

  connecting.value = true
  try {
    await store.connectDevice(form.ip, form.port)
    ElMessage.success(`已连接到 ${form.ip}:${form.port}`)
    dialogVisible.value = false
  } catch (e) {
    const errMsg = (e as Error).message || '连接失败'
    ElMessage.error(`连接失败: ${errMsg}`)
  } finally {
    connecting.value = false
  }
}

/** 处理断开设备 */
async function handleDisconnect(deviceId: string) {
  disconnectingId.value = deviceId
  try {
    await store.disconnectDevice(deviceId)
    ElMessage.success(`已断开 ${deviceId}`)
  } catch (e) {
    const errMsg = (e as Error).message || '断开失败'
    ElMessage.error(`断开失败: ${errMsg}`)
  } finally {
    disconnectingId.value = null
  }
}

/** 判断设备是否为 TCP/IP 连接 */
function isTcpDevice(deviceId: string): boolean {
  // TCP/IP 设备 ID 格式为 "ip:port" 或 "ip"
  return /^(\d{1,3}\.){3}\d{1,3}(:\d+)?$/.test(deviceId)
}
</script>

<style scoped>
.dashboard {
  padding: 1rem;
  height: 100%;
  display: flex;
  flex-direction: column;
}

.header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 1rem;
}

.header h2 {
  margin: 0;
  font-size: 1.5rem;
}

.header-actions {
  display: flex;
  gap: 0.5rem;
}

.empty-tip {
  margin-bottom: 1rem;
}

.empty-tip p {
  margin: 0.25rem 0;
}

.form-tip {
  font-size: 12px;
  color: #909399;
}

.form-tip p {
  margin: 0.25rem 0;
}

.form-tip code {
  background: #f5f7fa;
  padding: 2px 6px;
  border-radius: 3px;
  font-family: monospace;
}
</style>
