<!--
  参数行「?」手册直达链接（方案 47 T6）：
  指向 /settings/help#<group>-<paramKey> 锚点（anchorId 约定见 settingsHelpData.ts）。
-->
<template>
  <router-link
    class="help-link"
    :data-test="`help-${anchor}`"
    :title="`参数说明手册：${anchor}`"
    :to="`/settings/help#${anchor}`"
  >?</router-link>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { HelpGroupId } from '@/views/settingsHelpData'

const props = defineProps<{
  group: HelpGroupId
  paramKey: string
}>()

const anchor = computed(() => `${props.group}-${props.paramKey}`)
</script>

<style scoped>
.help-link {
  display: inline-block;
  margin-left: 6px;
  width: 16px;
  height: 16px;
  line-height: 15px;
  text-align: center;
  border-radius: 50%;
  border: 1px solid #909399;
  color: #909399;
  font-size: 12px;
  text-decoration: none;
}

.help-link:hover {
  border-color: var(--el-color-primary);
  color: var(--el-color-primary);
}
</style>
