<!--
  参数说明手册页（方案 47 T6）：/settings/help，纯静态文档页（无 API）。
  数据单一来源 settingsHelpData.ts；分组渲染，锚点 id = `<组>-<键>`（anchorId）。
  智能返回范式（同 Settings 返回设备）：上一跳恰为 /settings（含 ?device=）
  时 back 不垫栈，否则 push /settings。hash 直达时挂载后滚动定位锚点。
-->
<template>
  <div class="help-page">
    <div class="page-header">
      <h2>参数说明手册</h2>
      <el-button size="small" data-test="back-settings" @click="onBackSettings">
        ← 返回设置
      </el-button>
    </div>

    <el-card v-for="g in HELP_GROUPS" :key="g.id" class="group-card" :data-test="`group-${g.id}`">
      <template #header>{{ g.title }}</template>
      <section v-for="p in paramsOf(g.id)" :id="anchorId(p)" :key="p.key" class="param-entry">
        <h3>{{ p.name }}</h3>
        <p class="meaning">{{ p.meaning }}</p>
        <dl v-if="p.larger || p.smaller" class="directions">
          <template v-if="p.larger">
            <dt>{{ p.largerLabel ?? '调大' }}</dt>
            <dd>
              <span class="effect">{{ p.larger.effect }}</span>
              <span class="scenario">适合：{{ p.larger.scenario }}</span>
            </dd>
          </template>
          <template v-if="p.smaller">
            <dt>{{ p.smallerLabel ?? '调小' }}</dt>
            <dd>
              <span class="effect">{{ p.smaller.effect }}</span>
              <span class="scenario">适合：{{ p.smaller.scenario }}</span>
            </dd>
          </template>
        </dl>
        <p v-if="p.range" class="meta">范围：{{ p.range }}</p>
        <p v-if="p.when_effective" class="meta">生效时机：{{ p.when_effective }}</p>
      </section>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { onMounted, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import {
  anchorId,
  HELP_GROUPS,
  PARAM_HELP,
  type HelpGroupId,
  type ParamHelp,
} from './settingsHelpData'

const route = useRoute()
const router = useRouter()

function paramsOf(group: HelpGroupId): ParamHelp[] {
  return PARAM_HELP.filter((p) => p.group === group)
}

/** 智能返回：上一跳恰为设置页（含 ?device=）时 back 不垫栈，否则 push。 */
function onBackSettings() {
  const back = window.history.state?.back as string | undefined
  if (back === '/settings' || back?.startsWith('/settings?')) {
    window.history.back()
  } else {
    router.push('/settings')
  }
}

/** hash 直达（如 /settings/help#stream-max_size）：渲染完成后滚动定位锚点。 */
onMounted(async () => {
  if (!route.hash) return
  await nextTick()
  document.getElementById(route.hash.slice(1))?.scrollIntoView({ block: 'start' })
})
</script>

<style scoped>
.help-page {
  padding: 1rem;
}

.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  max-width: 860px;
}

.group-card {
  max-width: 860px;
  margin-bottom: 1rem;
}

.param-entry {
  margin-bottom: 1.25rem;
}

.param-entry h3 {
  margin: 0 0 4px;
  font-size: 15px;
}

.meaning {
  margin: 0 0 8px;
  color: #303133;
}

.directions {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 4px 12px;
  margin: 0 0 8px;
}

.directions dt {
  font-weight: 600;
  color: #606266;
  white-space: nowrap;
}

.directions dd {
  margin: 0;
  display: flex;
  flex-direction: column;
}

.directions .scenario {
  color: #909399;
  font-size: 12px;
}

.meta {
  margin: 2px 0;
  color: #909399;
  font-size: 12px;
}
</style>
