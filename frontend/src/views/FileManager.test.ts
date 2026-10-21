/**
 * FileManager 组件测试（方案 39 T5）
 * ====================================
 *
 * 覆盖：
 *   - 挂载：getDevice 在线 → listFiles('/sdcard')；行渲染名称与人类可读大小（1536→1.5 KB）
 *   - 目录行名点击进入子目录；面包屑呈现 `..` 与逐级；点「上一级」回父路径
 *   - 下载：勾选两行 → downloadFiles(id, paths, fn)；进度事件 adb→http 两段；
 *     blob 经 URL.createObjectURL + a.download 保存（多选名 xxx_etc.zip）
 *   - 上传：input change 两文件 → 顺序单飞 uploadFile；进度行 http→adb→done 文案状态机
 *   - 409 TRANSFER_BUSY → ElMessage.warning + 行 failed
 *   - 重命名：含 `/` 前端校验拒（api 未被调）+ 报错文案；合法 → renameFile + 重刷当前目录
 *   - 删除：ElMessageBox.confirm 纯文本（无 dangerouslyUseHTMLString）；目录含递归强确认文案；
 *     results 汇总「成功 1 / 失败 1」
 *   - 403/404 错误态（404 带重试）与空目录 el-empty
 *   - offline：getDevice 状态非 online → el-alert 显示、操作按钮禁用、不拉列表
 *
 * 说明：Element Plus 组件以轻量 stub 替代（沿用 Dashboard.test 的
 * ElTableStub/ElTableColumnStub provide/inject 范式，行加 data-name 定位）。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, shallowMount, type VueWrapper } from '@vue/test-utils'
import { defineComponent, h as vueH, inject, nextTick, provide } from 'vue'

const elMock = vi.hoisted(() => ({
  ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
  ElMessageBox: { confirm: vi.fn() },
}))
vi.mock('element-plus', () => ({
  ElMessage: elMock.ElMessage,
  ElMessageBox: elMock.ElMessageBox,
}))

const mockApi = vi.hoisted(() => ({
  getDevice: vi.fn(),
  listFiles: vi.fn(),
  downloadFiles: vi.fn(),
  uploadFile: vi.fn(),
  renameFile: vi.fn(),
  deleteFiles: vi.fn(),
  getFileJob: vi.fn(),
}))
vi.mock('@/services/api', () => ({ api: mockApi }))

// happy-dom 无 createObjectURL / revokeObjectURL 实现（下载保存链路需要）
const urlStub = vi.hoisted(() => ({
  createObjectURL: vi.fn(),
  revokeObjectURL: vi.fn(),
}))

import FileManager from './FileManager.vue'

const fileA = {
  name: 'a.txt',
  is_dir: false,
  is_symlink: false,
  size: 1536,
  mtime: '2026-10-12 09:12',
  permissions: '-rw-r--r--',
  link_target: null,
}
const fileB = {
  name: 'b.txt',
  is_dir: false,
  is_symlink: false,
  size: 2048,
  mtime: '2026-10-12 09:13',
  permissions: '-rw-r--r--',
  link_target: null,
}
const dirD = {
  name: 'Download',
  is_dir: true,
  is_symlink: false,
  size: 3452,
  mtime: '2026-10-12 09:14',
  permissions: 'drwxr-x---',
  link_target: null,
}

function uploadJob(overrides: Record<string, unknown> = {}) {
  return {
    id: 'job-1',
    device_id: 'dev1',
    direction: 'upload',
    paths: ['/sdcard'],
    total_size: 3,
    status: 'done',
    substage: null,
    error: null,
    ...overrides,
  }
}

const ElButtonStub = {
  name: 'ElButton',
  template: '<button class="stub-btn" type="button"><slot /></button>',
}

const ElDialogStub = {
  name: 'ElDialog',
  props: ['modelValue', 'title'],
  emits: ['update:modelValue'],
  template:
    '<div v-if="modelValue" class="stub-dialog"><div class="stub-dialog-title">{{ title }}</div><slot /><slot name="footer" /></div>',
}

const ElInputStub = {
  name: 'ElInput',
  props: ['modelValue'],
  emits: ['update:modelValue'],
  template:
    '<input class="stub-input" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
}

const ElAlertStub = {
  name: 'ElAlert',
  props: ['title', 'type'],
  template: '<div class="stub-alert" :data-type="type"><div class="stub-alert-title">{{ title }}</div><slot /></div>',
}

const ElEmptyStub = {
  name: 'ElEmpty',
  props: ['description'],
  template: '<div class="stub-empty">{{ description }}</div>',
}

const ElProgressStub = {
  name: 'ElProgress',
  props: ['percentage'],
  template: '<div class="stub-progress" :data-percentage="percentage"></div>',
}

const ElTableStub = defineComponent({
  name: 'ElTable',
  props: { data: { type: Array, default: () => [] } },
  emits: ['selection-change'],
  setup(props, { slots, expose }) {
    provide('tableData', () => props.data as Array<Record<string, unknown>>)
    const clearSelection = vi.fn()
    expose({ clearSelection })
    return () => vueH('div', { class: 'stub-table' }, slots.default?.())
  },
})

const ElTableColumnStub = defineComponent({
  name: 'ElTableColumn',
  setup(_props, { slots }) {
    const tableData = inject<(() => Array<Record<string, unknown>>) | undefined>('tableData')
    return () => {
      const slot = slots.default
      if (!slot || !tableData) return vueH('div', { class: 'stub-column stub-no-slot' })
      return vueH(
        'div',
        { class: 'stub-column' },
        tableData().map((row) =>
          vueH('div', { class: 'stub-row', 'data-name': String(row.name ?? '') }, slot({ row }))
        )
      )
    }
  },
})

async function mountView(): Promise<VueWrapper> {
  const wrapper = shallowMount(FileManager, {
    props: { id: 'dev1' },
    global: {
      stubs: {
        'el-button': ElButtonStub,
        'el-dialog': ElDialogStub,
        'el-input': ElInputStub,
        'el-alert': ElAlertStub,
        'el-empty': ElEmptyStub,
        'el-progress': ElProgressStub,
        'el-table': ElTableStub,
        'el-table-column': ElTableColumnStub,
        'el-icon': true,
      },
    },
  })
  await flushPromises()
  return wrapper
}

/** 在 .stub-row[data-name] 集合中找包含指定文本的行（多列各自成行） */
function rowWithText(wrapper: VueWrapper, name: string, text: string) {
  return wrapper
    .findAll(`.stub-row[data-name="${name}"]`)
    .find((r) => r.text().includes(text))
}

/** 通过定义 files 属性模拟 input[type=file] 选择文件 */
async function setUploadFiles(wrapper: VueWrapper, files: File[]) {
  const input = wrapper.find('.fm-upload-input')
  Object.defineProperty(input.element, 'files', { value: files, configurable: true })
  await input.trigger('change')
  await flushPromises()
}

/** 记录 a 标签下载（href + download 名）：blob 下载范式断言 */
let downloadedAnchors: { href: string; download: string }[] = []
vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
  this: HTMLAnchorElement,
) {
  downloadedAnchors.push({ href: this.href, download: this.download })
})

beforeEach(() => {
  downloadedAnchors = []
  mockApi.getDevice.mockReset().mockResolvedValue({ id: 'dev1', status: 'online' })
  mockApi.listFiles.mockReset().mockResolvedValue([fileA, fileB, dirD])
  mockApi.downloadFiles.mockReset()
  mockApi.uploadFile.mockReset()
  mockApi.renameFile.mockReset().mockResolvedValue(undefined)
  mockApi.deleteFiles.mockReset()
  mockApi.getFileJob.mockReset()
  elMock.ElMessage.success.mockClear()
  elMock.ElMessage.error.mockClear()
  elMock.ElMessage.warning.mockClear()
  elMock.ElMessage.info.mockClear()
  elMock.ElMessageBox.confirm.mockReset().mockResolvedValue('confirm')
  urlStub.createObjectURL.mockReset().mockReturnValue('blob:fm-dl')
  urlStub.revokeObjectURL.mockReset()
  Object.defineProperty(URL, 'createObjectURL', {
    configurable: true,
    value: urlStub.createObjectURL,
  })
  Object.defineProperty(URL, 'revokeObjectURL', {
    configurable: true,
    value: urlStub.revokeObjectURL,
  })
})

describe('FileManager', () => {
  it('挂载拉取在线设备并列出 /sdcard，行渲染名称与人类可读大小', async () => {
    const wrapper = await mountView()

    expect(mockApi.getDevice).toHaveBeenCalledWith('dev1')
    expect(mockApi.listFiles).toHaveBeenCalledWith('dev1', '/sdcard')

    expect(rowWithText(wrapper, 'a.txt', 'a.txt')).toBeTruthy()
    expect(rowWithText(wrapper, 'a.txt', '1.5 KB')).toBeTruthy()

    wrapper.unmount()
  })

  it('A→B 直跳（props.id 变更）：整页重核设备并重列 /sdcard（方案 51 F2-15）', async () => {
    const wrapper = await mountView()
    expect(mockApi.listFiles).toHaveBeenCalledTimes(1)

    await wrapper.setProps({ id: 'dev2' })
    await flushPromises()

    expect(mockApi.getDevice).toHaveBeenLastCalledWith('dev2')
    // 路径复位 /sdcard，列表按新设备重拉
    expect(mockApi.listFiles).toHaveBeenLastCalledWith('dev2', '/sdcard')
    expect(mockApi.listFiles).toHaveBeenCalledTimes(2)

    wrapper.unmount()
  })

  it('id 变更且新设备离线：显示离线 alert 且不拉列表', async () => {
    const wrapper = await mountView()
    mockApi.getDevice.mockResolvedValue({ id: 'dev2', status: 'offline' })

    await wrapper.setProps({ id: 'dev2' })
    await flushPromises()

    expect(wrapper.find('.fm-offline').exists()).toBe(true)
    expect(mockApi.listFiles).toHaveBeenCalledTimes(1) // 仅首挂那次，切换不拉列表

    wrapper.unmount()
  })

  it('点击目录名进入子目录，面包屑出现 .. 与逐级节点', async () => {
    const wrapper = await mountView()

    const dirName = wrapper.find(`.stub-row[data-name="Download"] .file-name`)
    await dirName.trigger('click')
    await flushPromises()

    expect(mockApi.listFiles).toHaveBeenCalledWith('dev1', '/sdcard/Download')
    const crumbs = wrapper.findAll('.crumb').map((c) => c.text())
    expect(crumbs).toContain('..')
    expect(crumbs).toContain('sdcard')
    expect(crumbs).toContain('Download')

    wrapper.unmount()
  })

  it('面包屑上一级 .. 回到父路径', async () => {
    const wrapper = await mountView()
    await wrapper.find(`.stub-row[data-name="Download"] .file-name`).trigger('click')
    await flushPromises()

    const up = wrapper.findAll('.crumb').find((c) => c.text() === '..')
    expect(up).toBeDefined()
    await up!.trigger('click')
    await flushPromises()

    expect(mockApi.listFiles).toHaveBeenLastCalledWith('dev1', '/sdcard')
    wrapper.unmount()
  })

  it('点文件行名不导航（仍停留当前目录）', async () => {
    const wrapper = await mountView()
    await wrapper.find(`.stub-row[data-name="a.txt"] .file-name`).trigger('click')
    await flushPromises()
    expect(mockApi.listFiles).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it('多选下载：adb 条纹段 → http 百分比段 → blob 保存 xxx_etc.zip 并刷新', async () => {
    let onHttpProgress!: (pct: number) => void
    let resolveDl!: (v: { blob: Blob; jobId: string | null }) => void
    mockApi.downloadFiles.mockImplementation(
      (_id: string, _paths: string[], cb: (pct: number) => void) => {
        onHttpProgress = cb
        return new Promise((res) => {
          resolveDl = res
        })
      }
    )
    const wrapper = await mountView()

    wrapper
      .findComponent({ name: 'ElTable' })
      .vm.$emit('selection-change', [fileA, fileB])
    await nextTick()

    await wrapper.find('.btn-download').trigger('click')
    await flushPromises()

    expect(mockApi.downloadFiles).toHaveBeenCalledWith(
      'dev1',
      ['/sdcard/a.txt', '/sdcard/b.txt'],
      expect.any(Function)
    )
    // adb 段：条纹动画 div + 「打包中」文案（不伪造百分比）
    expect(wrapper.find('.phase-adb .stripe-bar').exists()).toBe(true)
    expect(wrapper.find('.transfer-row').text()).toContain('打包中')

    // HTTP 段：真实百分比
    onHttpProgress(40)
    await nextTick()
    const prog = wrapper.find('.phase-http .stub-progress')
    expect(prog.exists()).toBe(true)
    expect(prog.attributes('data-percentage')).toBe('40')

    resolveDl({ blob: new Blob(['zip']), jobId: 'job-1' })
    await flushPromises()

    expect(downloadedAnchors).toHaveLength(1)
    expect(downloadedAnchors[0].download).toBe('a.txt_etc.zip')
    expect(downloadedAnchors[0].href).toBe('blob:fm-dl')
    expect(wrapper.find('.phase-done').exists()).toBe(true)

    wrapper.unmount()
  })

  it('单选下载：拉取中文案与以原文件名保存', async () => {
    mockApi.downloadFiles.mockResolvedValue({ blob: new Blob(['x']), jobId: null })
    const wrapper = await mountView()

    wrapper.findComponent({ name: 'ElTable' }).vm.$emit('selection-change', [fileA])
    await nextTick()
    await wrapper.find('.btn-download').trigger('click')
    await flushPromises()

    expect(downloadedAnchors).toHaveLength(1)
    expect(downloadedAnchors[0].download).toBe('a.txt')
    wrapper.unmount()
  })

  it('上传两文件：顺序单飞（互斥语义），进度行 http→adb→done 文案状态机，完成后刷新目录', async () => {
    const uploadCalls: { name: string; dir: string; cb: (p: number) => void }[] = []
    const resolvers: ((job: unknown) => void)[] = []
    mockApi.uploadFile.mockImplementation(
      (_id: string, dir: string, file: File, cb: (p: number) => void) => {
        uploadCalls.push({ name: file.name, dir, cb })
        return new Promise((res) => resolvers.push(res))
      }
    )
    const wrapper = await mountView()
    const listCallsBefore = mockApi.listFiles.mock.calls.length

    await setUploadFiles(wrapper, [new File(['aa'], 'one.txt'), new File(['bb'], 'two.txt')])

    // 单飞：第二个文件在第一个完成前不发起
    expect(uploadCalls).toHaveLength(1)
    expect(uploadCalls[0].name).toBe('one.txt')
    expect(uploadCalls[0].dir).toBe('/sdcard')

    // http 段百分比
    uploadCalls[0].cb(50)
    await nextTick()
    expect(wrapper.find('.phase-http .stub-progress').attributes('data-percentage')).toBe('50')
    expect(wrapper.find('.transfer-row').text()).toContain('上传中')

    // 100% 后进入 adb 段（推入设备中…，不伪造百分比）
    uploadCalls[0].cb(100)
    await nextTick()
    expect(wrapper.find('.phase-adb .stripe-bar').exists()).toBe(true)
    expect(wrapper.findAll('.transfer-row')[0].text()).toContain('推入设备中')

    resolvers[0](uploadJob())
    await flushPromises()
    expect(wrapper.findAll('.transfer-row')[0].classes()).toContain('phase-done')

    // 第二个文件起飞
    expect(uploadCalls).toHaveLength(2)
    expect(uploadCalls[1].name).toBe('two.txt')
    resolvers[1](uploadJob())
    await flushPromises()

    expect(wrapper.findAll('.transfer-row')[1].classes()).toContain('phase-done')
    // 全部完成后刷新当前目录
    expect(mockApi.listFiles).toHaveBeenCalledTimes(listCallsBefore + 1)
    expect(mockApi.listFiles).toHaveBeenLastCalledWith('dev1', '/sdcard')

    wrapper.unmount()
  })

  it('上传 409 TRANSFER_BUSY：ElMessage.warning + 行转 failed，后续文件不再发起', async () => {
    mockApi.uploadFile.mockRejectedValue({
      response: { data: { error: { code: 'TRANSFER_BUSY', message: '该设备已有进行中的文件传输，请等待其完成后再试' } } },
    })
    const wrapper = await mountView()

    await setUploadFiles(wrapper, [new File(['aa'], 'one.txt')])
    await flushPromises()

    expect(elMock.ElMessage.warning).toHaveBeenCalledWith('该设备已有进行中的文件传输，请等待其完成后再试')
    expect(wrapper.find('.phase-failed').exists()).toBe(true)

    wrapper.unmount()
  })

  it('重命名：新名含 / 前端校验拒绝，api 未被调并显示错误文案', async () => {
    const wrapper = await mountView()
    wrapper.findComponent({ name: 'ElTable' }).vm.$emit('selection-change', [fileA])
    await nextTick()

    await wrapper.find('.btn-rename').trigger('click')
    await nextTick()
    expect(wrapper.find('.stub-dialog').exists()).toBe(true)

    await wrapper.find('.rename-input').setValue('bad/name')
    await wrapper.find('.rename-save').trigger('click')
    await flushPromises()

    expect(mockApi.renameFile).not.toHaveBeenCalled()
    expect(wrapper.find('.rename-error').text()).toContain('/')

    wrapper.unmount()
  })

  it('重命名合法名：renameFile(旧全路径, 新名) 成功后关闭弹窗并重刷当前目录', async () => {
    const wrapper = await mountView()
    const listCallsBefore = mockApi.listFiles.mock.calls.length

    wrapper.findComponent({ name: 'ElTable' }).vm.$emit('selection-change', [fileA])
    await nextTick()
    await wrapper.find('.btn-rename').trigger('click')
    await nextTick()

    await wrapper.find('.rename-input').setValue('good.txt')
    await wrapper.find('.rename-save').trigger('click')
    await flushPromises()

    expect(mockApi.renameFile).toHaveBeenCalledWith('dev1', '/sdcard/a.txt', 'good.txt')
    expect(wrapper.find('.stub-dialog').exists()).toBe(false)
    expect(mockApi.listFiles).toHaveBeenCalledTimes(listCallsBefore + 1)

    wrapper.unmount()
  })

  it('删除多选（含目录）：纯文本递归强确认文案，results 汇总「成功 1 / 失败 1」并刷新', async () => {
    mockApi.deleteFiles.mockResolvedValue([
      { path: '/sdcard/a.txt', ok: true, error: null },
      { path: '/sdcard/Download', ok: false, error: 'busy' },
    ])
    const wrapper = await mountView()
    const listCallsBefore = mockApi.listFiles.mock.calls.length

    wrapper.findComponent({ name: 'ElTable' }).vm.$emit('selection-change', [fileA, dirD])
    await nextTick()
    await wrapper.find('.btn-delete').trigger('click')
    await flushPromises()

    expect(elMock.ElMessageBox.confirm).toHaveBeenCalled()
    const [message, , options] = elMock.ElMessageBox.confirm.mock.calls[0]
    expect(String(message)).toContain('递归删除目录')
    expect(String(message)).toContain('Download')
    expect(String(message)).toContain('不可恢复')
    // 防 XSS：确认框必须纯文本
    expect((options as Record<string, unknown>)?.dangerouslyUseHTMLString).not.toBe(true)

    expect(mockApi.deleteFiles).toHaveBeenCalledWith('dev1', ['/sdcard/a.txt', '/sdcard/Download'])
    expect(elMock.ElMessage.warning).toHaveBeenCalledWith('成功 1 / 失败 1')
    expect(mockApi.listFiles).toHaveBeenCalledTimes(listCallsBefore + 1)

    wrapper.unmount()
  })

  it('删除确认取消：不调用 deleteFiles', async () => {
    elMock.ElMessageBox.confirm.mockRejectedValue('cancel')
    const wrapper = await mountView()

    wrapper.findComponent({ name: 'ElTable' }).vm.$emit('selection-change', [fileA])
    await nextTick()
    await wrapper.find('.btn-delete').trigger('click')
    await flushPromises()

    expect(mockApi.deleteFiles).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('列表 403 → 「该路径不可访问」错误态；404 → 「目录不存在」带重试按钮', async () => {
    mockApi.listFiles.mockRejectedValueOnce({
      response: { data: { error: { code: 'PERMISSION_DENIED', message: 'nope' } } },
    })
    const wrapper = await mountView()
    expect(wrapper.find('.fm-error-text').text()).toContain('该路径不可访问')
    expect(wrapper.find('.btn-retry').exists()).toBe(false)

    mockApi.listFiles.mockRejectedValueOnce({
      response: { data: { error: { code: 'PATH_NOT_FOUND', message: 'gone' } } },
    })
    await wrapper.find('.btn-refresh').trigger('click')
    await flushPromises()
    expect(wrapper.find('.fm-error-text').text()).toContain('目录不存在')

    mockApi.listFiles.mockResolvedValueOnce([])
    await wrapper.find('.btn-retry').trigger('click')
    await flushPromises()
    expect(mockApi.listFiles).toHaveBeenCalledTimes(3)
    expect(wrapper.find('.fm-error').exists()).toBe(false)

    wrapper.unmount()
  })

  it('空目录显示 el-empty', async () => {
    mockApi.listFiles.mockResolvedValueOnce([])
    const wrapper = await mountView()
    expect(wrapper.find('.stub-empty').exists()).toBe(true)
    wrapper.unmount()
  })

  it('设备离线：alert 提示、操作按钮禁用、不拉取列表', async () => {
    mockApi.getDevice.mockResolvedValue({ id: 'dev1', status: 'offline' })
    const wrapper = await mountView()

    expect(wrapper.find('.stub-alert').exists()).toBe(true)
    expect(wrapper.find('.stub-alert-title').text()).toContain('离线')
    expect(mockApi.listFiles).not.toHaveBeenCalled()
    expect(wrapper.find('.btn-upload').attributes('disabled')).toBeDefined()
    expect(wrapper.find('.btn-download').attributes('disabled')).toBeDefined()
    expect(wrapper.find('.btn-delete').attributes('disabled')).toBeDefined()

    wrapper.unmount()
  })
})
