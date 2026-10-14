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
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import RecordingFieldSet, {
  type RecordingFormValue,
} from '@/components/settings/RecordingFieldSet.vue'
import StreamFieldSet, { type StreamFormValue } from '@/components/settings/StreamFieldSet.vue'
import { api } from '@/services/api'
import { isH265DecoderSupported } from '@/services/codecSupport'

const h265Supported = ref(false)
const saving = ref(false)
const streamForm = ref<StreamFormValue>({
  max_size: 1080,
  bit_rate: '4M',
  fps: 30,
  codec: 'h264',
  adaptive_bitrate: true,
})
const recordingForm = ref<RecordingFormValue>({ bit_rate: '4M', time_limit: 60 })

/** 后端结构化错误（{"error":{code,message}}）取 message，缺失时回退文案。 */
function errorText(e: unknown, fallback: string): string {
  const err = e as { response?: { data?: { error?: { message?: string } } } }
  return err?.response?.data?.error?.message || fallback
}

/** 拉取合并视图回填两段表单（保存/重置后同样调用以刷新来源）。 */
async function loadSettings() {
  const view = await api.getSettings()
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
}

async function saveStream() {
  saving.value = true
  try {
    await api.putSettings({ stream: { ...streamForm.value } })
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

onMounted(() => {
  // h265 支持面探测（T4）：不阻塞表单加载，结果到达后解禁选项
  isH265DecoderSupported().then((v) => {
    h265Supported.value = v
  })
  loadSettings().catch((e) => {
    ElMessage.error(errorText(e, '设置加载失败'))
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
</style>
