/**
 * AppsView 组件测试
 * ==================
 *
 * 覆盖：
 *   - 挂载：默认「用户应用」筛选只请求用户应用（include_system=false），状态栏统计正确
 *   - 筛选切换「全部应用」：自动补拉系统应用（include_system=true），命中缓存后不重复请求
 *   - 行点击打开应用详情对话框并拉取 getAppInfo
 *   - 点击「刷新」强制重新拉取
 *
 * 说明：Element Plus 的 el-* 组件用轻量 stub 替代（项目测试不引入 ElementPlus），
 * 其中 el-select/el-dialog/el-table 提供最小交互能力以便断言。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, shallowMount } from '@vue/test-utils'

const h = vi.hoisted(() => ({
  ElMessage: { success: vi.fn(), error: vi.fn() },
  ElMessageBox: { confirm: vi.fn() },
}))
vi.mock('element-plus', () => ({
  ElMessage: h.ElMessage,
  ElMessageBox: h.ElMessageBox,
}))

const mockApi = vi.hoisted(() => ({
  listApps: vi.fn(),
  getAppInfo: vi.fn(),
  getAppRuntime: vi.fn(),
  launchApp: vi.fn().mockResolvedValue({}),
  stopApp: vi.fn().mockResolvedValue({}),
}))
vi.mock('@/services/api', () => ({ api: mockApi }))

import AppsView from './AppsView.vue'

const userApp = {
  package_name: 'com.example.chat',
  version_name: '1.2.3',
  version_code: 12,
  install_time: '2026-01-01 10:00',
  update_time: '2026-02-01 10:00',
  apk_size_mb: 12.5,
  is_system: false,
  is_running: true,
  pid: 123,
  memory_kb: 204800,
}

const systemApp = {
  package_name: 'com.android.settings',
  version_name: '14',
  version_code: 34,
  install_time: '',
  update_time: '',
  apk_size_mb: 30,
  is_system: true,
  is_running: false,
  pid: null,
  memory_kb: null,
}

const ElButtonStub = {
  name: 'ElButton',
  template: '<button class="stub-btn" type="button"><slot /></button>',
}

const ElSelectStub = {
  name: 'ElSelect',
  props: ['modelValue'],
  emits: ['update:modelValue'],
  template: '<div class="stub-select"><slot /></div>',
}

const ElDialogStub = {
  name: 'ElDialog',
  props: ['modelValue', 'title'],
  template:
    '<div v-if="modelValue" class="stub-dialog"><div class="stub-dialog-title">{{ title }}</div><slot /></div>',
}

const ElTableStub = {
  name: 'ElTable',
  props: ['data'],
  emits: ['row-click'],
  template:
    '<div class="stub-table"><div v-for="(row, i) in data || []" :key="i" class="stub-table-row">{{ row.package_name || row.name }} {{ row.state || "" }}</div><slot /></div>',
}

const ElAlertStub = {
  name: 'ElAlert',
  props: ['type', 'title', 'description'],
  template: '<div class="stub-alert">{{ title }}{{ description }}</div>',
}

const ElDescriptionsStub = {
  name: 'ElDescriptions',
  props: ['column', 'border', 'size'],
  template: '<div class="stub-descriptions"><slot /></div>',
}

const ElDescriptionsItemStub = {
  name: 'ElDescriptionsItem',
  props: ['label', 'span'],
  template: '<div class="stub-descriptions-item">{{ label }}<slot /></div>',
}

function mountView() {
  return shallowMount(AppsView, {
    props: { deviceId: 'dev1' },
    global: {
      stubs: {
        'el-button': ElButtonStub,
        'el-select': ElSelectStub,
        'el-dialog': ElDialogStub,
        'el-table': ElTableStub,
        'el-input': true,
        'el-option': true,
        'el-table-column': true,
        'el-tag': true,
        'el-dropdown': true,
        'el-dropdown-menu': true,
        'el-dropdown-item': true,
        'el-descriptions': ElDescriptionsStub,
        'el-descriptions-item': ElDescriptionsItemStub,
        'el-alert': ElAlertStub,
      },
      directives: { loading: {} },
    },
  })
}

beforeEach(() => {
  mockApi.listApps.mockReset()
  mockApi.getAppInfo.mockReset()
  mockApi.getAppRuntime.mockReset()
  mockApi.launchApp.mockClear()
  mockApi.stopApp.mockClear()
  h.ElMessage.success.mockClear()
  h.ElMessage.error.mockClear()
})

describe('AppsView', () => {
  it('挂载时只请求用户应用并渲染工具栏与状态栏', async () => {
    mockApi.listApps.mockResolvedValue({ apps: [userApp, systemApp] })
    const wrapper = mountView()

    expect(mockApi.listApps).toHaveBeenCalledWith('dev1', false)

    await flushPromises()

    // 工具栏：搜索框 + 刷新按钮
    expect(wrapper.find('el-input-stub').exists()).toBe(true)
    expect(wrapper.findAll('button').map((b) => b.text())).toContain('刷新')

    // 默认筛选 user：过滤掉系统应用，状态栏显示 1 / 2
    const status = wrapper.find('.status-bar').text()
    expect(status).toContain('显示 1 / 2 个应用')
    expect(status).toContain('1 个运行中')
  })

  it('切换到「全部应用」补拉系统应用，退回后命中缓存不再请求', async () => {
    mockApi.listApps.mockResolvedValue({ apps: [userApp, systemApp] })
    const wrapper = mountView()
    await flushPromises()

    const select = wrapper.findComponent({ name: 'ElSelect' })
    select.vm.$emit('update:modelValue', 'all')
    await flushPromises()

    expect(mockApi.listApps).toHaveBeenCalledTimes(2)
    expect(mockApi.listApps).toHaveBeenLastCalledWith('dev1', true)
    expect(wrapper.find('.status-bar').text()).toContain('共 2 个应用')

    // 退回用户应用：已有全量缓存，不应再次请求
    select.vm.$emit('update:modelValue', 'user')
    await flushPromises()

    expect(mockApi.listApps).toHaveBeenCalledTimes(2)
    expect(wrapper.find('.status-bar').text()).toContain('显示 1 / 2 个应用')
  })

  it('点击表格行打开应用详情对话框并请求应用详情', async () => {
    mockApi.listApps.mockResolvedValue({ apps: [userApp, systemApp] })
    mockApi.getAppInfo.mockResolvedValue({ ...userApp, pid: 999, memory_kb: 1024, apk_size_mb: 20 })
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.find('.stub-dialog').exists()).toBe(false)

    wrapper.findComponent({ name: 'ElTable' }).vm.$emit('row-click', userApp)
    await flushPromises()

    expect(mockApi.getAppInfo).toHaveBeenCalledWith('dev1', 'com.example.chat')
    const dialog = wrapper.find('.stub-dialog')
    expect(dialog.exists()).toBe(true)
    expect(dialog.find('.stub-dialog-title').text()).toBe('应用详情')
    expect(dialog.text()).toContain('基本信息')
    expect(dialog.text()).toContain('运行信息')
    // 详情操作区的「清除数据 / 卸载应用」按钮
    const detailButtons = dialog.findAll('button').map((b) => b.text())
    expect(detailButtons).toContain('清除数据')
    expect(detailButtons).toContain('卸载应用')
  })

  // ==================== 方案 37 C5：运行时区块 ====================

  const cleanRuntime = {
    pkg: 'com.example.chat',
    top_activity: 'com.example.chat/.ChatActivity',
    resumed: 'com.example.chat/.ChatActivity',
    services: [
      { name: 'com.example.chat/.ChatService', state: 'running' },
      { name: 'com.example.chat/.PushService', state: 'stopped' },
    ],
    providers: ['com.example.chat/.ChatProvider'],
    parse_warning: '',
  }

  /** 挂载 → 等待列表 → 行点击打开详情弹窗 → 等待并行拉取完成 */
  async function openDetail(wrapper: ReturnType<typeof mountView>) {
    await flushPromises()
    wrapper.findComponent({ name: 'ElTable' }).vm.$emit('row-click', userApp)
    await flushPromises()
  }

  it('renders runtime card on selecting an app', async () => {
    mockApi.listApps.mockResolvedValue({ apps: [userApp] })
    mockApi.getAppInfo.mockResolvedValue({ ...userApp })
    mockApi.getAppRuntime.mockResolvedValue(cleanRuntime)
    const wrapper = mountView()
    await openDetail(wrapper)

    expect(mockApi.getAppRuntime).toHaveBeenCalledWith('dev1', 'com.example.chat')
    const dialog = wrapper.find('.stub-dialog')
    expect(dialog.text()).toContain('运行时')
    // 当前栈 / Resumed Activity 值渲染
    expect(dialog.text()).toContain('com.example.chat/.ChatActivity')
    // Service 表行存在（组件名 + 状态）
    expect(dialog.text()).toContain('com.example.chat/.ChatService running')
    expect(dialog.text()).toContain('com.example.chat/.PushService stopped')
    // providers 不渲染（YAGNI，数据仅留在 api 返回类型里）
    expect(dialog.text()).not.toContain('ChatProvider')
  })

  it('shows parse_warning alert when returned', async () => {
    mockApi.listApps.mockResolvedValue({ apps: [userApp] })
    mockApi.getAppInfo.mockResolvedValue({ ...userApp })
    mockApi.getAppRuntime.mockResolvedValue({ ...cleanRuntime, parse_warning: 'providers 段解析失败' })
    const wrapper = mountView()
    await openDetail(wrapper)

    const alert = wrapper.find('.stub-alert')
    expect(alert.exists()).toBe(true)
    expect(alert.text()).toContain('部分运行时字段解析缺失：providers 段解析失败')
  })

  it('refresh button re-queries and updates table', async () => {
    mockApi.listApps.mockResolvedValue({ apps: [userApp] })
    mockApi.getAppInfo.mockResolvedValue({ ...userApp })
    mockApi.getAppRuntime.mockResolvedValue(cleanRuntime)
    const wrapper = mountView()
    await openDetail(wrapper)
    expect(mockApi.getAppRuntime).toHaveBeenCalledTimes(1)

    mockApi.getAppRuntime.mockResolvedValue({
      ...cleanRuntime,
      resumed: 'com.example.chat/.NewActivity',
      services: [{ name: 'com.example.chat/.NewService', state: 'running' }],
    })
    const refreshBtn = wrapper
      .findAll('.stub-dialog button')
      .find((b) => b.text().includes('刷新运行时'))
    expect(refreshBtn).toBeDefined()
    await refreshBtn!.trigger('click')
    await flushPromises()

    expect(mockApi.getAppRuntime).toHaveBeenCalledTimes(2)
    const dialog = wrapper.find('.stub-dialog')
    expect(dialog.text()).toContain('com.example.chat/.NewActivity')
    expect(dialog.text()).toContain('com.example.chat/.NewService')
    expect(dialog.text()).not.toContain('com.example.chat/.ChatService')
  })

  it('shows inline failure hint when runtime query rejects', async () => {
    mockApi.listApps.mockResolvedValue({ apps: [userApp] })
    mockApi.getAppInfo.mockResolvedValue({ ...userApp })
    mockApi.getAppRuntime.mockRejectedValue(new Error('boom'))
    const wrapper = mountView()
    await openDetail(wrapper)

    const dialog = wrapper.find('.stub-dialog')
    expect(dialog.text()).toContain('运行时查询失败')
    // Service 空态：无表行渲染
    expect(dialog.findAll('.stub-dialog .stub-table-row').length).toBe(0)
    // 不打 ElMessage（与 detailLoading 内联风格一致）
    expect(h.ElMessage.error).not.toHaveBeenCalled()
  })

  it('点击刷新按钮强制重新拉取列表', async () => {
    mockApi.listApps.mockResolvedValue({ apps: [userApp] })
    const wrapper = mountView()
    await flushPromises()
    expect(mockApi.listApps).toHaveBeenCalledTimes(1)

    const refreshBtn = wrapper.findAll('button').find((b) => b.text().includes('刷新'))
    expect(refreshBtn).toBeDefined()
    await refreshBtn!.trigger('click')
    await flushPromises()

    expect(mockApi.listApps).toHaveBeenCalledTimes(2)
    expect(mockApi.listApps).toHaveBeenLastCalledWith('dev1', false)
  })
})