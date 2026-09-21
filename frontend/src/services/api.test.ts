/**
 * HTTP API 服务测试
 * ==================
 *
 * mock axios.create() 返回共享假 client，逐一验证 api 各方法的
 * URL / 请求参数 / 返回值透传 / 错误传播。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import axios from 'axios'
import { api } from './api'

const { mockClient } = vi.hoisted(() => ({
  mockClient: {
    get: vi.fn(),
    post: vi.fn(),
    delete: vi.fn(),
  },
}))

vi.mock('axios', () => ({
  default: { create: vi.fn(() => mockClient) },
}))

const OK = { ok: true }
const BLOB = new Blob(['png-bytes'])

beforeEach(() => {
  mockClient.get.mockReset().mockResolvedValue({ data: OK })
  mockClient.post.mockReset().mockResolvedValue({ data: OK })
  mockClient.delete.mockReset().mockResolvedValue({ data: OK })
})

describe('axios 实例与设备 API', () => {
  it('axios 实例使用 /api baseURL 与 30s 超时', () => {
    expect(vi.mocked(axios.create)).toHaveBeenCalledWith({ baseURL: '/api', timeout: 30000 })
  })

  it('listDevices 请求 /devices 并透传 data', async () => {
    await expect(api.listDevices()).resolves.toBe(OK)
    expect(mockClient.get).toHaveBeenCalledWith('/devices')
  })

  it('getDevice 请求 /devices/{id}', async () => {
    await api.getDevice('192.168.8.18:5555')
    expect(mockClient.get).toHaveBeenCalledWith('/devices/192.168.8.18:5555')
  })

  it('installApk 以 query 参数传 apk_path', async () => {
    await api.installApk('dev1', '/tmp/app.apk')
    expect(mockClient.post).toHaveBeenCalledWith('/devices/dev1/install', null, {
      params: { apk_path: '/tmp/app.apk' },
    })
  })

  it('connectDevice 默认端口 5555，超时 40s', async () => {
    await api.connectDevice('10.0.0.8')
    expect(mockClient.post).toHaveBeenCalledWith('/devices/connect', null, {
      params: { ip: '10.0.0.8', port: 5555 },
      timeout: 40000,
    })
  })

  it('connectDevice 支持自定义端口', async () => {
    await api.connectDevice('10.0.0.8', 4444)
    expect(mockClient.post).toHaveBeenCalledWith('/devices/connect', null, {
      params: { ip: '10.0.0.8', port: 4444 },
      timeout: 40000,
    })
  })

  it('disconnectDevice 对 deviceId 做 URL 编码', async () => {
    await api.disconnectDevice('192.168.8.18:5555')
    expect(mockClient.post).toHaveBeenCalledWith(
      '/devices/192.168.8.18%3A5555/disconnect',
      null,
      { timeout: 40000 },
    )
  })
})

describe('调试 API', () => {
  it('createDebugSession 传 device_id 与 user_id', async () => {
    await api.createDebugSession('dev1', 'user1')
    expect(mockClient.post).toHaveBeenCalledWith('/debug/sessions', null, {
      params: { device_id: 'dev1', user_id: 'user1' },
    })
  })

  it('getDebugSession 请求会话详情', async () => {
    await api.getDebugSession('s1')
    expect(mockClient.get).toHaveBeenCalledWith('/debug/sessions/s1')
  })

  it('getLogs 默认 level/tag 为空、limit=1000', async () => {
    await api.getLogs('s1')
    expect(mockClient.get).toHaveBeenCalledWith('/debug/sessions/s1/logs', {
      params: { level: undefined, tag: undefined, limit: 1000 },
    })
  })

  it('getLogs 透传过滤条件', async () => {
    await api.getLogs('s1', 'ERROR', 'Main', 50)
    expect(mockClient.get).toHaveBeenCalledWith('/debug/sessions/s1/logs', {
      params: { level: 'ERROR', tag: 'Main', limit: 50 },
    })
  })

  it('exportLogs 默认 json 格式、blob 响应，返回 Blob', async () => {
    mockClient.get.mockResolvedValueOnce({ data: BLOB })
    await expect(api.exportLogs('s1')).resolves.toBe(BLOB)
    expect(mockClient.get).toHaveBeenCalledWith('/debug/sessions/s1/logs/export', {
      params: { format: 'json', level: undefined, tag: undefined, limit: 50000 },
      responseType: 'blob',
    })
  })

  it('exportLogs 支持 csv 与过滤条件', async () => {
    mockClient.get.mockResolvedValueOnce({ data: BLOB })
    await api.exportLogs('s1', 'csv', 'WARN', 'Tag', 10)
    expect(mockClient.get).toHaveBeenCalledWith('/debug/sessions/s1/logs/export', {
      params: { format: 'csv', level: 'WARN', tag: 'Tag', limit: 10 },
      responseType: 'blob',
    })
  })

  it('runCleanup 触发手动清理', async () => {
    await api.runCleanup()
    expect(mockClient.post).toHaveBeenCalledWith('/debug/cleanup')
  })

  it('cleanupSessionLogs 用 DELETE 清理会话日志', async () => {
    await api.cleanupSessionLogs('s1')
    expect(mockClient.delete).toHaveBeenCalledWith('/debug/sessions/s1/logs')
  })

  it('getDebugStats 请求 /debug/stats', async () => {
    await api.getDebugStats()
    expect(mockClient.get).toHaveBeenCalledWith('/debug/stats')
  })

  it('execShell 以 query 参数传 command', async () => {
    await api.execShell('s1', 'ls -l')
    expect(mockClient.post).toHaveBeenCalledWith('/debug/sessions/s1/shell', null, {
      params: { command: 'ls -l' },
    })
  })

  it('closeDebugSession 用 DELETE 关闭会话', async () => {
    await api.closeDebugSession('s1')
    expect(mockClient.delete).toHaveBeenCalledWith('/debug/sessions/s1')
  })
})

describe('截屏与性能 API', () => {
  it('screenshot 以 blob 响应返回 Blob', async () => {
    mockClient.get.mockResolvedValueOnce({ data: BLOB })
    await expect(api.screenshot('dev1')).resolves.toBe(BLOB)
    expect(mockClient.get).toHaveBeenCalledWith('/devices/dev1/screenshot', {
      responseType: 'blob',
    })
  })

  it('getPerfMetrics 编码 deviceId 且 limit 默认 100', async () => {
    await api.getPerfMetrics('192.168.8.18:5555')
    expect(mockClient.get).toHaveBeenCalledWith('/perf/192.168.8.18%3A5555/metrics', {
      params: { limit: 100 },
    })
  })

  it('startPerfMonitoring 采样间隔默认 1.0s', async () => {
    await api.startPerfMonitoring('dev1')
    expect(mockClient.post).toHaveBeenCalledWith('/perf/dev1/start', null, {
      params: { interval: 1.0 },
    })
  })

  it('startPerfMonitoring 支持自定义间隔', async () => {
    await api.startPerfMonitoring('dev1', 0.5)
    expect(mockClient.post).toHaveBeenCalledWith('/perf/dev1/start', null, {
      params: { interval: 0.5 },
    })
  })

  it('stopPerfMonitoring 停止监控', async () => {
    await api.stopPerfMonitoring('dev1')
    expect(mockClient.post).toHaveBeenCalledWith('/perf/dev1/stop')
  })
})

describe('应用管理 API', () => {
  it('listApps 默认不含系统应用', async () => {
    await api.listApps('dev1')
    expect(mockClient.get).toHaveBeenCalledWith('/apps/dev1', {
      params: { include_system: false },
    })
  })

  it('listApps 可包含系统应用', async () => {
    await api.listApps('dev1', true)
    expect(mockClient.get).toHaveBeenCalledWith('/apps/dev1', {
      params: { include_system: true },
    })
  })

  it('getAppInfo 请求应用详情', async () => {
    await api.getAppInfo('dev1', 'com.example.app')
    expect(mockClient.get).toHaveBeenCalledWith('/apps/dev1/com.example.app')
  })

  it('launchApp / stopApp 请求对应端点', async () => {
    await api.launchApp('dev1', 'com.example.app')
    expect(mockClient.post).toHaveBeenCalledWith('/apps/dev1/com.example.app/launch')
    await api.stopApp('dev1', 'com.example.app')
    expect(mockClient.post).toHaveBeenCalledWith('/apps/dev1/com.example.app/stop')
  })

  it('uninstallApp / clearAppData 请求对应端点', async () => {
    await api.uninstallApp('dev1', 'com.example.app')
    expect(mockClient.post).toHaveBeenCalledWith('/apps/dev1/com.example.app/uninstall')
    await api.clearAppData('dev1', 'com.example.app')
    expect(mockClient.post).toHaveBeenCalledWith('/apps/dev1/com.example.app/clear-data')
  })

  it('getAppMemory 请求内存占用', async () => {
    await api.getAppMemory('dev1', 'com.example.app')
    expect(mockClient.get).toHaveBeenCalledWith('/apps/dev1/com.example.app/memory')
  })
})

describe('网络监控 API 与错误传播', () => {
  it('getNetworkStats 请求统计端点', async () => {
    await api.getNetworkStats('dev1')
    expect(mockClient.get).toHaveBeenCalledWith('/network/dev1/stats')
  })

  it('getNetworkConnections 默认无协议过滤', async () => {
    await api.getNetworkConnections('dev1')
    expect(mockClient.get).toHaveBeenCalledWith('/network/dev1/connections', {
      params: { protocol: undefined },
    })
  })

  it('getNetworkConnections 支持协议过滤', async () => {
    await api.getNetworkConnections('dev1', 'tcp')
    expect(mockClient.get).toHaveBeenCalledWith('/network/dev1/connections', {
      params: { protocol: 'tcp' },
    })
  })

  it('底层请求失败时错误向上抛出', async () => {
    mockClient.get.mockRejectedValueOnce(new Error('network down'))
    await expect(api.listDevices()).rejects.toThrow('network down')
  })

  it('post/delete 失败同样向上抛出', async () => {
    mockClient.post.mockRejectedValueOnce(new Error('post failed'))
    await expect(api.runCleanup()).rejects.toThrow('post failed')
    mockClient.delete.mockRejectedValueOnce(new Error('delete failed'))
    await expect(api.closeDebugSession('s1')).rejects.toThrow('delete failed')
  })
})