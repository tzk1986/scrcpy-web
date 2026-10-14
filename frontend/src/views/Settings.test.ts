/**
 * Settings 页面组件测试（方案 45 T5）
 * ====================================
 *
 * 覆盖：
 *   - 挂载：getSettings → 两节值回填 StreamFieldSet / RecordingFieldSet
 *     （表单值直接用后端键名，控制器裁定 R4）；h265 探测结果透传
 *   - 保存串流：putSettings({stream:{max_size,...}}) + toast「已保存，下次连接生效」
 *   - 保存录屏：putSettings({recording:{bit_rate,time_limit}}) + toast「已保存」（立即生效，spec §5.3）
 *   - 表单值变更后保存提交新值（v-model 链路）
 *   - h265 gating（T4 / 评审焦点 #4）：探测 false → option 禁用 + 提示文本；true → 可选
 *   - 重置：ElMessageBox.confirm 后 putSettings({节: null}) + 重拉；取消确认不写入
 *   - 加载 / 保存失败 → ElMessage.error（T2 500 SETTINGS_STORE_ERROR 可见透传）
 *
 * 说明：照 FileManager.test.ts 的 mock 模式（vi.hoisted elMock + vi.mock
 * element-plus / @/services/api）；el-* 组件以轻量 stub 替代（测试不引入
 * ElementPlus 插件，stub 键沿用 kebab-case 既有范式）。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, shallowMount, type VueWrapper } from '@vue/test-utils'
import { defineComponent, h } from 'vue'

const elMock = vi.hoisted(() => ({
  ElMessage: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
  ElMessageBox: { confirm: vi.fn() },
}))
vi.mock('element-plus', () => ({
  ElMessage: elMock.ElMessage,
  ElMessageBox: elMock.ElMessageBox,
}))

const mockApi = vi.hoisted(() => ({
  getSettings: vi.fn(),
  putSettings: vi.fn(),
  getRuntimeEnv: vi.fn(),
  checkScrcpyUpdate: vi.fn(),
  listDevices: vi.fn(),
}))
vi.mock('@/services/api', () => ({ api: mockApi }))

const routeMock = vi.hoisted(() => ({
  current: { query: {} as Record<string, unknown> },
}))
vi.mock('vue-router', () => ({
  useRoute: () => routeMock.current,
}))

const codecMock = vi.hoisted(() => ({ isH265DecoderSupported: vi.fn() }))
vi.mock('@/services/codecSupport', () => codecMock)

import RecordingFieldSet from '@/components/settings/RecordingFieldSet.vue'
import StreamFieldSet, {
  type StreamFormValue,
} from '@/components/settings/StreamFieldSet.vue'
import Settings from './Settings.vue'

const DEFAULT_VIEW = {
  stream: {
    max_size: { value: 1080, source: 'default' },
    bit_rate: { value: '4M', source: 'default' },
    fps: { value: 30, source: 'default' },
    codec: { value: 'h264', source: 'default' },
    adaptive_bitrate: { value: true, source: 'default' },
  },
  recording: {
    bit_rate: { value: '4M', source: 'default' },
    time_limit: { value: 60, source: 'default' },
  },
}

const DEFAULT_STREAM_FORM: StreamFormValue = {
  max_size: 1080,
  bit_rate: '4M',
  fps: 30,
  codec: 'h264',
  adaptive_bitrate: true,
}

/** 与真实后端形状一致：版本为 方案 44 单源常量原值 "5.0.1"，展示层补 v 前缀。 */
const RUNTIME_ENV_VIEW = {
  scrcpy_server_version: '5.0.1',
  jar_path: 'C:/x/scrcpy-server.jar',
  adb_path: 'C:/x/tools/adb.exe',
  adb_version: 'Android Debug Bridge version 1.0.41',
  frozen: false,
}

// ==================== el-* 轻量 stub ====================

const ElButtonStub = defineComponent({
  name: 'ElButton',
  props: { loading: Boolean },
  inheritAttrs: false,
  setup(props, { slots, attrs }) {
    return () =>
      h(
        'button',
        { ...attrs, class: 'stub-btn', 'data-loading': String(!!props.loading) },
        slots.default?.()
      )
  },
})

const ElCardStub = defineComponent({
  name: 'ElCard',
  setup(_, { slots }) {
    return () =>
      h('div', { class: 'stub-card' }, [
        h('div', { class: 'stub-card-header' }, slots.header?.()),
        slots.default?.(),
      ])
  },
})

const ElFormStub = defineComponent({
  name: 'ElForm',
  props: { labelWidth: { type: [String, Number], default: undefined } },
  setup(_, { slots }) {
    return () => h('form', { class: 'stub-form' }, slots.default?.())
  },
})

const ElFormItemStub = defineComponent({
  name: 'ElFormItem',
  props: { label: String },
  setup(props, { slots }) {
    return () =>
      h('div', { class: 'stub-form-item' }, [
        h('span', { class: 'stub-label' }, props.label),
        slots.default?.(),
      ])
  },
})

const ElInputNumberStub = defineComponent({
  name: 'ElInputNumber',
  props: { modelValue: Number, min: Number, max: Number },
  emits: ['update:modelValue'],
  setup(props, { emit }) {
    return () =>
      h('input', {
        class: 'stub-input-number',
        type: 'number',
        value: props.modelValue,
        onInput: (e: Event) =>
          emit('update:modelValue', Number((e.target as HTMLInputElement).value)),
      })
  },
})

const ElSelectStub = defineComponent({
  name: 'ElSelect',
  props: { modelValue: { type: String, default: '' } },
  emits: ['update:modelValue'],
  setup(props, { slots }) {
    return () =>
      h('div', { class: 'stub-select', 'data-value': props.modelValue }, slots.default?.())
  },
})

const ElOptionStub = defineComponent({
  name: 'ElOption',
  props: { label: String, value: String, disabled: Boolean },
  setup(props) {
    return () =>
      h(
        'div',
        {
          class: 'stub-option',
          'data-value': props.value,
          'data-disabled': String(!!props.disabled),
        },
        props.label
      )
  },
})

const ElSwitchStub = defineComponent({
  name: 'ElSwitch',
  props: { modelValue: Boolean },
  emits: ['update:modelValue'],
  setup(props, { emit }) {
    return () =>
      h('button', {
        class: 'stub-switch',
        type: 'button',
        'data-checked': String(!!props.modelValue),
        onClick: () => emit('update:modelValue', !props.modelValue),
      })
  },
})

const ElTagStub = defineComponent({
  name: 'ElTag',
  props: { type: String, size: String },
  setup(props, { slots }) {
    return () =>
      h('span', { class: 'stub-tag', 'data-type': props.type ?? '' }, slots.default?.())
  },
})

const ElDescriptionsStub = defineComponent({
  name: 'ElDescriptions',
  props: { border: Boolean, column: Number },
  setup(_, { slots }) {
    return () => h('div', { class: 'stub-descriptions' }, slots.default?.())
  },
})

const ElDescriptionsItemStub = defineComponent({
  name: 'ElDescriptionsItem',
  props: { label: String },
  setup(props, { slots }) {
    return () =>
      h('div', { class: 'stub-descriptions-item' }, [
        h('span', { class: 'stub-label' }, props.label),
        slots.default?.(),
      ])
  },
})

const ElCollapseStub = defineComponent({
  name: 'ElCollapse',
  setup(_, { slots }) {
    return () => h('div', { class: 'stub-collapse' }, slots.default?.())
  },
})

const ElCollapseItemStub = defineComponent({
  name: 'ElCollapseItem',
  props: { title: String, name: String },
  setup(props, { slots }) {
    return () =>
      h('div', { class: 'stub-collapse-item' }, [
        h('div', { class: 'stub-collapse-title' }, props.title),
        slots.default?.(),
      ])
  },
})

/** FieldSet 直挂时使用的表单类 stub 集合 */
const FORM_STUBS = {
  'el-form': ElFormStub,
  'el-form-item': ElFormItemStub,
  'el-input-number': ElInputNumberStub,
  'el-select': ElSelectStub,
  'el-option': ElOptionStub,
  'el-switch': ElSwitchStub,
}

async function mountSettings(): Promise<VueWrapper> {
  const wrapper = shallowMount(Settings, {
    global: {
      stubs: {
        'el-card': ElCardStub,
        'el-button': ElButtonStub,
        'el-divider': true,
        'el-select': ElSelectStub,
        'el-option': ElOptionStub,
        'el-descriptions': ElDescriptionsStub,
        'el-descriptions-item': ElDescriptionsItemStub,
        'el-tag': ElTagStub,
        'el-collapse': ElCollapseStub,
        'el-collapse-item': ElCollapseItemStub,
      },
    },
  })
  await flushPromises()
  return wrapper
}

/** axios 结构化错误（backend {"error":{code,message}} 形状） */
function axiosError(code: string, message: string) {
  return { response: { data: { error: { code, message } } } }
}

beforeEach(() => {
  vi.clearAllMocks()
  routeMock.current = { query: {} }
  mockApi.getSettings.mockResolvedValue(DEFAULT_VIEW)
  mockApi.putSettings.mockResolvedValue({ saved: true })
  mockApi.getRuntimeEnv.mockResolvedValue(RUNTIME_ENV_VIEW)
  mockApi.listDevices.mockResolvedValue([{ device_id: 'd1' }, { device_id: 'd9' }])
  codecMock.isH265DecoderSupported.mockResolvedValue(false)
  elMock.ElMessageBox.confirm.mockReset().mockResolvedValue('confirm')
})

describe('Settings.vue', () => {
  it('挂载拉取设置并回填两个表单组件（后端键名）+ h265 探测透传', async () => {
    const wrapper = await mountSettings()

    expect(mockApi.getSettings).toHaveBeenCalledOnce()
    expect(codecMock.isH265DecoderSupported).toHaveBeenCalledOnce()

    const stream = wrapper.findComponent(StreamFieldSet)
    expect(stream.exists()).toBe(true)
    expect(stream.props('modelValue')).toEqual(DEFAULT_STREAM_FORM)
    expect(stream.props('h265Supported')).toBe(false)

    const recording = wrapper.findComponent(RecordingFieldSet)
    expect(recording.exists()).toBe(true)
    expect(recording.props('modelValue')).toEqual({ bit_rate: '4M', time_limit: 60 })

    wrapper.unmount()
  })

  it('保存串流参数：后端键 patch + toast「已保存，下次连接生效」', async () => {
    const wrapper = await mountSettings()

    await wrapper.find('[data-test="save-stream"]').trigger('click')
    await flushPromises()

    expect(mockApi.putSettings).toHaveBeenCalledWith({ stream: { ...DEFAULT_STREAM_FORM } })
    expect(elMock.ElMessage.success).toHaveBeenCalledWith('已保存，下次连接生效')

    wrapper.unmount()
  })

  it('保存录屏参数：recording 节 patch + toast「已保存」', async () => {
    const wrapper = await mountSettings()

    await wrapper.find('[data-test="save-recording"]').trigger('click')
    await flushPromises()

    expect(mockApi.putSettings).toHaveBeenCalledWith({
      recording: { bit_rate: '4M', time_limit: 60 },
    })
    expect(elMock.ElMessage.success).toHaveBeenCalledWith('已保存')

    wrapper.unmount()
  })

  it('shows effective values with source badges（生效参数卡）', async () => {
    const wrapper = await mountSettings()

    const card = wrapper.find('[data-test="effective-values"]')
    expect(card.exists()).toBe(true)
    expect(card.text()).toContain('bit_rate')
    expect(card.text()).toContain('4M')

    const badges = card.findAll('.stub-tag')
    expect(badges.length).toBeGreaterThan(0)
    for (const badge of badges) {
      expect(badge.text()).toBe('出厂默认')
      expect(badge.attributes('data-type')).toBe('info')
    }

    wrapper.unmount()
  })

  it('来源徽标映射：user → 用户设置 / env → 环境覆盖（前端只渲染后端 source）', async () => {
    mockApi.getSettings.mockResolvedValue({
      stream: {
        ...DEFAULT_VIEW.stream,
        bit_rate: { value: '8M', source: 'user' },
        codec: { value: 'h265', source: 'env' },
      },
      recording: DEFAULT_VIEW.recording,
    })
    const wrapper = await mountSettings()

    const text = wrapper.find('[data-test="effective-values"]').text()
    expect(text).toContain('8M')
    expect(text).toContain('用户设置')
    expect(text).toContain('环境覆盖')

    wrapper.unmount()
  })

  it('shows runtime env info from api（运行环境卡）', async () => {
    const wrapper = await mountSettings()

    expect(mockApi.getRuntimeEnv).toHaveBeenCalledOnce()
    const card = wrapper.find('[data-test="runtime-env"]')
    expect(card.exists()).toBe(true)
    expect(card.text()).toContain('v5.0.1')
    expect(card.text()).toContain('Android Debug Bridge version 1.0.41')
    expect(card.text()).toContain('C:/x/scrcpy-server.jar')
    expect(card.text()).toContain('C:/x/tools/adb.exe')

    wrapper.unmount()
  })

  it('检查更新 updateAvailable：版本对比文案 + 发布页链接 href', async () => {
    mockApi.checkScrcpyUpdate.mockResolvedValue({
      status: 'updateAvailable',
      current_version: '5.0.1',
      latest_version: 'v5.1.0',
      url: 'https://github.com/Genymobile/scrcpy/releases',
      error: null,
    })
    const wrapper = await mountSettings()

    await wrapper.find('[data-test="check-update"]').trigger('click')
    await flushPromises()

    expect(mockApi.checkScrcpyUpdate).toHaveBeenCalledOnce()
    const result = wrapper.find('[data-test="update-result"]')
    expect(result.exists()).toBe(true)
    expect(result.text()).toContain('上游有 v5.1.0（本地 5.0.1）')
    expect(result.text()).toContain('方案 44 换包流程')
    const link = result.find('a')
    expect(link.exists()).toBe(true)
    expect(link.attributes('href')).toBe('https://github.com/Genymobile/scrcpy/releases')
    expect(link.text()).toBe('查看发布页')

    wrapper.unmount()
  })

  it('检查更新 upToDate：已是最新文案', async () => {
    mockApi.checkScrcpyUpdate.mockResolvedValue({
      status: 'upToDate',
      current_version: '5.0.1',
      latest_version: 'v5.0.1',
      url: 'https://github.com/Genymobile/scrcpy/releases',
      error: null,
    })
    const wrapper = await mountSettings()

    await wrapper.find('[data-test="check-update"]').trigger('click')
    await flushPromises()

    const result = wrapper.find('[data-test="update-result"]')
    expect(result.exists()).toBe(true)
    expect(result.text()).toContain('已是最新（本地 5.0.1）')

    wrapper.unmount()
  })

  it('检查更新 installedNewer：本地高于上游文案', async () => {
    mockApi.checkScrcpyUpdate.mockResolvedValue({
      status: 'installedNewer',
      current_version: '5.0.1',
      latest_version: 'v4.9.0',
      url: 'https://github.com/Genymobile/scrcpy/releases',
      error: null,
    })
    const wrapper = await mountSettings()

    await wrapper.find('[data-test="check-update"]').trigger('click')
    await flushPromises()

    const result = wrapper.find('[data-test="update-result"]')
    expect(result.exists()).toBe(true)
    expect(result.text()).toContain('本地版本高于上游（本地 5.0.1 / 上游 v4.9.0）')

    wrapper.unmount()
  })

  it('检查更新 unknown：显示 error + warning toast', async () => {
    mockApi.checkScrcpyUpdate.mockResolvedValue({
      status: 'unknown',
      current_version: '5.0.1',
      latest_version: null,
      url: null,
      error: '无法访问 GitHub（离线/内网环境？）：offline',
    })
    const wrapper = await mountSettings()

    await wrapper.find('[data-test="check-update"]').trigger('click')
    await flushPromises()

    const result = wrapper.find('[data-test="update-result"]')
    expect(result.exists()).toBe(true)
    expect(result.text()).toContain('无法访问 GitHub（离线/内网环境？）：offline')
    expect(elMock.ElMessage.warning).toHaveBeenCalledWith(
      '无法访问 GitHub（离线/内网环境？）：offline'
    )

    wrapper.unmount()
  })

  it('表单值变更后保存提交新值（v-model 链路）', async () => {
    const wrapper = await mountSettings()

    wrapper.findComponent(StreamFieldSet).vm.$emit('update:modelValue', {
      max_size: 720,
      bit_rate: '8M',
      fps: 60,
      codec: 'h265',
      adaptive_bitrate: false,
    })
    await flushPromises()
    await wrapper.find('[data-test="save-stream"]').trigger('click')
    await flushPromises()

    expect(mockApi.putSettings).toHaveBeenCalledWith({
      stream: { max_size: 720, bit_rate: '8M', fps: 60, codec: 'h265', adaptive_bitrate: false },
    })

    wrapper.unmount()
  })

  it('重置串流：确认后 putSettings({stream: null}) 并重拉设置', async () => {
    const wrapper = await mountSettings()

    await wrapper.find('[data-test="reset-stream"]').trigger('click')
    await flushPromises()

    expect(elMock.ElMessageBox.confirm).toHaveBeenCalledOnce()
    expect(mockApi.putSettings).toHaveBeenCalledWith({ stream: null })
    expect(mockApi.getSettings).toHaveBeenCalledTimes(2)
    expect(elMock.ElMessage.success).toHaveBeenCalledOnce()

    wrapper.unmount()
  })

  it('重置录屏：确认后 putSettings({recording: null})', async () => {
    const wrapper = await mountSettings()

    await wrapper.find('[data-test="reset-recording"]').trigger('click')
    await flushPromises()

    expect(mockApi.putSettings).toHaveBeenCalledWith({ recording: null })
    expect(mockApi.getSettings).toHaveBeenCalledTimes(2)

    wrapper.unmount()
  })

  it('取消重置确认：不写入、不弹成功提示', async () => {
    elMock.ElMessageBox.confirm.mockRejectedValue('cancel')
    const wrapper = await mountSettings()

    await wrapper.find('[data-test="reset-stream"]').trigger('click')
    await flushPromises()

    expect(mockApi.putSettings).not.toHaveBeenCalled()
    expect(elMock.ElMessage.success).not.toHaveBeenCalled()

    wrapper.unmount()
  })

  it('加载失败：ElMessage.error 透传后端错误信息', async () => {
    mockApi.getSettings.mockRejectedValue(axiosError('SETTINGS_STORE_ERROR', '读取用户设置失败'))
    const wrapper = await mountSettings()

    expect(elMock.ElMessage.error).toHaveBeenCalledWith('读取用户设置失败')

    wrapper.unmount()
  })

  it('保存失败：ElMessage.error 透传且 loading 复位', async () => {
    mockApi.putSettings.mockRejectedValue(axiosError('SETTINGS_STORE_ERROR', '写入用户设置失败'))
    const wrapper = await mountSettings()

    await wrapper.find('[data-test="save-stream"]').trigger('click')
    await flushPromises()

    expect(elMock.ElMessage.error).toHaveBeenCalledWith('写入用户设置失败')
    expect(elMock.ElMessage.success).not.toHaveBeenCalled()
    expect(wrapper.find('[data-test="save-stream"]').attributes('data-loading')).toBe('false')

    wrapper.unmount()
  })

  // ==================== 设备级参数覆盖卡（方案 45 T8） ====================

  it('?device=d1 进入即聚焦选中该设备（覆盖卡联动渲染）', async () => {
    routeMock.current = { query: { device: 'd1' } }
    const wrapper = await mountSettings()

    const select = wrapper.find('[data-test="override-device-select"]')
    expect(select.exists()).toBe(true)
    expect(select.attributes('data-value')).toBe('d1')
    expect(wrapper.findAll('[data-test="save-override"]').length).toBe(1)
    expect(wrapper.find('[data-test="clear-override"]').exists()).toBe(true)

    wrapper.unmount()
  })

  it('query.device 不在设备列表内 → 不强行选中（保持未选提示）', async () => {
    routeMock.current = { query: { device: 'ghost' } }
    const wrapper = await mountSettings()

    expect(wrapper.find('[data-test="override-device-select"]').attributes('data-value')).toBe('')
    expect(wrapper.text()).toContain('选择设备后可为该设备单独调整串流参数')

    wrapper.unmount()
  })

  it('选设备覆盖回填（覆盖值优先、未覆盖键回落全局），保存 → putSettings 带 devices 键', async () => {
    mockApi.getSettings.mockResolvedValue({
      ...DEFAULT_VIEW,
      devices: { d1: { stream: { max_size: 720, bit_rate: '2M' } } },
    })
    const wrapper = await mountSettings()

    wrapper.getComponent({ name: 'ElSelect' }).vm.$emit('update:modelValue', 'd1')
    await flushPromises()

    const fieldSets = wrapper.findAllComponents(StreamFieldSet)
    expect(fieldSets.length).toBe(2)
    expect(fieldSets[1].props('modelValue')).toEqual({
      max_size: 720,
      bit_rate: '2M',
      fps: 30,
      codec: 'h264',
      adaptive_bitrate: true,
    })

    await wrapper.find('[data-test="save-override"]').trigger('click')
    await flushPromises()

    expect(mockApi.putSettings).toHaveBeenCalledWith({
      devices: { d1: { stream: expect.objectContaining({ max_size: 720, bit_rate: '2M' }) } },
    })
    expect(elMock.ElMessage.success).toHaveBeenCalledWith('已保存，该设备下次连接生效')
    expect(mockApi.getSettings).toHaveBeenCalledTimes(2)

    wrapper.unmount()
  })

  it('清除该设备覆盖：确认后 putSettings devices[id]=null 并重拉设置', async () => {
    routeMock.current = { query: { device: 'd1' } }
    const wrapper = await mountSettings()

    await wrapper.find('[data-test="clear-override"]').trigger('click')
    await flushPromises()

    expect(elMock.ElMessageBox.confirm).toHaveBeenCalledOnce()
    expect(mockApi.putSettings).toHaveBeenCalledWith({ devices: { d1: null } })
    expect(mockApi.getSettings).toHaveBeenCalledTimes(2)

    wrapper.unmount()
  })
})

describe('StreamFieldSet.vue（h265 gating，评审焦点 #4）', () => {
  function mountFieldSet(h265Supported: boolean) {
    return shallowMount(StreamFieldSet, {
      props: { modelValue: { ...DEFAULT_STREAM_FORM }, h265Supported },
      global: { stubs: FORM_STUBS },
    })
  }

  it('不支持时 h265 选项禁用并显示提示，h264 保持可选', () => {
    const wrapper = mountFieldSet(false)

    const h265 = wrapper.find('.stub-option[data-value="h265"]')
    expect(h265.exists()).toBe(true)
    expect(h265.attributes('data-disabled')).toBe('true')
    expect(wrapper.find('.stub-option[data-value="h264"]').attributes('data-disabled')).toBe(
      'false'
    )
    expect(wrapper.text()).toContain('当前浏览器不支持 h265 解码')

    wrapper.unmount()
  })

  it('支持时 h265 可选且无禁用提示', () => {
    const wrapper = mountFieldSet(true)

    expect(wrapper.find('.stub-option[data-value="h265"]').attributes('data-disabled')).toBe(
      'false'
    )
    expect(wrapper.text()).not.toContain('不支持 h265 解码')

    wrapper.unmount()
  })

  it('字段编辑直接写回 v-model 对象（后端键名，免 emit 映射）', async () => {
    const form = { ...DEFAULT_STREAM_FORM }
    const wrapper = shallowMount(StreamFieldSet, {
      props: { modelValue: form, h265Supported: false },
      global: { stubs: FORM_STUBS },
    })

    // 第一个 el-input-number 为 max_size：编辑写回父组件持有的同一对象
    await wrapper.find('.stub-input-number').setValue('720')
    expect(form.max_size).toBe(720)

    // 自适应码率开关写回 adaptive_bitrate
    await wrapper.find('.stub-switch').trigger('click')
    expect(form.adaptive_bitrate).toBe(false)

    wrapper.unmount()
  })
})

describe('RecordingFieldSet.vue', () => {
  it('渲染录屏码率与时长上限，含生效时机提示', () => {
    const wrapper = shallowMount(RecordingFieldSet, {
      props: { modelValue: { bit_rate: '4M', time_limit: 60 } },
      global: { stubs: FORM_STUBS },
    })

    expect(wrapper.text()).toContain('每次开始录屏时读取，保存即对新录屏生效')
    expect((wrapper.find('.stub-input-number').element as HTMLInputElement).value).toBe('60')
    expect(wrapper.find('.stub-select').attributes('data-value')).toBe('4M')

    wrapper.unmount()
  })
})
