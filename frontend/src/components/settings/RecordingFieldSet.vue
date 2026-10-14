<!--
  录屏参数表单段（方案 45 T5；与 StreamFieldSet 同构的 v-model 表单）。
  props: modelValue（录屏段值，键名直接用后端键——控制器裁定 R4）
  提交由父组件收集 —— 本组件只维护表单值。
  保存语义：录屏段立即生效（spec §5.3）——每次开始录屏时读取当前值。
-->
<template>
  <el-form label-width="140px" @submit.prevent>
    <el-form-item label="录屏码率 bit_rate">
      <el-select v-model="value.bit_rate">
        <el-option v-for="v in BIT_RATES" :key="v" :label="v" :value="v" />
      </el-select>
    </el-form-item>
    <el-form-item label="时长上限 time_limit">
      <el-input-number v-model="value.time_limit" :min="1" :max="1800" />
      <span class="hint">秒；每次开始录屏时读取，保存即对新录屏生效</span>
    </el-form-item>
  </el-form>
</template>

<script lang="ts">
/**
 * 录屏段表单值（控制器裁定 R4：字段名与后端键一致）。
 * `<script setup>` 不允许 export（裁定 R3），故类型放独立普通 script 块。
 */
export interface RecordingFormValue {
  bit_rate: string
  time_limit: number
}
</script>

<script setup lang="ts">
import { computed } from 'vue'

/** 码率档位（与后端 RecordingOverride bit_rate 的 ^\d+[MK]$ 约束兼容的常用档） */
const BIT_RATES = ['1M', '2M', '4M', '6M', '8M']

const props = defineProps<{
  modelValue: RecordingFormValue
}>()
const emit = defineEmits<{ 'update:modelValue': [value: RecordingFormValue] }>()

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
