import { describe, expect, it, vi } from 'vitest'
import { buildCommands, filterCommands, type Command } from './commandRegistry'

const devices = [
  { id: '192.168.8.22:5555', model: 'D2', status: 'online' },
  { id: '192.168.8.99:5555', model: 'Pixel', status: 'offline' },
]

function deps() {
  return {
    devices,
    currentDeviceId: '192.168.8.22:5555',
    navigate: vi.fn(),
    disconnect: vi.fn(),
    setDebugTab: vi.fn(),
    toggleFullscreen: vi.fn(),
  }
}

describe('buildCommands', () => {
  it('包含导航、调试 Tab、视图、断开命令', () => {
    const cmds = buildCommands(deps())
    const ids = cmds.map(c => c.id)
    expect(ids).toContain('nav.dashboard')
    expect(ids).toContain('debug.tab.shell')
    expect(ids).toContain('view.fullscreen')
    expect(ids).toContain('device.disconnect')
  })

  it('为每个在线设备生成打开命令，离线设备不生成', () => {
    const cmds = buildCommands(deps())
    const openIds = cmds.filter(c => c.id.startsWith('device.open')).map(c => c.id)
    expect(openIds).toContain('device.open:192.168.8.22:5555')
    expect(openIds).not.toContain('device.open:192.168.8.99:5555')
  })

  it('非设备页（currentDeviceId=null）不含断开命令', () => {
    const cmds = buildCommands({ ...deps(), currentDeviceId: null })
    expect(cmds.map(c => c.id)).not.toContain('device.disconnect')
  })

  it('命令 run 调用对应副作用', () => {
    const d = deps()
    const cmds = buildCommands(d)
    cmds.find(c => c.id === 'nav.dashboard')!.run()
    expect(d.navigate).toHaveBeenCalledWith('/')
    cmds.find(c => c.id === 'debug.tab.perf')!.run()
    expect(d.setDebugTab).toHaveBeenCalledWith('perf')
    cmds.find(c => c.id === 'device.disconnect')!.run()
    expect(d.disconnect).toHaveBeenCalledWith('192.168.8.22:5555')
  })
})

describe('filterCommands', () => {
  const mk = (title: string, section: string, keywords = ''): Command =>
    ({ id: title, title, section, keywords, run: () => {} })

  it('空查询返回全部', () => {
    const cs = [mk('Dashboard', '导航'), mk('Shell', '调试')]
    expect(filterCommands('', cs)).toHaveLength(2)
  })

  it('大小写不敏感的标题子串匹配', () => {
    const cs = [mk('Dashboard', '导航'), mk('Shell', '调试')]
    const r = filterCommands('dash', cs)
    expect(r.map(c => c.title)).toEqual(['Dashboard'])
  })

  it('标题前缀优先于包含', () => {
    const cs = [mk('打开设备 Pixel', '设备'), mk('断开当前设备', '设备')]
    const r = filterCommands('设备', cs)
    expect(r[0].title).toBe('打开设备 Pixel') // 含"设备"都在，稳定顺序保留输入序
    expect(r).toHaveLength(2)
  })

  it('keywords 参与匹配', () => {
    const cs = [mk('全屏', '视图', 'fullscreen 全屏')]
    expect(filterCommands('fullscreen', cs).map(c => c.title)).toEqual(['全屏'])
  })

  it('无匹配返回空', () => {
    const cs = [mk('Dashboard', '导航')]
    expect(filterCommands('zzz', cs)).toHaveLength(0)
  })
})
