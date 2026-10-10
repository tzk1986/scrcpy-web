<!--
  设备管理面板（Dashboard）
  ===========================

  应用的主页面，展示所有通过 ADB 连接的设备列表。

  功能：
    - 显示设备列表（表格形式）：设备 ID、屏幕缩略图、型号、系统版本、分辨率、电量、状态
    - 网段扫描（方案 36）：独立 scan-bar 行输入 CIDR 批量探测并连接
    - 添加设备按钮：通过 TCP/IP 连接新设备（弹窗输入 IP 和端口）
    - 断开按钮：断开设备的 TCP/IP 连接
    - 连接按钮：跳转到设备详情页（/device/:id）
    - 刷新按钮：重新扫描设备；刷新缩略图按钮：强制重拉全表屏幕快照
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

    <!-- 网段扫描（方案 36）：独立行置于 header 之下、空态提示之上 -->
    <div class="scan-bar">
      <el-input
        v-model="scanForm.cidr"
        placeholder="192.168.8.0/24"
        class="scan-cidr"
        clearable
        @keyup.enter="handleScan"
      />
      <el-input-number
        v-model="scanForm.port"
        :min="1"
        :max="65535"
        class="scan-port"
      />
      <el-button type="primary" :loading="scanning" @click="handleScan">
        {{ scanning ? '正在扫描网段…' : '扫描并连接' }}
      </el-button>
      <el-checkbox v-model="scanForm.scanOnly">仅扫描不连接</el-checkbox>
      <el-button @click="refreshThumbnails">刷新缩略图</el-button>
    </div>

    <!-- 扫描结果回显（初始隐藏） -->
    <el-alert
      v-if="scanResult"
      title="扫描完成"
      type="info"
      :closable="false"
      class="scan-result"
    >
      <p>{{ scanSummaryText }}</p>
      <p v-if="scanResult.truncated">
        开放主机过多，仅连接前 64 台（其余保留在开放清单中）。
      </p>
    </el-alert>

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
      <el-table-column label="屏幕" width="90">
        <template #default="{ row }">
          <div class="thumb-cell">
            <template v-if="store.thumbnails[row.id]">
              <!-- 快照时间用原生 title（免 el-tooltip） -->
              <el-image
                :src="store.thumbnails[row.id].url"
                :preview-src-list="[store.thumbnails[row.id].url]"
                :title="thumbTitle(row.id)"
                fit="cover"
                preview-teleported
                class="thumb-image"
              />
              <el-button
                class="thumb-refresh"
                size="small"
                text
                title="刷新缩略图"
                @click="store.loadThumbnail(row.id, true)"
              >
                <el-icon><Refresh /></el-icon>
              </el-button>
            </template>
            <div v-else-if="store.thumbLoading[row.id]" class="thumb-loading">
              加载中…
            </div>
            <div
              v-else-if="store.thumbFailed[row.id]"
              class="thumb-failed"
              @click="store.loadThumbnail(row.id, true)"
            >
              加载失败<br />点击重试
            </div>
            <div
              v-else
              class="thumb-placeholder"
              @click="store.loadThumbnail(row.id)"
            >
              点击加载
            </div>
          </div>
        </template>
      </el-table-column>
      <el-table-column prop="model" label="型号" min-width="120" />
      <el-table-column prop="os_version" label="系统版本" min-width="100" />
      <el-table-column label="分辨率" min-width="100">
        <template #default="{ row }">
          {{ row.resolution[0] }}x{{ row.resolution[1] }}
        </template>
      </el-table-column>
      <el-table-column prop="battery" label="电量" min-width="80" />
      <el-table-column prop="status" label="状态" min-width="120">
        <template #default="{ row }">
          <el-tag :type="row.status === 'online' ? 'success' : 'info'" size="small">
            {{ row.status === 'online' ? '在线' : row.status }}
          </el-tag>
          <!-- 方案 35 D7：信息查询失败（最常见原因为 adb 响应慢），
               当前信息可能为缓存/默认值；查询恢复后手动刷新灭灯 -->
          <el-tag v-if="row.slow" type="danger" size="small" class="slow-tag">
            adb 慢
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
import { ref, computed, onMounted, onUnmounted, reactive } from 'vue'
import { useRouter } from 'vue-router'
import { Plus, Refresh } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'
import { useDeviceStore } from '@/stores/device'
import type { ScanResult } from '@/services/api'

const store = useDeviceStore()
const router = useRouter()

// ===== 对话框状态 =====
const dialogVisible = ref(false)
const connecting = ref(false)
const disconnectingId = ref<string | null>(null)
const formRef = ref<FormInstance>()

// ===== 网段扫描状态（方案 36） =====
const scanning = ref(false)
const scanResult = ref<ScanResult | null>(null)
const scanForm = reactive({
  cidr: '',
  port: 5555,
  scanOnly: false,
})

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
  // SSE 订阅先就位（方案 35 D6）：列表刷新慢不推迟事件订阅
  store.startSSE()
  await store.fetchDevices()
})

onUnmounted(() => {
  store.stopSSE()
})

// ===== 方法 =====

/** 刷新设备列表 */
function refresh() {
  store.fetchDevices()
}

/** 扫描结果摘要文本（探测 / 开放 / 连接成功 / 失败分类） */
const scanSummaryText = computed(() => {
  const r = scanResult.value
  if (!r) return ''
  let text = `探测 ${r.probed} 台 / 发现开放 ${r.open_hosts.length} 台`
  if (r.connect_results.length > 0) {
    const okCount = r.connect_results.filter((a) => a.ok).length
    const failed = r.connect_results.filter((a) => !a.ok)
    text += ` / 连接成功 ${okCount} 台`
    if (failed.length > 0) {
      const unreachable = failed.filter((a) => a.reason === 'unreachable').length
      const parts: string[] = []
      if (unreachable > 0) parts.push(`不可达 ${unreachable}`)
      if (failed.length - unreachable > 0) parts.push(`连接失败 ${failed.length - unreachable}`)
      text += `，失败 ${failed.length} 台（${parts.join(' / ')}）`
    }
  }
  return text
})

/** 处理网段扫描（store.scanSubnet 内部已静默重拉列表 + 对 ok 设备加载缩略图） */
async function handleScan() {
  const cidr = scanForm.cidr.trim()
  if (!cidr) {
    ElMessage.error('请输入扫描网段')
    return
  }
  scanning.value = true
  scanResult.value = null
  try {
    scanResult.value = await store.scanSubnet(cidr, !scanForm.scanOnly, scanForm.port)
  } catch (e) {
    ElMessage.error(`扫描失败: ${extractApiError(e, '未知错误')}`)
  } finally {
    scanning.value = false
  }
}

/** 强制重拉全表设备缩略图（绕过 TTL） */
function refreshThumbnails() {
  void store.loadThumbnails(store.devices.map((d) => d.id), true)
}

/** 缩略图快照时间提示（原生 title） */
function thumbTitle(deviceId: string): string {
  const entry = store.thumbnails[deviceId]
  return entry ? `快照时间：${new Date(entry.ts).toLocaleTimeString()}` : ''
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

/** 提取后端结构化错误（{"error":{"code","message"}}）的消息，回退到 Error.message */
function extractApiError(e: unknown, fallback: string): string {
  const body = (e as { response?: { data?: { error?: { message?: string } } } })
    .response?.data?.error
  return body?.message || (e as Error).message || fallback
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
    ElMessage.error(`连接失败: ${extractApiError(e, '未知错误')}`)
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
    ElMessage.error(`断开失败: ${extractApiError(e, '未知错误')}`)
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

.scan-bar {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 1rem;
}

.scan-cidr {
  width: 220px;
}

.scan-port {
  width: 120px;
}

.scan-result {
  margin-bottom: 1rem;
}

.scan-result p {
  margin: 0.25rem 0;
}

.thumb-cell {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
}

.thumb-image {
  width: 40px;
  height: 70px;
  display: block;
  border-radius: 3px;
}

.thumb-loading,
.thumb-placeholder,
.thumb-failed {
  width: 40px;
  height: 70px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 10px;
  line-height: 1.2;
  text-align: center;
  color: #909399;
  border: 1px dashed #dcdfe6;
  border-radius: 3px;
  cursor: pointer;
  user-select: none;
}

.thumb-failed {
  color: #f56c6c;
  border-color: #f56c6c;
}

.empty-tip {
  margin-bottom: 1rem;
}

.slow-tag {
  margin-left: 4px;
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
