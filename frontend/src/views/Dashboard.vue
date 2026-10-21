<!--
  设备管理面板（Dashboard）
  ===========================

  应用的主页面，展示所有通过 ADB 连接的设备列表。

  功能：
    - 显示设备列表（表格形式）：设备 ID、屏幕缩略图、型号、系统版本、分辨率、电量、状态
    - 网段扫描（方案 36）：独立 scan-bar 行输入 CIDR 批量探测并连接
    - 添加设备对话框（方案 47 T5 页签）：网络 ADB 传统连接 /
      无线调试配对（Android 11+）两步式——步骤 1 弹窗配对、步骤 2 调试端口连接
    - 断开按钮：断开设备的 TCP/IP 连接
    - 连接按钮：跳转到设备详情页（/device/:id）
    - 刷新按钮：重新扫描设备；刷新缩略图按钮：强制重拉全表屏幕快照
    - SSE 实时监听：设备连接/断开时自动更新列表

  数据来源：
    使用 useDeviceStore 管理设备列表状态。
    组件挂载时自动调用 fetchDevices() 和 startSSE()。

  子组件关系：
    Dashboard.vue → stores/device.ts → services/api.ts
    例外：配对步骤 1 直调 services/api.ts.pairDevice（不产生设备状态，无需入 store）
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
        placeholder="如 192.168.8.0/24 或 192.168.8.10-100"
        class="scan-cidr"
        clearable
        @keyup.enter="handleScan"
      />
      <el-popover placement="bottom" :width="340" trigger="click">
        <template #reference>
          <el-button class="scan-help" size="small" circle>?</el-button>
        </template>
        <div class="scan-help-body">
          <p><strong>单个 IP：</strong><code>192.168.8.18</code>（只扫这一台）</p>
          <p><strong>网段（CIDR）：</strong><code>192.168.8.0/24</code>（支持 /22 ~ /32）</p>
          <p>
            <strong>起止区间：</strong><code>192.168.8.10-100</code>（只扫 .10 到 .100；
            也支持完整写法 <code>192.168.8.10-192.168.9.20</code>）
          </p>
          <p>端口默认 5555；勾选「仅扫描不连接」只探测，结果中可逐台点击连接。</p>
        </div>
      </el-popover>
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

    <!-- 方案 36 第三批：扫描记录独立卡片（仅未连接主机，localStorage 持久化） -->
    <div v-if="recordHosts.length > 0" class="scan-record-card">
      <div class="scan-record-head">
        <span class="scan-record-title">扫描记录</span>
        <span v-if="scanRecord" class="scan-record-meta">
          {{ scanRecord.label }} : {{ scanRecord.port }} · {{ recordTimeText }}
        </span>
        <el-button size="small" @click="clearScanRecord">清除记录</el-button>
      </div>
      <ul class="scan-record-hosts">
        <li v-for="entry in recordHosts" :key="entry.ip" class="scan-record-row">
          <el-checkbox
            :model-value="recordSelected.has(entry.ip)"
            @update:model-value="toggleRecordSelect(entry.ip, $event)"
          />
          <span class="scan-host-ip">{{ entry.ip }}</span>
          <el-tag
            v-if="hostState(entry.ip) === 'failed'"
            type="danger"
            size="small"
            :title="hostFailureMessage(entry.ip)"
          >
            连接失败
          </el-tag>
          <el-tag v-else type="info" size="small">未连接</el-tag>
          <el-button
            size="small"
            :loading="hostConnectingIp === entry.ip"
            :disabled="batchConnectingIps"
            @click="connectHost(entry.ip)"
          >
            {{ hostState(entry.ip) === 'failed' ? '重试' : '连接' }}
          </el-button>
        </li>
      </ul>
      <div v-if="recordSelected.size > 0" class="record-batch-bar">
        <span class="batch-count">已选 {{ recordSelected.size }} 台</span>
        <el-button
          type="primary"
          size="small"
          :loading="batchConnectingIps"
          @click="batchConnectHosts"
        >
          连接所选
        </el-button>
        <el-button size="small" @click="recordSelected = new Set()">取消选择</el-button>
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

    <!-- 方案 50 T2：本地过滤（备注/型号/设备ID 三字段子串，大小写不敏感） -->
    <div class="device-search-bar">
      <el-input
        v-model="searchKeyword"
        placeholder="搜索备注/型号/设备ID"
        class="device-search"
        clearable
      />
    </div>

    <!-- 方案 36 增补 D10：批量断开工具条（选中 ≥1 台 TCP 设备时出现） -->
    <div v-if="selectedDevices.length > 0" class="batch-bar">
      <span class="batch-count">已选 {{ selectedDevices.length }} 台</span>
      <el-button
        type="danger"
        size="small"
        :loading="batchDisconnecting"
        @click="handleBatchDisconnect"
      >
        批量断开
      </el-button>
      <el-button size="small" @click="clearDeviceSelection">取消选择</el-button>
    </div>

    <el-table
      ref="tableRef"
      :data="filteredDevices"
      v-loading="store.loading"
      :row-class-name="rowClassName"
      @selection-change="handleSelectionChange"
    >
      <!-- D10：选择列，仅 TCP 设备可勾选（USB 无 TCP 断开语义）；EP 回调传行对象，
           经 rowSelectable 取 id 后与行内「断开」按钮同一判定口径 -->
      <el-table-column type="selection" :selectable="rowSelectable" width="40" />
      <el-table-column label="设备ID" min-width="140" show-overflow-tooltip>
        <template #default="{ row }">
          <!-- 方案 50 T2：备注优先展示，下行灰字保留序列号 -->
          <div class="device-id-cell">
            <span class="alias">{{ row.alias || row.id }}</span>
            <small v-if="row.alias">{{ row.id }}</small>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="屏幕" width="120" align="center">
        <template #default="{ row }">
          <div
            class="thumb-cell"
            style="position: relative; width: 96px; height: 72px; margin: 0 auto"
          >
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
                style="position: absolute; top: 2px; right: 2px"
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
      <el-table-column label="分辨率" min-width="100" align="center">
        <template #default="{ row }">
          {{ row.resolution[0] }}x{{ row.resolution[1] }}
        </template>
      </el-table-column>
      <el-table-column prop="battery" label="电量" min-width="80" />
      <el-table-column prop="status" label="状态" min-width="120">
        <template #default="{ row }">
          <!-- 方案 35 D7 + 半死设备治理：网络在线但 ADB 命令持续超时（slow=true）时，
               不再显示误导性的绿色「在线」，改 danger「无响应」+ tooltip 可执行指引，
               避免用户点了进黑屏。查询恢复后手动刷新即灭灯回落「在线」。 -->
          <el-tooltip v-if="row.slow" :content="SLOW_TIP" placement="top">
            <el-tag type="danger" size="small" class="slow-tag">无响应</el-tag>
          </el-tooltip>
          <el-tag v-else :type="statusTagType(row.status)" size="small">
            {{ statusLabel(row.status) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="260" fixed="right">
        <template #default="{ row }">
          <el-button size="small" @click="editAlias(row)">
            备注
          </el-button>
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
          <el-button size="small" type="primary" :plain="row.slow" @click="connect(row.id)">
            连接
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 添加设备对话框（方案 47 T5：页签——传统网络 ADB / 无线调试配对两步式） -->
    <el-dialog
      v-model="dialogVisible"
      title="添加设备"
      width="420px"
      :close-on-click-modal="false"
      @closed="resetDialog"
    >
      <el-tabs v-model="activeTab">
        <el-tab-pane label="网络 ADB（传统）" name="adb">
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
        </el-tab-pane>
        <el-tab-pane label="无线调试配对（Android 11+）" name="pair">
          <div v-if="pairStep === 1" class="pair-step">
            <el-form
              ref="pairFormRef"
              :model="pairForm"
              :rules="pairRules"
              label-width="90px"
            >
              <el-form-item label="IP 地址" prop="ip">
                <el-input
                  v-model="pairForm.ip"
                  placeholder="配对弹窗显示的 IP"
                  clearable
                  @keyup.enter="handlePair"
                />
              </el-form-item>
              <el-form-item label="配对端口" prop="port">
                <el-input-number
                  v-model="pairForm.port"
                  :min="1"
                  :max="65535"
                  placeholder="配对弹窗显示的端口"
                  style="width: 100%"
                />
              </el-form-item>
              <el-form-item label="配对码" prop="code">
                <el-input
                  v-model="pairForm.code"
                  placeholder="6 位配对码"
                  maxlength="6"
                  clearable
                  @keyup.enter="handlePair"
                />
              </el-form-item>
            </el-form>
            <div class="form-tip">
              <p>
                手机「开发者选项 → 无线调试 → 使用配对码配对设备」，
                输入弹窗显示的 IP、端口与 6 位配对码。
              </p>
              <p>配对端口每次弹窗都会变化；配对成功后请用无线调试主页面显示的端口连接。</p>
            </div>
          </div>
          <div v-else class="pair-step">
            <el-form label-width="90px">
              <el-form-item label="IP 地址">
                <span class="readonly-ip">{{ pairForm.ip }}</span>
              </el-form-item>
              <el-form-item label="调试端口">
                <el-input-number
                  v-model="pairConnectPort"
                  :min="1"
                  :max="65535"
                  placeholder="无线调试主页面端口"
                  style="width: 100%"
                />
              </el-form-item>
            </el-form>
            <div class="form-tip">
              <p>输入手机「无线调试」主页面显示的「IP 地址和端口」（与上一步的配对端口不同）。</p>
              <p>以后重连只需执行本步骤连接即可；Android 16 部分机型重启后需重新配对。</p>
            </div>
            <a class="pair-back" @click="pairStep = 1">返回重新配对</a>
          </div>
        </el-tab-pane>
      </el-tabs>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button
          v-if="activeTab === 'adb'"
          type="primary"
          :loading="connecting"
          @click="handleConnect"
        >
          连接
        </el-button>
        <el-button
          v-else-if="pairStep === 1"
          type="primary"
          :loading="pairing"
          @click="handlePair"
        >
          配对
        </el-button>
        <el-button
          v-else
          type="primary"
          :loading="pairConnecting"
          @click="handlePairConnect"
        >
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
import { ElMessage, ElMessageBox } from 'element-plus'
import type { FormInstance, FormItemRule, FormRules } from 'element-plus'
import { useDeviceStore } from '@/stores/device'
import type { DeviceInfo } from '@/stores/device'
import { api, type ScanResult } from '@/services/api'

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
// 方案 36 增补 D9：扫描发起时的端口快照（防中途改表单导致结果条目连接错位）
const scanPort = ref(5555)
// D9：结果清单「已连接」判定 = 本次 connect_results 成功集合 ∪ 手动连接成功
// 集合 ∪ store 当前设备列表（含 `${ip}:${scanPort}`）
const hostConnectedIps = ref(new Set<string>())
const hostConnectingIp = ref<string | null>(null)

// ===== 扫描记录（方案 36 第三批）：未连接清单卡通 + localStorage 持久化 =====
interface ScanRecordHost {
  ip: string
  failed?: string
}
interface ScanRecordData {
  label: string
  port: number
  scannedAt: number
  hosts: ScanRecordHost[]
}
const SCAN_RECORD_KEY = 'oscrpy-scan-record'
const scanRecord = ref<ScanRecordData | null>(null)
const recordSelected = ref<Set<string>>(new Set())
const batchConnectingIps = ref(false)
// 手动连接失败的 ip → 原因（行转「连接失败」态，重试成功即清除）
const hostFailedIps = ref<Map<string, string>>(new Map())

// ===== 设备多选与批量断开（方案 36 增补 D10） =====
const tableRef = ref()
const selectedDevices = ref<DeviceInfo[]>([])
const batchDisconnecting = ref(false)

// ===== 设备备注与搜索（方案 50 T2） =====
const searchKeyword = ref('')
const filteredDevices = computed(() => {
  const kw = searchKeyword.value.trim().toLowerCase()
  if (!kw) return store.devices
  return store.devices.filter(
    (d) =>
      (d.alias ?? '').toLowerCase().includes(kw) ||
      d.model.toLowerCase().includes(kw) ||
      d.id.toLowerCase().includes(kw)
  )
})

// 半死设备（网络在线但 ADB 命令持续超时）状态标签的悬停指引
const SLOW_TIP =
  '设备网络在线但 ADB 命令持续超时——可能是响应慢，也可能设备过载/adbd 卡死。截图与推流此时会失败（黑屏）。请检查该设备负载，或重启设备后用 adb shell echo ok 探活再试。'

// B5 失联行状态呈现：adb 原始态中文标签 + 配色（offline/unauthorized 等，
// 列表不再静默过滤；未知态回落原文，不谎报）
const STATUS_LABELS: Record<string, string> = {
  online: '在线',
  offline: '离线',
  unauthorized: '待授权',
}
function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? status
}
function statusTagType(status: string): 'success' | 'warning' | 'danger' | 'info' {
  if (status === 'online') return 'success'
  if (status === 'unauthorized') return 'warning'
  if (status === 'offline') return 'danger'
  return 'info'
}

// slow 行整行置灰（缓存值语义）；B5 失联行同样置灰（列表数据为失联前缓存）
function rowClassName({ row }: { row: DeviceInfo }): string {
  if (row.slow) return 'row-slow'
  return row.status === 'online' ? '' : 'row-offline'
}

const form = reactive({
  ip: '',
  port: 5555,
})

// IP 格式规则：传统连接与配对页签（方案 47 T5）共用
const ipRules: FormItemRule[] = [
  { required: true, message: '请输入 IP 地址', trigger: 'blur' },
  {
    pattern: /^(\d{1,3}\.){3}\d{1,3}$/,
    message: '请输入有效的 IP 地址',
    trigger: 'blur',
  },
]

const formRules: FormRules = {
  ip: ipRules,
  port: [
    { required: true, message: '请输入端口', trigger: 'blur' },
  ],
}

// ===== 无线调试配对（方案 47 T5，两步式） =====
const activeTab = ref<'adb' | 'pair'>('adb')
const pairStep = ref<1 | 2>(1)
const pairing = ref(false)
const pairConnecting = ref(false)
const pairFormRef = ref<FormInstance>()
const pairForm = reactive({
  ip: '',
  port: undefined as number | undefined,
  code: '',
})
const pairConnectPort = ref<number | undefined>(undefined)

const pairRules: FormRules = {
  ip: ipRules,
  port: [{ required: true, message: '请输入配对弹窗显示的端口', trigger: 'blur' }],
  code: [
    { required: true, message: '请输入 6 位配对码', trigger: 'blur' },
    { pattern: /^\d{6}$/, message: '配对码须为 6 位数字', trigger: 'blur' },
  ],
}

// ===== 生命周期 =====
onMounted(async () => {
  // 第三批：扫描记录优先恢复（端口号快照随记录恢复，供 hostState 判定）
  restoreScanRecord()
  // SSE 订阅先就位（方案 35 D6）：列表刷新慢不推迟事件订阅
  store.startSSE()
  // 重进静默刷新：store 已有列表时不置 v-loading 遮罩（旧列表即时保留、数据到后换血），
  // 仅冷进入才显示加载遮罩
  await store.fetchDevices({ silent: store.devices.length > 0 })
})

onUnmounted(() => {
  store.stopSSE()
})

// ===== 方法 =====

/** 刷新设备列表 */
function refresh() {
  store.fetchDevices()
}

/** 行内编辑设备备注（方案 50 T2）：空串=清除；取消 prompt 不写入 */
async function editAlias(row: DeviceInfo) {
  let value: string
  try {
    const res = await ElMessageBox.prompt(
      '留空提交即清除备注；清除后展示回落设备 ID。',
      `设置设备备注 — ${row.id}`,
      {
        inputValue: row.alias ?? '',
        inputValidator: (v: string) =>
          (v ?? '').trim().length <= 64 || '备注不能超过 64 字符',
        confirmButtonText: '确定',
        cancelButtonText: '取消',
      }
    )
    value = (res.value ?? '').trim()
  } catch {
    return // 取消（reject）吞掉
  }
  try {
    await api.putSettings({ devices: { [row.id]: { alias: value } } })
    store.setLocalAlias(row.id, value)
    ElMessage.success(value ? '备注已更新' : '备注已清除')
  } catch (e) {
    ElMessage.error(`备注保存失败: ${extractApiError(e, '未知错误')}`)
  }
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
  if (scanning.value) return // 终评 M-1：扫描中回车重入会撞自家 409
  const cidr = scanForm.cidr.trim()
  if (!cidr) {
    ElMessage.error('请输入扫描网段')
    return
  }
  scanning.value = true
  scanResult.value = null
  hostConnectedIps.value = new Set() // 新一次扫描清空上次手动连接集合（D9）
  hostFailedIps.value = new Map() // 第三批：手动失败态随新扫描重置
  scanPort.value = scanForm.port // D9：端口显式快照，结果条目连接用快照值
  try {
    const result = await store.scanSubnet(cidr, !scanForm.scanOnly, scanForm.port)
    scanResult.value = result
    saveScanRecord()
  } catch (e) {
    ElMessage.error(`扫描失败: ${extractApiError(e, '未知错误')}`)
  } finally {
    scanning.value = false
  }
}

/** D9：开放主机条目状态三分类（已连接 / 连接失败 / 未连接） */
type HostState = 'connected' | 'failed' | 'open'
function hostState(ip: string): HostState {
  const attempt = scanResult.value?.connect_results.find((a) => a.ip === ip)
  if (attempt?.ok) return 'connected'
  if (hostConnectedIps.value.has(ip)) return 'connected'
  if (store.devices.some((d) => d.id === `${ip}:${scanPort.value}`)) return 'connected'
  if (hostFailedIps.value.has(ip)) return 'failed'
  if (attempt) return 'failed'
  return 'open'
}

/** 失败条目悬停提示（优先手动失败原因；其次后端 message；缺省回退固定文案） */
function hostFailureMessage(ip: string): string {
  const manual = hostFailedIps.value.get(ip)
  if (manual) return manual
  const attempt = scanResult.value?.connect_results.find((a) => a.ip === ip)
  return attempt?.message || '连接失败'
}

/** D9：结果条目点击连接（端口用扫描时快照；成功后即时转已连接） */
async function connectHost(ip: string) {
  hostConnectingIp.value = ip
  try {
    await store.connectDevice(ip, scanPort.value)
    hostConnectedIps.value = new Set([...hostConnectedIps.value, ip])
    const nextFailed = new Map(hostFailedIps.value)
    nextFailed.delete(ip)
    hostFailedIps.value = nextFailed
    ElMessage.success(`已连接 ${ip}:${scanPort.value}`)
    persistScanRecord()
  } catch (e) {
    const msg = extractApiError(e, '未知错误')
    ElMessage.error(`连接失败: ${msg}`)
    const nextFailed = new Map(hostFailedIps.value)
    nextFailed.set(ip, msg)
    hostFailedIps.value = nextFailed
  } finally {
    hostConnectingIp.value = null
  }
}

/** 第三批：行勾选开关（Set 整体替换保证响应性） */
function toggleRecordSelect(ip: string, checked: boolean) {
  const next = new Set(recordSelected.value)
  if (checked) next.add(ip)
  else next.delete(ip)
  recordSelected.value = next
}

/** 第三批：批量连接所选（顺序逐台；失败台即时报错；结束汇总一次） */
async function batchConnectHosts() {
  const targets = recordHosts.value.filter(
    (h) => recordSelected.value.has(h.ip) && hostState(h.ip) !== 'connected'
  )
  if (targets.length === 0) return
  batchConnectingIps.value = true
  try {
    let okCount = 0
    let failedCount = 0
    for (const h of targets) {
      try {
        await store.connectDevice(h.ip, scanPort.value)
        hostConnectedIps.value = new Set([...hostConnectedIps.value, h.ip])
        const nextFailed = new Map(hostFailedIps.value)
        nextFailed.delete(h.ip)
        hostFailedIps.value = nextFailed
        okCount++
      } catch (e) {
        const msg = extractApiError(e, '未知错误')
        failedCount++
        const nextFailed = new Map(hostFailedIps.value)
        nextFailed.set(h.ip, msg)
        hostFailedIps.value = nextFailed
        ElMessage.error(`连接失败 ${h.ip}: ${msg}`)
      }
    }
    recordSelected.value = new Set()
    if (failedCount === 0) ElMessage.success(`已连接 ${okCount} 台设备`)
    else if (okCount > 0) ElMessage.warning(`成功 ${okCount} 台，失败 ${failedCount} 台`)
    else ElMessage.error(`连接失败 ${failedCount} 台`)
    // 落盘异常（localStorage 满/禁用）不逃逸：单独兜底，汇总提示已发出后再补告警
    if (okCount > 0) {
      try {
        persistScanRecord()
      } catch {
        ElMessage.warning('扫描记录保存失败（不影响已连接设备）')
      }
    }
  } finally {
    // 复位入 finally：任何未预见异常都不再永久禁用批连按钮（方案 51 F1-18）
    batchConnectingIps.value = false
  }
}

/** 第三批：待连接清单（记录中剔除已连接主机；卡片为空时整体隐藏） */
const recordHosts = computed(() =>
  (scanRecord.value?.hosts ?? []).filter((h) => hostState(h.ip) !== 'connected')
)

/** 第三批：记录扫描时间（HH:MM:SS） */
const recordTimeText = computed(() =>
  scanRecord.value ? new Date(scanRecord.value.scannedAt).toLocaleTimeString() : ''
)

/** 第三批：扫描完成后落盘（已连接主机不入记录，失败主机带原因） */
function saveScanRecord() {
  const r = scanResult.value
  if (!r) return
  const hosts: ScanRecordHost[] = r.open_hosts
    .filter((ip) => hostState(ip) !== 'connected')
    .map((ip) => {
      const attempt = r.connect_results.find((a) => a.ip === ip)
      const entry: ScanRecordHost = { ip }
      if (attempt && !attempt.ok && attempt.message) entry.failed = attempt.message
      return entry
    })
  scanRecord.value = {
    label: r.label,
    port: scanPort.value,
    scannedAt: Date.now(),
    hosts,
  }
  localStorage.setItem(SCAN_RECORD_KEY, JSON.stringify(scanRecord.value))
}

/** 第三批：页载恢复记录（端口快照随记录恢复，损坏数据静默清理） */
function restoreScanRecord() {
  try {
    const raw = localStorage.getItem(SCAN_RECORD_KEY)
    if (!raw) return
    const data = JSON.parse(raw) as ScanRecordData
    if (!Array.isArray(data.hosts)) return
    scanRecord.value = data
    scanPort.value = data.port
  } catch {
    localStorage.removeItem(SCAN_RECORD_KEY)
  }
}

/** 第三批：连接成功后修剪记录并同步落盘（已连接主机移出清单） */
function persistScanRecord() {
  if (!scanRecord.value) return
  scanRecord.value = {
    ...scanRecord.value,
    hosts: scanRecord.value.hosts.filter((h) => hostState(h.ip) !== 'connected'),
  }
  localStorage.setItem(SCAN_RECORD_KEY, JSON.stringify(scanRecord.value))
}

/** 第三批：清除记录（卡片 + localStorage） */
function clearScanRecord() {
  scanRecord.value = null
  localStorage.removeItem(SCAN_RECORD_KEY)
}

/** 强制重拉全表在线设备缩略图（绕过 TTL）；失联行必失败，跳过（B5） */
function refreshThumbnails() {
  void store.loadThumbnails(store.devices.filter((d) => d.status === 'online').map((d) => d.id), true)
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

/** 重置对话框表单（方案 47 T5：页签/配对状态一并复位，重开不残留） */
function resetDialog() {
  form.ip = ''
  form.port = 5555
  formRef.value?.resetFields()
  activeTab.value = 'adb'
  pairStep.value = 1
  pairForm.ip = ''
  pairForm.port = undefined
  pairForm.code = ''
  pairConnectPort.value = undefined
  pairFormRef.value?.resetFields()
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

/** 配对步骤 1：弹窗 ip:port + 6 位码执行 adb pair（方案 47 T5） */
async function handlePair() {
  if (!pairFormRef.value) return

  const valid = await pairFormRef.value.validate().catch(() => false)
  if (!valid) return

  pairing.value = true
  try {
    await api.pairDevice(pairForm.ip, pairForm.port as number, pairForm.code)
    ElMessage.success('配对成功，请在手机「无线调试」主页面查看连接端口')
    pairStep.value = 2
  } catch (e) {
    ElMessage.error(`配对失败: ${extractApiError(e, '未知错误')}`)
  } finally {
    pairing.value = false
  }
}

/** 配对步骤 2：用无线调试主页面端口走既有 connect 链路（方案 47 T5） */
async function handlePairConnect() {
  if (!pairConnectPort.value) {
    ElMessage.warning('请输入无线调试主页面显示的端口')
    return
  }

  pairConnecting.value = true
  try {
    await store.connectDevice(pairForm.ip, pairConnectPort.value)
    ElMessage.success(`已连接到 ${pairForm.ip}:${pairConnectPort.value}`)
    dialogVisible.value = false
  } catch (e) {
    ElMessage.error(`连接失败: ${extractApiError(e, '未知错误')}`)
  } finally {
    pairConnecting.value = false
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

/** 判断设备 ID 是否为 TCP/IP 连接 */
function isTcpDevice(deviceId: string): boolean {
  // TCP/IP 设备 ID 格式为 "ip:port" 或 "ip"
  return /^(\d{1,3}\.){3}\d{1,3}(:\d+)?$/.test(deviceId)
}

/** D10：selection 列 selectable 回调（EP 传行对象，与行内「断开」同口径） */
function rowSelectable(row: DeviceInfo): boolean {
  return isTcpDevice(row.id)
}

// ===== 设备多选与批量断开（方案 36 增补 D10） =====

/** 表格选中变化（el-table selection-change 通道） */
function handleSelectionChange(rows: DeviceInfo[]) {
  selectedDevices.value = rows
}

/** 显式清空表格选择（工具条「取消选择」/ 批量断开完成后） */
function clearDeviceSelection() {
  // 本地状态先行清空（工具条即时消失，不依赖 EP selection-change 回发时序），
  // 表格勾选态交给 clearSelection 同步
  selectedDevices.value = []
  tableRef.value?.clearSelection()
}

/**
 * 批量断开选中设备：ElMessageBox 二次确认 → store.batchDisconnect
 * （逐台顺序、失败不中断、成功项本地移除 + 仅一次静默重拉）；
 * 完成后汇总提示并显式清除选择。
 */
async function handleBatchDisconnect() {
  const ids = selectedDevices.value.map((d) => d.id)
  if (ids.length === 0) return
  try {
    await ElMessageBox.confirm(
      `确定断开选中的 ${ids.length} 台设备吗？`,
      '批量断开',
      { type: 'warning', confirmButtonText: '断开', cancelButtonText: '取消' }
    )
  } catch {
    return // 用户取消
  }
  batchDisconnecting.value = true
  try {
    const result = await store.batchDisconnect(ids)
    if (result.failed.length > 0) {
      ElMessage.warning(
        `已断开 ${result.ok.length} 台，失败 ${result.failed.length} 台：` +
          result.failed.map((f) => f.id).join('、')
      )
    } else {
      ElMessage.success(`已断开 ${result.ok.length} 台设备`)
    }
  } finally {
    batchDisconnecting.value = false
    clearDeviceSelection()
  }
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

/* 方案 36 第三批：扫描记录独立卡片 */
.scan-record-card {
  margin-bottom: 1rem;
  padding: 0.75rem 1rem;
  background: #fff;
  border: 1px solid #e4e7ed;
  border-radius: 4px;
}

.scan-record-head {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  margin-bottom: 0.5rem;
}

.scan-record-title {
  font-weight: 600;
  font-size: 14px;
}

.scan-record-meta {
  font-size: 12px;
  color: #909399;
}

.scan-record-hosts {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem 1.25rem;
}

.scan-record-row {
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
}

.record-batch-bar {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  margin-top: 0.5rem;
  padding: 0.5rem 0.75rem;
  background: #ecf5ff;
  border: 1px solid #d9ecff;
  border-radius: 4px;
}

.record-batch-bar .batch-count {
  color: #409eff;
}

/* 方案 36 增补 D10：批量断开工具条 */
/* 方案 50 T2：设备搜索框与 ID 列复合单元格 */
.device-search-bar {
  max-width: 280px;
  margin-bottom: 0.75rem;
}

.device-id-cell {
  display: flex;
  flex-direction: column;
  line-height: 1.3;
}

.device-id-cell small {
  color: #909399;
  font-size: 12px;
}

.batch-bar {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  margin-bottom: 0.5rem;
  padding: 0.5rem 0.75rem;
  background: #fef0f0;
  border: 1px solid #fde2e2;
  border-radius: 4px;
}

.batch-count {
  font-size: 13px;
  color: #f56c6c;
}

.scan-help {
  flex-shrink: 0;
}

.thumb-cell {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
}

.thumb-image {
  width: 96px;
  height: 72px;
  display: block;
  border-radius: 3px;
}

.thumb-loading,
.thumb-placeholder,
.thumb-failed {
  width: 96px;
  height: 72px;
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

/* 半死设备整行文字置灰（缓存值语义）；状态标签/按钮自带配色不受影响 */
:deep(.row-slow .cell) {
  color: #a8abb2;
}

/* B5 失联行（offline/unauthorized 等）整行置灰：信息为失联前缓存值 */
:deep(.row-offline .cell) {
  color: #a8abb2;
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

/* 方案 47 T5 配对页签：步骤 2 只读 IP + 返回重新配对链接 */
.pair-step {
  padding-top: 0.25rem;
}

.readonly-ip {
  line-height: 32px;
}

.pair-back {
  display: inline-block;
  margin-top: 0.5rem;
  font-size: 12px;
  color: var(--el-color-primary);
  cursor: pointer;
}
</style>
