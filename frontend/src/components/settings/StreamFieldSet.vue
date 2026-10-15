<!--
  串流参数表单段（方案 45 T5；T8 设备级覆盖复用）。
  props: modelValue（串流段值，键名直接用后端键——控制器裁定 R4，免映射层）；
         h265Supported（codec h265 是否可选项，T4 探测结果，评审焦点 #4）
  提交由父组件收集 —— 本组件只维护表单值（v-model 对象语义，字段变更直接
  写入父组件持有的响应式对象）。
-->
<template>
  <el-form label-width="140px" @submit.prevent>
    <el-form-item>
      <template #label>最大分辨率 max_size <HelpLink group="stream" param-key="max_size" /></template>
      <el-input-number v-model="value.max_size" :min="0" :max="4096" />
      <span class="hint">0 表示不缩放（服务端截图链路恒不缩放，仅影响视频流）</span>
    </el-form-item>
    <el-form-item>
      <template #label>起始码率 bit_rate <HelpLink group="stream" param-key="bit_rate" /></template>
      <el-select v-model="value.bit_rate">
        <el-option v-for="v in BIT_RATES" :key="v" :label="v" :value="v" />
      </el-select>
      <span class="hint">自适应码率开启时的起始档</span>
    </el-form-item>
    <el-form-item>
      <template #label>帧率 fps <HelpLink group="stream" param-key="fps" /></template>
      <el-input-number v-model="value.fps" :min="1" :max="120" />
    </el-form-item>
    <el-form-item>
      <template #label>编解码 codec <HelpLink group="stream" param-key="codec" /></template>
      <el-select v-model="value.codec">
        <el-option label="h264 (AVC)" value="h264" />
        <el-option label="h265 (HEVC)" value="h265" :disabled="!h265Supported" />
      </el-select>
      <span v-if="!h265Supported" class="hint">当前浏览器不支持 h265 解码，已禁用</span>
    </el-form-item>
    <el-form-item>
      <template #label>自适应码率 <HelpLink group="stream" param-key="adaptive_bitrate" /></template>
      <el-switch v-model="value.adaptive_bitrate" />
    </el-form-item>
  </el-form>
</template>

<script lang="ts">
/**
 * 串流段表单值（控制器裁定 R4：字段名与后端键一致，保存 patch 直接展开提交）。
 * `<script setup>` 不允许 export（裁定 R3），故类型放独立普通 script 块。
 */
export interface StreamFormValue {
  max_size: number
  bit_rate: string
  fps: number
  codec: 'h264' | 'h265'
  adaptive_bitrate: boolean
}
</script>

<script setup lang="ts">
import { computed } from 'vue'
import HelpLink from './HelpLink.vue'

/** 码率档位（与后端 StreamOverride bit_rate 的 ^\d+[MK]$ 约束兼容的常用档） */
const BIT_RATES = ['1M', '2M', '4M', '6M', '8M']

const props = withDefaults(
  defineProps<{
    modelValue: StreamFormValue
    h265Supported?: boolean
  }>(),
  { h265Supported: false }
)
const emit = defineEmits<{ 'update:modelValue': [value: StreamFormValue] }>()

// v-model 对象：模板内对各字段直接读写；整体替换（父组件加载回填）时经 set 上抛
const value = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})
</script>

<style scoped>
.hint {
  margin-left: 8px;
  color: #909399;
  font-size: 12px;
}
</style>
