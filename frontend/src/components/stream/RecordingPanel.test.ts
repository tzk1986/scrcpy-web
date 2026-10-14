/**
 * RecordingPanel 组件测试（方案 37 B5）
 * =====================================
 *
 * 覆盖：
 *   - formatElapsed：秒 → mm:ss
 *   - 空闲态：显示「录屏」按钮，挂载时查询当前会话
 *   - 开始：进入录制态（红点 + 计时 00:00 起、fake timers 推进到 00:05、停止按钮）
 *   - 停止：done + local_path → 一行提示（已保存 + 查看录屏链接跳 /recordings）
 *     + 重新录制（方案 46：预览/下载/webm 导出移入独立录屏页）
 *   - 互斥：start 409 → ElMessage.error 提示且保持空闲（录屏入口仍在）
 *   - 卸载：录制中仅提示服务端 60 秒上限兜底，不调 stop API
 *
 * 说明：仿 AppsView.test.ts 模式——vi.hoisted + vi.mock('element-plus') +
 * vi.mock('@/services/api')；el-button / router-link 用轻量 stub 替代
 * （router-link 渲染成 <a :href>，供 href 编码断言）。
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

import RecordingPanel, { formatElapsed } from './RecordingPanel.vue'

const ElButtonStub = {
  name: 'ElButton',
  template: '<button class="stub-btn" type="button"><slot /></button>',
}

/** router-link stub：渲染成 <a :href> 供 href 断言（含 encodeURIComponent 编码）。 */
const RouterLinkStub = {
  name: 'RouterLink',
  props: ['to'],
  template: '<a :href="to"></a>',
}

function mountPanel(deviceId = 'dev1') {
  return shallowMount(RecordingPanel, {
    props: { deviceId },
    global: {
      stubs: { 'el-button': ElButtonStub, 'router-link': RouterLinkStub },
    },
  })
}

/** 挂载并进入 done 态（getRecording 返回 done + local_path）。 */
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

  it('停止返回 done → 一行提示：已保存 + 查看录屏链接 + 重新录制', async () => {
    mockApi.startRecording.mockResolvedValue({ session: sessionWith() })
    mockApi.stopRecording.mockResolvedValue({
      session: sessionWith({ state: 'done', local_path: 'data/recordings/s1.mp4' }),
      file_size: 3145728,
    })
    const wrapper = mountPanel('192.168.8.18:5555')
    await flushPromises()
    await wrapper.find('.record-start').trigger('click')
    await flushPromises()
    await wrapper.find('.record-stop').trigger('click')
    await flushPromises()

    expect(wrapper.find('video').exists()).toBe(false)
    expect(wrapper.find('a.record-download').exists()).toBe(false)
    expect(wrapper.find('.export-webm').exists()).toBe(false)
    const view = wrapper.find('.record-view')
    expect(view.exists()).toBe(true)
    expect(view.attributes('href')).toBe('/recordings?device=192.168.8.18%3A5555')
    expect(wrapper.find('.record-info').text()).toContain('已保存')
    expect(wrapper.find('.record-info').text()).toContain('约 3.0 MB')
    expect(wrapper.find('.record-again').exists()).toBe(true)
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

  it('挂载恢复完成态：done + local_path → 一行提示 + 查看录屏链接', async () => {
    mockApi.getRecording.mockResolvedValue({
      session: sessionWith({ state: 'done', local_path: 'data/recordings/s1.mp4' }),
    })
    const wrapper = mountPanel()
    await flushPromises()

    expect(wrapper.find('video').exists()).toBe(false)
    expect(wrapper.find('a.record-download').exists()).toBe(false)
    expect(wrapper.find('.record-view').exists()).toBe(true)
    expect(wrapper.find('.record-info').text()).toContain('已保存')
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

  it('挂载恢复：done + file_size → 已保存提示与大小信息行（方案 42 T4）', async () => {
    const wrapper = await mountDonePanel()

    expect(wrapper.find('.record-view').exists()).toBe(true)
    expect(wrapper.find('.record-info').text()).toContain('约 1.0 MB')
  })
})