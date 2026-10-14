<!--
  独立设置页（方案 45 T5；T6/T7/T8 在本文件追加区块）。
  保存语义：串流段「下次连接生效」；录屏段「立即生效」（spec §5.3）。
  数据链路：GET /api/settings 回填（生效值 + 来源）；PUT /api/settings
  提交后端键 patch（裁定 R4，表单值即后端键名，免映射层）；节传 null = 重置。
  h265 gating：T4 isH265DecoderSupported() 探测结果透传 StreamFieldSet。

  子组件关系：
    Settings.vue → StreamFieldSet.vue / RecordingFieldSet.vue
-->
<template>
  <div class="settings-page">
    <h2>设置</h2>
    <el-card class="section-card">
      <template #header>运行参数</template>
      <div class="section-row">
        <StreamFieldSet v-model="streamForm" :h265-supported="h265Supported" />
        <div class="actions">
          <el-button type="primary" data-test="save-stream" :loading="saving" @click="saveStream">
            保存串流参数
          </el-button>
          <el-button data-test="reset-stream" @click="resetStream">重置串流为出厂默认</el-button>
        </div>
      </div>
      <el-divider />
      <div class="section-row">
        <RecordingFieldSet v-model="recordingForm" />
        <div class="actions">
          <el-button
            type="primary"
            data-test="save-recording"
            :loading="saving"
            @click="saveRecording"
          >
            保存录屏参数
          </el-button>
          <el-button data-test="reset-recording" @click="resetRecording">
            重置录屏为出厂默认
          </el-button>
        </div>
      </div>
    </el-card>

    <el-card class="section-card" data-test="effective-values">
      <template #header>生效参数（当前实际生效值）</template>
      <template v-if="settingsView">
        <h4 class="group-title">串流（下次连接生效）</h4>
        <el-descriptions :column="2" border>
          <el-descriptions-item
            v-for="(entry, key) in settingsView.stream"
            :key="key"
            :label="key"
          >
            <span class="entry-value">{{ entry.value }}</span>
            <el-tag size="small" :type="SOURCE_TAG_TYPES[entry.source]">
              {{ SOURCE_LABELS[entry.source] }}
            </el-tag>
          </el-descriptions-item>
        </el-descriptions>
        <h4 class="group-title">录屏（每次开始录屏时读取）</h4>
        <el-descriptions :column="2" border>
          <el-descriptions-item
            v-for="(entry, key) in settingsView.recording"
            :key="key"
            :label="key"
          >
            <span class="entry-value">{{ entry.value }}</span>
            <el-tag size="small" :type="SOURCE_TAG_TYPES[entry.source]">
              {{ SOURCE_LABELS[entry.source] }}
            </el-tag>
          </el-descriptions-item>
        </el-descriptions>
      </template>
    </el-card>

    <el-card class="section-card" data-test="runtime-env">
      <template #header>运行环境</template>
      <template v-if="runtimeEnv">
        <el-descriptions :column="1" border>
          <el-descriptions-item label="scrcpy-server 版本">
            v{{ runtimeEnv.scrcpy_server_version }}
          </el-descriptions-item>
          <el-descriptions-item label="adb 版本">
            {{ runtimeEnv.adb_version }}
          </el-descriptions-item>
          <el-descriptions-item label="jar 路径">
            <el-collapse>
              <el-collapse-item title="展开查看完整路径">
                <pre class="path-value">{{ runtimeEnv.jar_path }}</pre>
              </el-collapse-item>
            </el-collapse>
          </el-descriptions-item>
          <el-descriptions-item label="adb 路径">
            <el-collapse>
              <el-collapse-item title="展开查看完整路径">
                <pre class="path-value">{{ runtimeEnv.adb_path }}</pre>
              </el-collapse-item>
            </el-collapse>
          </el-descriptions-item>
        </el-descriptions>
        <el-button data-test="check-update" :loading="checkingUpdate" @click="checkUpdate">
          检查更新
        </el-button>
        <div v-if="updateResult" data-test="update-result">
          <!-- 四态文案（方案 45 §4.2：仅提示，不自动下载，升级走方案 44 换包 SOP） -->
          <template v-if="updateResult.status === 'updateAvailable'">
            上游有 {{ updateResult.latest_version }}（本地 {{ updateResult.current_version }}），
            升级请走方案 44 换包流程
            <a v-if="updateResult.url" :href="updateResult.url" target="_blank" rel="noopener">
              查看发布页
            </a>
          </template>
          <template v-else-if="updateResult.status === 'upToDate'">
            已是最新（本地 {{ updateResult.current_version }}）
          </template>
          <template v-else-if="updateResult.status === 'installedNewer'">
            本地版本高于上游（本地 {{ updateResult.current_version }} / 上游
            {{ updateResult.latest_version }}）
          </template>
          <template v-else>{{ updateResult.error }}</template>
        </div>
      </template>
    </el-card>

    <el-card class="section-card" data-test="device-overrides">
      <template #header>设备级参数覆盖</template>
      <div class="section-row">
        <el-select
          v-model="overrideDeviceId"
          placeholder="选择设备"
          data-test="override-device-select"
        >
          <el-option v-for="d in deviceList" :key="d.device_id" :label="d.device_id" :value="d.device_id" />
        </el-select>
        <template v-if="overrideDeviceId">
          <StreamFieldSet v-model="overrideStreamForm" :h265-supported="h265Supported" />
          <div class="actions">
            <el-button
              type="primary"
              data-test="save-override"
              :loading="saving"
              @click="saveOverride"
            >
              保存该设备覆盖
            </el-button>
            <el-button data-test="clear-override" @click="clearOverride">清除该设备覆盖</el-button>
          </div>
        </template>
        <span v-else class="hint">选择设备后可为该设备单独调整串流参数（弱设备降规格）</span>
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'

import RecordingFieldSet, {
  type RecordingFormValue,
} from '@/components/settings/RecordingFieldSet.vue'
import StreamFieldSet, { type StreamFormValue } from '@/components/settings/StreamFieldSet.vue'
import {
  api,
  type RuntimeEnvView,
  type ScrcpyUpdateResult,
  type SettingEntry,
  type SettingsView,
} from '@/services/api'
import { isH265DecoderSupported } from '@/services/codecSupport'

/** 来源徽标文案/类型映射（T6）：仅渲染后端 source 字段，前端不做来源推导。 */
const SOURCE_LABELS: Record<SettingEntry['source'], string> = {
  default: '出厂默认',
  user: '用户设置',
  env: '环境覆盖',
}
const SOURCE_TAG_TYPES: Record<SettingEntry['source'], 'info' | 'primary' | 'warning'> = {
  default: 'info',
  user: 'primary',
  env: 'warning',
}

const route = useRoute()
const h265Supported = ref(false)
const saving = ref(false)
const settingsView = ref<SettingsView | null>(null)
const runtimeEnv = ref<RuntimeEnvView | null>(null)
const checkingUpdate = ref(false)
const updateResult = ref<ScrcpyUpdateResult | null>(null)
const streamForm = ref<StreamFormValue>({
  max_size: 1080,
  bit_rate: '4M',
  fps: 30,
  codec: 'h264',
  adaptive_bitrate: true,
})
const recordingForm = ref<RecordingFormValue>({ bit_rate: '4M', time_limit: 60 })
/** 设备级覆盖卡（方案 45 T8）：设备列表 / 当前选中设备 / 该设备串流覆盖表单。 */
const deviceList = ref<{ device_id: string }[]>([])
const overrideDeviceId = ref('')
const overrideStreamForm = ref<StreamFormValue>({
  max_size: streamForm.value.max_size,
  bit_rate: streamForm.value.bit_rate,
  fps: streamForm.value.fps,
  codec: streamForm.value.codec,
  adaptive_bitrate: streamForm.value.adaptive_bitrate,
})

/** 后端结构化错误（{"error":{code,message}}）取 message，缺失时回退文案。 */
function errorText(e: unknown, fallback: string): string {
  const err = e as { response?: { data?: { error?: { message?: string } } } }
  return err?.response?.data?.error?.message || fallback
}

/** 拉取合并视图回填两段表单与生效参数面板（保存/重置后同样调用以刷新来源）。 */
async function loadSettings() {
  const view = await api.getSettings()
  settingsView.value = view
  streamForm.value = {
    max_size: Number(view.stream.max_size.value),
    bit_rate: String(view.stream.bit_rate.value),
    fps: Number(view.stream.fps.value),
    codec: view.stream.codec.value as 'h264' | 'h265',
    adaptive_bitrate: Boolean(view.stream.adaptive_bitrate.value),
  }
  recordingForm.value = {
    bit_rate: String(view.recording.bit_rate.value),
    time_limit: Number(view.recording.time_limit.value),
  }
  syncOverrideForm() // 覆盖卡表单与最新合并视图对齐（选中设备已定但视图晚到时兜底）
}

/** 依选中设备把覆盖卡表单回填：有覆盖用覆盖值，未覆盖键回落全局表单值。 */
function syncOverrideForm() {
  const id = overrideDeviceId.value
  if (!id || !settingsView.value) return
  const over = settingsView.value.devices?.[id]?.stream
  overrideStreamForm.value = {
    max_size: over?.max_size !== undefined ? Number(over.max_size) : streamForm.value.max_size,
    bit_rate: over?.bit_rate !== undefined ? String(over.bit_rate) : streamForm.value.bit_rate,
    fps: over?.fps !== undefined ? Number(over.fps) : streamForm.value.fps,
    codec: (over?.codec as 'h264' | 'h265' | undefined) ?? streamForm.value.codec,
    adaptive_bitrate:
      over?.adaptive_bitrate !== undefined
        ? Boolean(over.adaptive_bitrate)
        : streamForm.value.adaptive_bitrate,
  }
}

async function saveStream() {
  saving.value = true
  try {
    await api.putSettings({ stream: { ...streamForm.value } })
    await loadSettings() // 刷新生效参数面板（保存后来源变为 用户设置）
    ElMessage.success('已保存，下次连接生效')
  } catch (e) {
    ElMessage.error(errorText(e, '保存失败，请重试'))
  } finally {
    saving.value = false
  }
}

async function saveRecording() {
  saving.value = true
  try {
    await api.putSettings({ recording: { ...recordingForm.value } })
    await loadSettings() // 刷新生效参数面板
    ElMessage.success('已保存')
  } catch (e) {
    ElMessage.error(errorText(e, '保存失败，请重试'))
  } finally {
    saving.value = false
  }
}

/** 重置一节：确认 → 节传 null（删除该节用户设置）→ 重拉回填。 */
async function resetSection(section: 'stream' | 'recording') {
  try {
    await ElMessageBox.confirm('将删除该节的用户设置并回到出厂默认？', '重置确认', {
      confirmButtonText: '重置',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch {
    return // 用户取消
  }
  saving.value = true
  try {
    await api.putSettings({ [section]: null })
    await loadSettings()
    ElMessage.success('已恢复出厂默认')
  } catch (e) {
    ElMessage.error(errorText(e, '重置失败，请重试'))
  } finally {
    saving.value = false
  }
}

function resetStream() {
  return resetSection('stream')
}

function resetRecording() {
  return resetSection('recording')
}

/** 保存选中设备的 stream 覆盖：devices.<id>.stream 节提交（后端键，语义「下次连接生效」）。 */
async function saveOverride() {
  const id = overrideDeviceId.value
  if (!id) return
  saving.value = true
  try {
    await api.putSettings({
      devices: { [id]: { stream: { ...overrideStreamForm.value } as Record<string, unknown> } },
    })
    await loadSettings() // 刷新视图与覆盖卡回填（保存后覆盖值即合并视图值）
    ElMessage.success('已保存，该设备下次连接生效')
  } catch (e) {
    ElMessage.error(errorText(e, '保存失败，请重试'))
  } finally {
    saving.value = false
  }
}

/** 清除选中设备的全部覆盖：devices.<id> 传 null = 删除该设备覆盖节。 */
async function clearOverride() {
  const id = overrideDeviceId.value
  if (!id) return
  try {
    await ElMessageBox.confirm(`将删除设备 ${id} 的全部参数覆盖并回到全局设置？`, '清除确认', {
      confirmButtonText: '清除',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch {
    return // 用户取消
  }
  saving.value = true
  try {
    await api.putSettings({ devices: { [id]: null } })
    await loadSettings()
    ElMessage.success('已清除该设备覆盖')
  } catch (e) {
    ElMessage.error(errorText(e, '清除失败，请重试'))
  } finally {
    saving.value = false
  }
}

// 选中设备变化 → 覆盖卡表单按该设备合并视图回填
watch(overrideDeviceId, () => {
  syncOverrideForm()
})

/** 检查 scrcpy-server 上游版本（方案 45 §4.2）：仅手动触发提示，不自动下载。 */
async function checkUpdate() {
  checkingUpdate.value = true
  try {
    const result = await api.checkScrcpyUpdate()
    updateResult.value = result
    if (result.status === 'unknown') ElMessage.warning(result.error ?? '检查更新失败')
  } catch (e) {
    ElMessage.error('检查更新失败')
  } finally {
    checkingUpdate.value = false
  }
}

onMounted(() => {
  // h265 支持面探测（T4）：不阻塞表单加载，结果到达后解禁选项
  isH265DecoderSupported().then((v) => {
    h265Supported.value = v
  })
  loadSettings().catch((e) => {
    ElMessage.error(errorText(e, '设置加载失败'))
  })
  // 设备级覆盖卡（T8）：拉取设备列表供选择；?device=<id> 查询串且在列表内 → 聚焦选中
  api
    .listDevices()
    .then((list) => {
      deviceList.value = Array.isArray(list) ? (list as { device_id: string }[]) : []
      const q = String(route.query.device ?? '')
      if (q && !overrideDeviceId.value && deviceList.value.some((d) => d.device_id === q)) {
        overrideDeviceId.value = q
      }
    })
    .catch(() => {})
  // 运行环境信息（T6）：只读展示，与设置拉取并行
  api
    .getRuntimeEnv()
    .then((v) => {
      runtimeEnv.value = v
    })
    .catch((e) => {
      ElMessage.error(errorText(e, '运行环境信息加载失败'))
    })
})
</script>

<style scoped>
.settings-page {
  padding: 1rem;
}

.section-card {
  max-width: 860px;
}

.section-row {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.actions {
  display: flex;
  gap: 8px;
}

.group-title {
  margin: 12px 0 8px;
}

.entry-value {
  margin-right: 8px;
}

.path-value {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
