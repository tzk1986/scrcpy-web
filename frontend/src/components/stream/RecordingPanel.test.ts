/**
 * RecordingPanel 组件测试（方案 37 B5）
 * =====================================
 *
 * 覆盖：
 *   - formatElapsed：秒 → mm:ss
 *   - 空闲态：显示「录屏」按钮，挂载时查询当前会话
 *   - 开始：进入录制态（红点 + 计时 00:00 起、fake timers 推进到 00:05、停止按钮）
 *   - 停止：done + local_path → video 预览（/file 端点）+ 下载链接
 *   - 互斥：start 409 → ElMessage.error 提示且保持空闲（录屏入口仍在）
 *   - 卸载：录制中仅提示服务端 60 秒上限兜底，不调 stop API
 *   - webm 导出（B6）：done 态显示「导出 webm」按钮；点击调用
 *     exportWebmClip(videoEl) 并触发 clip.webm 下载；失败提示且按钮恢复；
 *     浏览器不支持时按钮禁用（webmExporter 模块整体 mock）
 *
 * 说明：仿 AppsView.test.ts 模式——vi.hoisted + vi.mock('element-plus') +
 * vi.mock('@/services/api')；el-button 用轻量 stub 替代。
 * 计时依赖 setInterval：fake timers 只伪造 interval（setTimeout 保持真实，
 * 否则 flushPromises 依赖的 setTimeout(0) 会挂起）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, shallowMount } from '@vue/test-utils'
import { nextTick } from 'vue'

const h = vi.hoisted(() => ({
  ElMessage: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}))
vi.mock('element-plus', () => ({
  ElMessage: h.ElMessage,
}))

const mockApi = vi.hoisted(() => ({
  startRecording: vi.fn(),
  stopRecording: vi.fn(),
  getRecording: vi.fn(),
  listRecordings: vi.fn(),
}))
vi.mock('@/services/api', () => ({ api: mockApi }))

const mockExporter = vi.hoisted(() => ({
  exportWebmClip: vi.fn(),
  isWebmRecorderSupported: vi.fn(),
}))
vi.mock('@/utils/webmExporter', () => mockExporter)

import RecordingPanel, { formatElapsed } from './RecordingPanel.vue'

const ElButtonStub = {
  name: 'ElButton',
  template: '<button class="stub-btn" type="button"><slot /></button>',
}

function mountPanel() {
  return shallowMount(RecordingPanel, {
    props: { deviceId: 'dev1' },
    global: {
      stubs: { 'el-button': ElButtonStub },
    },
  })
}

/** 挂载并进入 done 预览态（getRecording 返回 done + local_path）。 */
async function mountDonePanel() {
  mockApi.getRecording.mockResolvedValue({
    session: sessionWith({ state: 'done', local_path: 'data/recordings/s1.mp4' }),
    file_size: 1048576,
  })
  const wrapper = mountPanel()
  await flushPromises()
  return wrapper
}

/** 录制态会话（对应后端 RecordingSession 结构）。 */
function sessionWith(overrides: Record<string, unknown> = {}) {
  return {
    id: 's1',
    device_id: 'dev1',
    started_at: 0,
    state: 'recording',
    remote_path: '/sdcard/openscrcpy_rec_s1.mp4',
    local_path: '',
    ...overrides,
  }
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] })
  for (const fn of Object.values(mockApi)) fn.mockReset()
  mockApi.getRecording.mockResolvedValue(undefined) // 默认无会话 → idle
  mockExporter.exportWebmClip.mockReset()
  mockExporter.isWebmRecorderSupported.mockReset()
  mockExporter.isWebmRecorderSupported.mockReturnValue(true) // 默认支持 webm
  h.ElMessage.success.mockClear()
  h.ElMessage.error.mockClear()
  h.ElMessage.info.mockClear()
})

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe('formatElapsed', () => {
  it('秒 → mm:ss', () => {
    expect(formatElapsed(0)).toBe('00:00')
    expect(formatElapsed(5)).toBe('00:05')
    expect(formatElapsed(65)).toBe('01:05')
    expect(formatElapsed(3599)).toBe('59:59')
  })
})

describe('RecordingPanel', () => {
  it('空闲态显示录屏按钮，挂载时查询当前会话', async () => {
    const wrapper = mountPanel()
    await flushPromises()

    expect(mockApi.getRecording).toHaveBeenCalledWith('dev1')
    expect(wrapper.find('.record-start').exists()).toBe(true)
    expect(wrapper.find('.record-stop').exists()).toBe(false)
    expect(wrapper.find('video').exists()).toBe(false)
    expect(wrapper.find('.export-webm').exists()).toBe(false) // 仅 done 态显示导出按钮
  })

  it('点击录屏进入录制态：红点 + 计时 00:00 起推进到 00:05、停止按钮出现', async () => {
    mockApi.startRecording.mockResolvedValue({ session: sessionWith() })
    const wrapper = mountPanel()
    await flushPromises()

    await wrapper.find('.record-start').trigger('click')
    await flushPromises()

    expect(mockApi.startRecording).toHaveBeenCalledWith('dev1')
    expect(wrapper.find('.record-dot').exists()).toBe(true)
    expect(wrapper.find('.record-timer').text()).toBe('00:00')
    expect(wrapper.find('.record-stop').exists()).toBe(true)
    expect(wrapper.find('.record-start').exists()).toBe(false)

    await vi.advanceTimersByTimeAsync(5000)
    await nextTick()
    expect(wrapper.find('.record-timer').text()).toBe('00:05')

    wrapper.unmount()
  })

  it('停止返回 done + local_path → video 预览与下载链接指向 /file 端点', async () => {
    mockApi.startRecording.mockResolvedValue({ session: sessionWith() })
    mockApi.stopRecording.mockResolvedValue({
      session: sessionWith({ state: 'done', local_path: 'data/recordings/s1.mp4' }),
      file_size: 3145728,
    })
    const wrapper = mountPanel()
    await flushPromises()
    await wrapper.find('.record-start').trigger('click')
    await flushPromises()

    await wrapper.find('.record-stop').trigger('click')
    await flushPromises()

    expect(mockApi.stopRecording).toHaveBeenCalledWith('dev1')
    const video = wrapper.find('video')
    expect(video.exists()).toBe(true)
    expect(video.attributes('src')).toBe('/api/recording/dev1/file')
    const link = wrapper.find('a.record-download')
    expect(link.exists()).toBe(true)
    expect(link.attributes('href')).toBe('/api/recording/dev1/file')
    expect(link.attributes('download')).toMatch(/\.mp4$/)
    expect(wrapper.find('.record-start').exists()).toBe(false)
    expect(wrapper.find('.record-again').exists()).toBe(true)
    expect(wrapper.find('.record-info').text()).toBe('约 3.0 MB')
  })

  it('开始互斥 409 → ElMessage.error 提示且保持录屏入口', async () => {
    mockApi.startRecording.mockRejectedValue({
      response: {
        status: 409,
        data: { error: { message: '该设备已有进行中的录屏会话，请先停止当前录制后再试' } },
      },
    })
    const wrapper = mountPanel()
    await flushPromises()

    await wrapper.find('.record-start').trigger('click')
    await flushPromises()

    expect(h.ElMessage.error).toHaveBeenCalledWith(
      '该设备已有进行中的录屏会话，请先停止当前录制后再试'
    )
    expect(wrapper.find('.record-start').exists()).toBe(true)
    expect(wrapper.find('.record-stop').exists()).toBe(false)
  })

  it('挂载恢复录制态：getRecording 返回 recording 会话 → 按 started_at 推进计时', async () => {
    const nowSec = Math.floor(Date.now() / 1000)
    mockApi.getRecording.mockResolvedValue({
      session: sessionWith({ started_at: nowSec - 5 }),
    })
    const wrapper = mountPanel()
    await flushPromises()

    expect(wrapper.find('.record-timer').text()).toBe('00:05')
    expect(wrapper.find('.record-stop').exists()).toBe(true)
    wrapper.unmount()
  })

  it('挂载恢复完成态：done + local_path → 直接显示预览与下载', async () => {
    mockApi.getRecording.mockResolvedValue({
      session: sessionWith({ state: 'done', local_path: 'data/recordings/s1.mp4' }),
    })
    const wrapper = mountPanel()
    await flushPromises()

    expect(wrapper.find('video').exists()).toBe(true)
    expect(wrapper.find('a.record-download').exists()).toBe(true)
  })

  it('录制中卸载：提示服务端 60 秒上限兜底且不调 stop API', async () => {
    mockApi.startRecording.mockResolvedValue({ session: sessionWith() })
    const wrapper = mountPanel()
    await flushPromises()
    await wrapper.find('.record-start').trigger('click')
    await flushPromises()

    wrapper.unmount()

    expect(h.ElMessage.info).toHaveBeenCalledWith('录制仍在继续，服务端将按 60 秒上限自动收尾')
    expect(mockApi.stopRecording).not.toHaveBeenCalled()
  })

  it('停止返回 failed → 回到空闲态并提示未生成录像', async () => {
    mockApi.startRecording.mockResolvedValue({ session: sessionWith() })
    mockApi.stopRecording.mockResolvedValue({ session: sessionWith({ state: 'failed' }) })
    const wrapper = mountPanel()
    await flushPromises()
    await wrapper.find('.record-start').trigger('click')
    await flushPromises()

    await wrapper.find('.record-stop').trigger('click')
    await flushPromises()

    expect(h.ElMessage.error).toHaveBeenCalledWith('录制已结束但未生成录像文件')
    expect(wrapper.find('.record-start').exists()).toBe(true)
    expect(wrapper.find('video').exists()).toBe(false)
  })

  it('done 态显示「导出 webm」按钮（浏览器支持时可用）', async () => {
    const wrapper = await mountDonePanel()

    const btn = wrapper.find('.export-webm')
    expect(btn.exists()).toBe(true)
    expect(btn.text()).toContain('导出 webm')
    expect(btn.attributes('disabled')).toBeUndefined()
  })

  it('浏览器不支持 webm 时导出按钮禁用（降级防御，不硬抛）', async () => {
    mockExporter.isWebmRecorderSupported.mockReturnValue(false)
    const wrapper = await mountDonePanel()

    const btn = wrapper.find('.export-webm')
    expect(btn.exists()).toBe(true)
    expect(btn.attributes('disabled')).toBeDefined()
  })

  it('点击「导出 webm」：exportWebmClip 拿 video 元素 → Blob 触发 clip.webm 下载后 revoke', async () => {
    const wrapper = await mountDonePanel()

    const blob = new Blob(['clip-data'], { type: 'video/webm' })
    mockExporter.exportWebmClip.mockResolvedValue(blob)
    const createUrlSpy = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:mock-clip')
    const revokeSpy = vi.spyOn(URL, 'revokeObjectURL')
    // 对象持有而非 let 变量：避免 TS 控制流把闭包赋值收窄成 never
    const clicked = { anchor: null as HTMLAnchorElement | null }
    const clickSpy = vi
      .spyOn(HTMLAnchorElement.prototype, 'click')
      .mockImplementation(function (this: HTMLAnchorElement) {
        clicked.anchor = this
      })

    await wrapper.find('.export-webm').trigger('click')
    await flushPromises()

    expect(mockExporter.exportWebmClip).toHaveBeenCalledTimes(1)
    expect(mockExporter.exportWebmClip.mock.calls[0][0]?.tagName).toBe('VIDEO')
    expect(createUrlSpy).toHaveBeenCalledWith(blob)
    expect(clicked.anchor?.download).toBe('clip.webm')
    expect(clicked.anchor?.getAttribute('href')).toBe('blob:mock-clip')
    expect(revokeSpy).toHaveBeenCalledWith('blob:mock-clip')
    expect(clickSpy).toHaveBeenCalledTimes(1)
  })

  it('导出失败 → ElMessage.error 提示且按钮恢复可用', async () => {
    const wrapper = await mountDonePanel()

    mockExporter.exportWebmClip.mockRejectedValue(new Error('boom'))
    await wrapper.find('.export-webm').trigger('click')
    await flushPromises()

    expect(h.ElMessage.error).toHaveBeenCalledWith('webm 导出失败')
    expect(wrapper.find('.export-webm').attributes('disabled')).toBeUndefined()
  })

  it('done 态「重新录制」：点击 → 调 startRecording 进入录制态并清信息行（方案 42 T4）', async () => {
    const wrapper = await mountDonePanel()
    mockApi.startRecording.mockResolvedValue({ session: sessionWith() })

    await wrapper.find('.record-again').trigger('click')
    await flushPromises()

    expect(mockApi.startRecording).toHaveBeenCalledWith('dev1')
    expect(wrapper.find('.record-timer').exists()).toBe(true)
    expect(wrapper.find('.record-info').exists()).toBe(false)
  })

  it('挂载恢复：GET 会话 404（产物已被清理，后端已收敛）→ 静默回 idle 不弹错误（方案 42 T4）', async () => {
    mockApi.getRecording.mockRejectedValue({ response: { status: 404 } })
    const wrapper = mountPanel()
    await flushPromises()

    expect(wrapper.find('.record-start').exists()).toBe(true)
    expect(h.ElMessage.error).not.toHaveBeenCalled()
  })

  it('挂载恢复：done + file_size → 预览与大小信息行（方案 42 T4）', async () => {
    const wrapper = await mountDonePanel()

    expect(wrapper.find('video').exists()).toBe(true)
    expect(wrapper.find('.record-info').text()).toBe('约 1.0 MB')
  })
})
