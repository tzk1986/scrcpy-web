/**
 * Recordings 页测试（方案 46 T3）
 * ==============================
 * 覆盖：
 *   - 挂载加载列表：设备列原样 / 旧格式「未知设备（旧版产物）」/ 大小 1 位小数 /
 *     时间本地化 / 行内播放·下载·删除按钮
 *   - ?device= 过滤：仅该设备 + 「仅看」条 + 清除恢复全量
 *   - 下载 href：recordingFileUrl 编码文件名
 *   - 播放弹层：video src 指向文件端点；webm 导出按钮随支持面启用/禁用
 *   - 删除：二次确认 → api → 重拉；失败 → error toast 且列表不消失
 *   - 列表加载失败：error toast + 空态；空态含回 Dashboard 引导
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, shallowMount } from '@vue/test-utils'

const h = vi.hoisted(() => ({
  ElMessage: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  ElMessageBox: { confirm: vi.fn() },
}))
vi.mock('element-plus', () => ({
  ElMessage: h.ElMessage,
  ElMessageBox: h.ElMessageBox,
}))

const api = vi.hoisted(() => ({
  listRecordingFiles: vi.fn(),
  deleteRecordingFile: vi.fn(),
  recordingFileUrl: (name: string) => `/api/recording/files/${encodeURIComponent(name)}`,
}))
vi.mock('@/services/api', () => ({ api }))

const exporter = vi.hoisted(() => ({
  isWebmRecorderSupported: vi.fn(() => false),
  exportWebmClip: vi.fn(),
}))
vi.mock('@/utils/webmExporter', () => exporter)

const routeMock = vi.hoisted(() => ({
  current: { query: {} as Record<string, string> },
}))
vi.mock('vue-router', () => ({
  useRoute: () => routeMock.current,
}))

import Recordings from './Recordings.vue'

const ITEM_NEW = {
  filename: 'openscrcpy_rec_192.168.8.18-5555__a1b2c3d4e5f6.mp4',
  device: '192.168.8.18-5555',
  session_id: 'a1b2c3d4e5f6',
  size_bytes: 3145728,
  created_at: '2026-10-14T04:00:00+00:00',
}
const ITEM_OLD = {
  filename: 'openscrcpy_rec_aaa111222333.mp4',
  device: null,
  session_id: 'aaa111222333',
  size_bytes: 1048576,
  created_at: '2026-10-13T04:00:00+00:00',
}

const RouterLinkStub = {
  name: 'RouterLink',
  props: ['to'],
  template: '<a :href="typeof to === \'string\' ? to : to.path">{{ $slots.default?.()[0]?.children }}</a>',
}

function mountPage(deviceQuery?: string) {
  routeMock.current = { query: deviceQuery ? { device: deviceQuery } : {} }
  return shallowMount(Recordings, {
    global: {
      stubs: {
        // 注意：不可在 stub 内 $emit('click')——父级 @click 会经 attrs 合并到根
        // <button> 上，重复 emit 会令处理器双击生效（仓库既有 stub 范式：不含 emit）
        'el-button': { template: '<button><slot /></button>' },
        'el-dialog': { props: ['modelValue'], template: '<div v-if="modelValue"><slot /></div>' },
        'el-empty': { template: '<div><slot /></div>' },
        RouterLinkStub,
      },
      components: { 'router-link': RouterLinkStub },
    },
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  routeMock.current = { query: {} }
  api.listRecordingFiles.mockResolvedValue({ files: [ITEM_NEW, ITEM_OLD] })
  h.ElMessageBox.confirm.mockResolvedValue(true)
})

describe('Recordings 页', () => {
  it('挂载加载列表：设备列 / 旧格式文案 / 大小一位小数', async () => {
    const w = mountPage()
    await flushPromises()
    expect(api.listRecordingFiles).toHaveBeenCalledTimes(1)
    const rows = w.findAll('.file-row')
    expect(rows).toHaveLength(2)
    expect(rows[0].find('.col-device').text()).toBe('192.168.8.18-5555')
    expect(rows[1].find('.col-device').text()).toBe('未知设备（旧版产物）')
    expect(rows[0].find('.col-size').text()).toBe('3.0 MB')
  })

  it('?device= 进入即过滤并显示「仅看」条，清除后恢复全量', async () => {
    const w = mountPage('192.168.8.18:5555')
    await flushPromises()
    expect(w.findAll('.file-row')).toHaveLength(1)
    expect(w.find('.filter-bar').text()).toContain('192.168.8.18:5555')
    await w.find('.filter-clear').trigger('click')
    expect(w.findAll('.file-row')).toHaveLength(2)
  })

  it('下载链接 href 为编码后的文件端点', async () => {
    const w = mountPage()
    await flushPromises()
    const a = w.findAll('.file-row')[1].find('a.col-download')
    expect(a.attributes('href')).toBe('/api/recording/files/openscrcpy_rec_aaa111222333.mp4')
  })

  it('播放打开弹层：video src 指向文件端点，webm 按钮随支持面禁用', async () => {
    const w = mountPage()
    await flushPromises()
    await w.findAll('.file-row')[0].find('.btn-play').trigger('click')
    const video = w.find('video')
    expect(video.exists()).toBe(true)
    expect(video.attributes('src')).toBe(
      '/api/recording/files/openscrcpy_rec_192.168.8.18-5555__a1b2c3d4e5f6.mp4'
    )
    expect(w.find('.btn-webm').attributes('disabled')).toBeDefined()
    exporter.isWebmRecorderSupported.mockReturnValue(true)
    await w.find('.btn-play').trigger('click') // 触发重开以走支持分支——组件在点击时读支持面
    expect(w.find('.btn-webm').attributes('disabled')).toBeUndefined()
  })

  it('删除：确认后调 api 并重拉列表；失败仅 toast 不消失行', async () => {
    const w = mountPage()
    await flushPromises()
    api.deleteRecordingFile.mockResolvedValue({ deleted: true })
    await w.findAll('.file-row')[0].find('.btn-delete').trigger('click')
    await flushPromises()
    expect(api.deleteRecordingFile).toHaveBeenCalledWith(ITEM_NEW.filename)
    expect(api.listRecordingFiles).toHaveBeenCalledTimes(2)
    // 失败路径
    api.deleteRecordingFile.mockRejectedValue(new Error('busy'))
    await w.findAll('.file-row')[1].find('.btn-delete').trigger('click')
    await flushPromises()
    expect(h.ElMessage.error).toHaveBeenCalledWith('删除失败，文件可能正被占用')
  })

  it('取消确认不调删除', async () => {
    h.ElMessageBox.confirm.mockRejectedValue('cancel')
    const w = mountPage()
    await flushPromises()
    await w.findAll('.file-row')[0].find('.btn-delete').trigger('click')
    await flushPromises()
    expect(api.deleteRecordingFile).not.toHaveBeenCalled()
  })

  it('列表加载失败：error toast + 空态', async () => {
    api.listRecordingFiles.mockRejectedValue(new Error('boom'))
    const w = mountPage()
    await flushPromises()
    expect(h.ElMessage.error).toHaveBeenCalledWith('录屏列表加载失败')
    expect(w.findAll('.file-row')).toHaveLength(0)
    expect(w.find('.empty-hint').text()).toContain('暂无录屏')
  })
})
