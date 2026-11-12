<!--
  命令面板（Ctrl+K / Cmd+K）
  ==========================

  全局快捷命令入口。纯逻辑在 services/commandRegistry.ts，本组件只负责
  收集依赖（设备 store、路由、副作用）、渲染与键盘交互。

  交互：
    Ctrl/Cmd+K  开关面板
    Esc         关闭
    ↑ / ↓       移动选中项
    Enter       执行选中命令
    Tab         焦点锁定在面板内，关闭后归还唤起前元素（方案 58 E-11）
-->
<template>
  <Teleport to="body">
    <div v-if="open" class="cmd-overlay" @mousedown.self="close">
      <!-- 方案 58 E-11：补 dialog 语义与 Tab 焦点陷阱（面板内仅输入框可聚焦） -->
      <div
        class="cmd-panel"
        role="dialog"
        aria-modal="true"
        aria-label="命令面板"
        @keydown.tab.prevent="focusInput"
      >
        <input
          ref="inputRef"
          v-model="query"
          class="cmd-input"
          type="text"
          placeholder="输入命令…"
          @keydown.down.prevent="move(1)"
          @keydown.up.prevent="move(-1)"
          @keydown.enter.prevent="runActive"
          @keydown.esc.prevent="close"
        />
        <ul class="cmd-list">
          <li v-if="!filtered.length" class="cmd-empty">无匹配命令</li>
          <template v-for="(group, gi) in grouped" :key="gi">
            <li class="cmd-section">{{ group.section }}</li>
            <li
              v-for="item in group.items"
              :key="item.cmd.id"
              class="cmd-item"
              :class="{ active: item.index === activeIndex }"
              @mouseenter="activeIndex = item.index"
              @click="run(item.cmd)"
            >
              {{ item.cmd.title }}
            </li>
          </template>
        </ul>
        <div class="cmd-hint">↑↓ 选择 · Enter 执行 · Esc 关闭</div>
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useDeviceStore } from '@/stores/device'
import { buildCommands, filterCommands, type Command } from '@/services/commandRegistry'
// E-24/E-12 断开成败提示改用方案 58 E-17 共享助手（error.message 优先、detail 兼容）
import { errText } from '@/services/apiError'

const router = useRouter()
const route = useRoute()
const deviceStore = useDeviceStore()

const open = ref(false)
const query = ref('')
const activeIndex = ref(0)
const inputRef = ref<HTMLInputElement>()

// commands 仅在 show() 时整体重建；filtered 随 query/commands 响应式重算。
// 注意：必须用 ref——watch(filtered) 会在 setup 阶段立即求值一次，
// 若 commands 是非响应式的普通变量，首次打开面板时 filtered 会缓存空列表。
const commands = ref<Command[]>([])

const filtered = computed(() => filterCommands(query.value, commands.value))

const grouped = computed(() => {
  const out: { section: string; items: { cmd: Command; index: number }[] }[] = []
  const bySection = new Map<string, { cmd: Command; index: number }[]>()
  filtered.value.forEach((cmd, index) => {
    if (!bySection.has(cmd.section)) bySection.set(cmd.section, [])
    bySection.get(cmd.section)!.push({ cmd, index })
  })
  for (const [section, items] of bySection) out.push({ section, items })
  return out
})

watch(filtered, list => {
  if (activeIndex.value >= list.length) activeIndex.value = 0
})

function refreshCommands() {
  const currentDeviceId =
    route.name === 'device-detail' && route.params.id
      ? decodeURIComponent(String(route.params.id))
      : null
  commands.value = buildCommands({
    devices: deviceStore.devices,
    currentDeviceId,
    navigate: (path) => router.push(path),
    // 方案 58 E-24 裁决 a：面板「断开当前设备」与列表行内/批量同一确认口径；
    // E-12：await 完成并播报成败（旧实现即发即忘、失败静默）。
    disconnect: async (id) => {
      try {
        await ElMessageBox.confirm(
          `确定断开 ${id} 吗？`,
          '断开设备',
          { type: 'warning', confirmButtonText: '断开', cancelButtonText: '取消' }
        )
      } catch {
        return // 取消不断开
      }
      try {
        await deviceStore.disconnectDevice(id)
        ElMessage.success(`已断开 ${id}`)
      } catch (e) {
        ElMessage.error(`断开失败: ${errText(e, '未知错误')}`)
      }
    },
    setDebugTab: (tab) =>
      window.dispatchEvent(new CustomEvent('openscrcpy:set-debug-tab', { detail: tab })),
    // 方案 58 E-29：DebugPanel 仅挂载于设备详情页，非该页不注册切 Tab 命令（旧实现静默无效）
    debugAvailable: route.name === 'device-detail',
    toggleFullscreen: () => {
      if (document.fullscreenElement) document.exitFullscreen()
      else document.documentElement.requestFullscreen?.()
    },
  })
}

function onGlobalKey(e: KeyboardEvent) {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
    e.preventDefault()
    open.value ? close() : show()
  }
}

/** 方案 58 E-11：唤起面板前的焦点元素，关闭后归还。 */
let lastFocused: HTMLElement | null = null

function show() {
  lastFocused = document.activeElement instanceof HTMLElement ? document.activeElement : null
  refreshCommands()
  query.value = ''
  activeIndex.value = 0
  open.value = true
  nextTick(() => inputRef.value?.focus())
}

function close() {
  open.value = false
  // 方案 58 E-11：避免焦点随面板销毁掉回页面顶部
  lastFocused?.focus()
}

/** 方案 58 E-11：Tab 焦点陷阱——面板内仅输入框可聚焦，Tab 一律拉回输入框。 */
function focusInput() {
  inputRef.value?.focus()
}

function move(delta: number) {
  const n = filtered.value.length
  if (!n) return
  activeIndex.value = (activeIndex.value + delta + n) % n
}

function runActive() {
  const cmd = filtered.value[activeIndex.value]
  if (cmd) run(cmd)
}

function run(cmd: Command) {
  close()
  cmd.run()
}

onMounted(() => window.addEventListener('keydown', onGlobalKey))
onUnmounted(() => window.removeEventListener('keydown', onGlobalKey))
</script>

<style scoped>
.cmd-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.4);
  display: flex;
  justify-content: center;
  align-items: flex-start;
  padding-top: 12vh;
  z-index: 3000;
}
.cmd-panel {
  width: 520px;
  max-width: 90vw;
  background: #fff;
  border-radius: 8px;
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.25);
  overflow: hidden;
}
.cmd-input {
  width: 100%;
  border: none;
  outline: none;
  padding: 14px 16px;
  font-size: 15px;
  box-sizing: border-box;
  border-bottom: 1px solid #eee;
}
.cmd-list {
  list-style: none;
  margin: 0;
  padding: 6px 0;
  max-height: 50vh;
  overflow-y: auto;
}
.cmd-section {
  padding: 6px 16px 2px;
  font-size: 12px;
  color: #999;
}
.cmd-item {
  padding: 8px 16px;
  cursor: pointer;
  font-size: 14px;
}
.cmd-item.active {
  background: #eaf2ff;
}
.cmd-empty {
  padding: 16px;
  color: #999;
  font-size: 14px;
}
.cmd-hint {
  padding: 8px 16px;
  font-size: 12px;
  color: #bbb;
  border-top: 1px solid #f0f0f0;
}
</style>
