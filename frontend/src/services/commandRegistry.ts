/**
 * 命令面板命令注册表（Ctrl+K）
 * ============================
 *
 * 纯逻辑，无 Vue 依赖，副作用通过 deps 注入以便单测。
 *
 * 命令来源（v1）：
 *   导航   — Dashboard、打开各在线设备
 *   设备   — 断开当前设备（仅设备页上下文）
 *   调试   — 切换 DebugPanel 五个 Tab（经 CustomEvent 广播）
 *   视图   — 全屏切换
 */

export interface Command {
  id: string
  title: string
  section: '导航' | '设备' | '调试' | '视图'
  keywords?: string
  run: () => void
}

export interface DeviceLike {
  id: string
  model?: string
  status?: string
}

export interface CommandDeps {
  devices: DeviceLike[]
  currentDeviceId: string | null
  navigate: (path: string) => void
  disconnect: (deviceId: string) => void
  setDebugTab: (tab: string) => void
  toggleFullscreen: () => void
}

const DEBUG_TABS = ['logcat', 'shell', 'network', 'perf', 'apps'] as const

export function buildCommands(deps: CommandDeps): Command[] {
  const cmds: Command[] = []

  cmds.push({
    id: 'nav.dashboard',
    title: '打开 Dashboard（设备列表）',
    section: '导航',
    keywords: 'dashboard home 首页 列表',
    run: () => deps.navigate('/'),
  })

  for (const d of deps.devices) {
    if (d.status && d.status !== 'online') continue
    cmds.push({
      id: `device.open:${d.id}`,
      title: `打开设备 ${d.model || d.id}`,
      section: '设备',
      keywords: `device ${d.id} ${d.model ?? ''}`,
      run: () => deps.navigate(`/device/${encodeURIComponent(d.id)}`),
    })
  }

  if (deps.currentDeviceId) {
    cmds.push({
      id: 'device.disconnect',
      title: '断开当前设备',
      section: '设备',
      keywords: 'disconnect 断开',
      run: () => deps.disconnect(deps.currentDeviceId!),
    })
  }

  for (const tab of DEBUG_TABS) {
    cmds.push({
      id: `debug.tab.${tab}`,
      title: `切换到 ${tab} 面板`,
      section: '调试',
      keywords: `tab ${tab} 面板`,
      run: () => deps.setDebugTab(tab),
    })
  }

  cmds.push({
    id: 'view.fullscreen',
    title: '切换全屏',
    section: '视图',
    keywords: 'fullscreen 全屏',
    run: () => deps.toggleFullscreen(),
  })

  return cmds
}

function score(c: Command, q: string): number {
  const title = c.title.toLowerCase()
  if (title.startsWith(q)) return 3
  if (title.includes(q)) return 2
  const meta = `${c.section} ${c.keywords ?? ''}`.toLowerCase()
  if (meta.includes(q)) return 1
  return 0
}

export function filterCommands(query: string, commands: Command[]): Command[] {
  const q = query.trim().toLowerCase()
  if (!q) return commands.slice()
  return commands
    .map((c, i) => ({ c, i, s: score(c, q) }))
    .filter(x => x.s > 0)
    .sort((a, b) => b.s - a.s || a.i - b.i)
    .map(x => x.c)
}
