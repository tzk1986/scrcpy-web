<!--
  设备文件管理独立页（方案 39 T5）
  ==================================

  路由：/device/:id/files（props: id）

  职责：
    - 浏览：listFiles 列当前目录（默认 /sdcard，读全盘），面包屑逐级导航
    - 下载：勾选 → downloadFiles（单=原文件、多=zip），blob + a.download 保存
    - 上传：隐藏 file input，多文件顺序单飞（设备互斥语义），完成刷新目录
    - 重命名：单条目弹窗改（前端先校验非法名，后端为准）
    - 删除：多选 ElMessageBox.confirm 纯文本强确认（目录含递归文案），逐条汇总
    - 两段诚实进度：HTTP 段真实百分比（el-progress），adb 段条纹动画不伪造
    - 错误态：403「该路径不可访问」；404「目录不存在」+ 重试；离线整页 alert 禁用
-->
<template>
  <div class="fm">
    <el-alert
      v-if="offline"
      class="fm-offline"
      type="warning"
      :closable="false"
      title="设备离线，无法管理文件"
    />

    <div class="fm-toolbar">
      <h3 class="fm-title">{{ id }}</h3>
      <el-button class="btn-refresh" @click="loadList">刷新</el-button>
      <el-button class="btn-back" @click="goBack">返回设备</el-button>
      <el-button class="btn-upload" :disabled="offline || transferring" @click="pickFiles">
        上传
      </el-button>
      <input
        ref="uploadInputRef"
        type="file"
        class="fm-upload-input"
        multiple
        :disabled="offline"
        @change="onUploadChange"
      />
      <el-button class="btn-download" :disabled="offline || !selected.length" @click="doDownload">
        下载
      </el-button>
      <el-button class="btn-rename" :disabled="offline || selected.length !== 1" @click="openRename">
        重命名
      </el-button>
      <el-button
        class="btn-delete"
        type="danger"
        plain
        :disabled="offline || !selected.length"
        @click="doDelete"
      >
        删除
      </el-button>
    </div>

    <div class="fm-breadcrumb">
      <span
        v-for="c in crumbs"
        :key="c.path + c.label"
        class="crumb"
        @click="cd(c.path)"
      >{{ c.label }}</span>
      <span class="fm-path">{{ currentPath }}</span>
    </div>

    <div
      v-for="t in transfers"
      :key="t.id"
      class="transfer-row"
      :class="`phase-${t.phase}`"
    >
      <span class="transfer-name">{{ t.label }}</span>
      <div v-if="t.phase === 'adb'" class="stripe-bar" />
      <el-progress v-if="t.phase === 'http'" :percentage="t.percent" class="transfer-progress" />
      <span class="transfer-text">{{ t.text }}</span>
      <span v-if="t.error" class="transfer-error">{{ t.error }}</span>
    </div>

    <div v-if="errorText" class="fm-error">
      <span class="fm-error-text">{{ errorText }}</span>
      <el-button v-if="errorRetryable" class="btn-retry" @click="loadList">重试</el-button>
    </div>

    <el-empty v-else-if="entries.length === 0 && !transfers.length" description="目录为空" />

    <el-table v-else ref="tableRef" :data="entries" class="fm-table" @selection-change="onSelChange">
      <el-table-column type="selection" width="44" />
      <el-table-column label="名称" min-width="220">
        <template #default="{ row }">
          <span class="file-name" :class="{ 'is-dir': row.is_dir }" @click="onNameClick(row)">
            {{ row.name }}
          </span>
          <span v-if="row.is_symlink && row.link_target" class="link-target">→ {{ row.link_target }}</span>
        </template>
      </el-table-column>
      <el-table-column label="大小" width="110">
        <template #default="{ row }">{{ formatBytes(row.size) }}</template>
      </el-table-column>
      <el-table-column label="修改时间" prop="mtime" width="150" />
      <el-table-column label="权限" prop="permissions" width="120" />
    </el-table>

    <el-dialog v-model="renameVisible" title="重命名" width="420px">
      <el-input v-model="renameValue" class="rename-input" placeholder="新名称" />
      <div v-if="renameError" class="rename-error">{{ renameError }}</div>
      <template #footer>
        <el-button @click="renameVisible = false">取消</el-button>
        <el-button type="primary" class="rename-save" @click="saveRename">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api, type FileEntry } from '@/services/api'

const props = defineProps<{ id: string }>()
const id = props.id

/** 传输进度行（两段诚实进度：http 真实百分比 / adb 条纹不伪造） */
interface TransferRow {
  id: number
  kind: 'upload' | 'download'
  label: string
  phase: 'http' | 'adb' | 'done' | 'failed'
  percent: number
  text: string
  error: string | null
}

/** axios 错误体 → {code,message}（后端 {error:{code,message}} 约定） */
function errOf(e: unknown): { code: string; message: string } {
  const resp = (e as { response?: { data?: { error?: { code?: string; message?: string } } } })
    ?.response
  const code = resp?.data?.error?.code ?? ''
  const message =
    resp?.data?.error?.message ?? (e instanceof Error ? e.message : String(e))
  return { code, message }
}

const offline = ref(false)
const currentPath = ref('/sdcard')
const entries = ref<FileEntry[]>([])
const selected = ref<FileEntry[]>([])
const errorText = ref('')
const errorRetryable = ref(false)
const transfers = reactive<TransferRow[]>([])
const tableRef = ref<{ clearSelection: () => void } | null>(null)
const uploadInputRef = ref<HTMLInputElement | null>(null)
const transferring = computed(() => transfers.some((t) => t.phase === 'http' || t.phase === 'adb'))

const renameVisible = ref(false)
const renameValue = ref('')
const renameError = ref('')
const renameTarget = ref<FileEntry | null>(null)

let transferSeq = 0

function joinPath(dir: string, name: string): string {
  return dir.endsWith('/') ? dir + name : `${dir}/${name}`
}

/** 1536 → "1.5 KB"；0-1023 → "N B" */
function formatBytes(n: number): string {
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let v = n
  let i = 0
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i += 1
  }
  return i === 0 ? `${v} B` : `${v.toFixed(1)} ${units[i]}`
}

const crumbs = computed<{ label: string; path: string }[]>(() => {
  const out: { label: string; path: string }[] = []
  if (currentPath.value !== '/') {
    const idx = currentPath.value.lastIndexOf('/')
    out.push({ label: '..', path: idx <= 0 ? '/' : currentPath.value.slice(0, idx) })
  }
  const segs = currentPath.value.split('/').filter(Boolean)
  let acc = ''
  for (const s of segs) {
    acc += `/${s}`
    out.push({ label: s, path: acc })
  }
  return out
})

async function loadList(): Promise<void> {
  errorText.value = ''
  try {
    entries.value = await api.listFiles(id, currentPath.value)
  } catch (e) {
    const { code, message } = errOf(e)
    entries.value = []
    if (code === 'PERMISSION_DENIED') {
      errorText.value = '该路径不可访问'
      errorRetryable.value = false
    } else if (code === 'PATH_NOT_FOUND') {
      errorText.value = '目录不存在'
      errorRetryable.value = true
    } else {
      errorText.value = `加载失败：${message}`
      errorRetryable.value = true
    }
  }
}

async function cd(path: string): Promise<void> {
  currentPath.value = path
  await loadList()
}

function onNameClick(row: FileEntry): void {
  if (row.is_dir && !row.is_symlink) void cd(joinPath(currentPath.value, row.name))
}

function onSelChange(rows: FileEntry[]): void {
  selected.value = rows
}

function goBack(): void {
  window.history.back()
}

function newTransfer(kind: TransferRow['kind'], label: string): TransferRow {
  // reactive 包装后再入数组：后续经回调改 phase/percent 才能触发进度行更新
  // （裸对象 push 进 reactive 数组后改原引用属性不走代理，进度态不刷新）
  const t = reactive<TransferRow>({
    id: ++transferSeq,
    kind,
    label,
    phase: 'adb',
    percent: 0,
    text: '',
    error: null,
  })
  transfers.push(t)
  return t
}

async function doDownload(): Promise<void> {
  const paths = selected.value.map((r) => joinPath(currentPath.value, r.name))
  if (!paths.length) return
  const multi = paths.length > 1
  const t = newTransfer('download', multi ? `${paths[0]} 等 ${paths.length} 项` : paths[0])
  t.text = multi ? '打包中…' : '拉取中…'
  try {
    const { blob } = await api.downloadFiles(id, paths, (pct) => {
      t.phase = 'http'
      t.percent = pct
      t.text = `下载中 ${pct}%`
    })
    const name = multi ? `${selected.value[0].name}_etc.zip` : selected.value[0].name
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = name
    a.click()
    URL.revokeObjectURL(url)
    t.phase = 'done'
    t.text = '已完成'
  } catch (e) {
    const { message } = errOf(e)
    t.phase = 'failed'
    t.error = message
    ElMessage.error(message)
  }
}

function pickFiles(): void {
  uploadInputRef.value?.click()
}

async function onUploadChange(e: Event): Promise<void> {
  const input = e.target as HTMLInputElement
  const files = Array.from(input.files ?? [])
  input.value = '' // 允许重复选择同名文件再传
  if (!files.length) return
  for (const f of files) {
    const t = newTransfer('upload', f.name)
    t.text = '上传中 0%'
    try {
      const job = await api.uploadFile(id, currentPath.value, f, (pct) => {
        t.percent = pct
        if (pct >= 100) {
          t.phase = 'adb'
          t.text = '推入设备中…'
        } else {
          t.phase = 'http'
          t.text = `上传中 ${pct}%`
        }
      })
      if (job.status === 'failed') {
        t.phase = 'failed'
        t.error = job.error
      } else {
        t.phase = 'done'
        t.text = '已完成'
      }
    } catch (err) {
      const { code, message } = errOf(err)
      t.phase = 'failed'
      t.error = message
      if (code === 'TRANSFER_BUSY') ElMessage.warning(message)
      else ElMessage.error(message)
      break // 互斥语义：失败后队列后续文件不再发起
    }
  }
  await loadList()
}

function openRename(): void {
  if (selected.value.length !== 1) return
  renameTarget.value = selected.value[0]
  renameValue.value = selected.value[0].name
  renameError.value = ''
  renameVisible.value = true
}

async function saveRename(): Promise<void> {
  const target = renameTarget.value
  if (!target) return
  const n = renameValue.value
  if (!n || n.includes('/') || n.includes('\x00') || n === '.' || n === '..') {
    renameError.value = '名称非法：不能为空、含 /、NUL 或 . / ..'
    return
  }
  try {
    await api.renameFile(id, joinPath(currentPath.value, target.name), n)
    renameVisible.value = false
    ElMessage.success('重命名成功')
    await loadList()
  } catch (e) {
    const { message } = errOf(e)
    ElMessage.error(message)
  }
}

async function doDelete(): Promise<void> {
  const rows = selected.value
  if (!rows.length) return
  const paths = rows.map((r) => joinPath(currentPath.value, r.name))
  const hasDir = rows.some((r) => r.is_dir)
  const names = rows.map((r) => r.name).join('、')
  const message = hasDir
    ? `将删除 ${rows.length} 项：${names}。其中含目录，将递归删除目录及其全部内容，不可恢复。确认？`
    : `将删除 ${rows.length} 项：${names}。不可恢复，确认？`
  try {
    // 纯文本 message（禁 dangerouslyUseHTMLString），设备端文件名不可信防 XSS
    await ElMessageBox.confirm(message, '删除确认', { type: 'warning' })
  } catch {
    return
  }
  try {
    const results = await api.deleteFiles(id, paths)
    const okN = results.filter((r) => r.ok).length
    const failN = results.length - okN
    if (failN) ElMessage.warning(`成功 ${okN} / 失败 ${failN}`)
    else ElMessage.success(`已删除 ${okN} 项`)
  } catch (e) {
    ElMessage.error(errOf(e).message)
  }
  tableRef.value?.clearSelection()
  await loadList()
}

;(async () => {
  try {
    const dev = await api.getDevice(id)
    if (dev?.status !== 'online') {
      offline.value = true
      return
    }
  } catch {
    offline.value = true
    return
  }
  await loadList()
})()
</script>

<style scoped>
.fm {
  padding: 12px 16px;
}
.fm-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 8px;
}
.fm-title {
  margin: 0 8px 0 0;
  font-size: 15px;
}
.fm-upload-input {
  display: none;
}
.fm-breadcrumb {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-bottom: 8px;
  font-size: 13px;
}
.crumb {
  color: #409eff;
  cursor: pointer;
  user-select: none;
}
.crumb:hover {
  text-decoration: underline;
}
.fm-path {
  margin-left: 8px;
  color: #909399;
}
.transfer-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 8px;
  border: 1px solid #ebeef5;
  border-radius: 4px;
  margin-bottom: 6px;
  font-size: 13px;
}
.transfer-row.phase-failed {
  border-color: #f56c6c;
}
.transfer-row.phase-done {
  border-color: #67c23a;
}
.transfer-name {
  max-width: 320px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.transfer-progress {
  width: 180px;
}
.transfer-error {
  color: #f56c6c;
}
/* adb 段条纹动画：不确定态，不伪造百分比 */
.stripe-bar {
  width: 120px;
  height: 10px;
  border-radius: 5px;
  background: repeating-linear-gradient(
    45deg,
    #a0cfff 0 10px,
    #79bbff 10px 20px
  );
  background-size: 20px 20px;
  animation: stripe-move 0.8s linear infinite;
}
@keyframes stripe-move {
  from {
    background-position: 0 0;
  }
  to {
    background-position: 20px 0;
  }
}
.fm-error {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 10px 0;
  color: #f56c6c;
  font-size: 13px;
}
.file-name {
  cursor: pointer;
  color: #409eff;
}
.file-name.is-dir {
  font-weight: 600;
}
.link-target {
  margin-left: 6px;
  color: #909399;
  font-size: 12px;
}
</style>
