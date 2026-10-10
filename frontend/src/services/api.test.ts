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

  it('scanSubnet 以 query 参数传 cidr/connect/port，超时 60s（方案 36 + 终评 I-1 上界）', async () => {
    await api.scanSubnet('192.168.8.0/24')
    expect(mockClient.post).toHaveBeenCalledWith('/devices/scan', null, {
      params: { cidr: '192.168.8.0/24', connect: true, port: 5555 },
      timeout: 60000,
    })
  })

  it('scanSubnet 支持自定义 connect/port 并透传 ScanResult', async () => {
    const scanResult = {
      cidr: '10.0.0.0/24',
      probed: 254,
      open_hosts: ['10.0.0.5'],
      connect_results: [
        { ip: '10.0.0.5', ok: true, device_id: '10.0.0.5:4444', reason: null, message: null },
      ],
      truncated: false,
    }
    mockClient.post.mockResolvedValueOnce({ data: scanResult })
    await expect(api.scanSubnet('10.0.0.0/24', false, 4444)).resolves.toBe(scanResult)
    expect(mockClient.post).toHaveBeenCalledWith('/devices/scan', null, {
      params: { cidr: '10.0.0.0/24', connect: false, port: 4444 },
      timeout: 60000,
    })
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

  it('screenshotRaw 带 format=raw 参数并解析 x-frame-format 头（方案 29）', async () => {
    mockClient.get.mockResolvedValueOnce({
      data: BLOB,
      headers: { 'x-frame-format': 'raw-rgba' },
    })
    await expect(api.screenshotRaw('dev1')).resolves.toEqual({
      blob: BLOB,
      format: 'raw-rgba',
    })
    expect(mockClient.get).toHaveBeenCalledWith('/devices/dev1/screenshot', {
      params: { format: 'raw' },
      responseType: 'blob',
    })
  })

  it('screenshotRaw 后端回退 png 头 → format 为 png', async () => {
    mockClient.get.mockResolvedValueOnce({
      data: BLOB,
      headers: { 'x-frame-format': 'png' },
    })
    await expect(api.screenshotRaw('dev1')).resolves.toEqual({
      blob: BLOB,
      format: 'png',
    })
  })

  it('screenshotRaw 响应头缺失 → 按 png 处理（防中间层吞头）', async () => {
    // 真实 axios 响应必有 headers 对象；吞头场景 = headers 存在但自定义头缺席
    mockClient.get.mockResolvedValueOnce({ data: BLOB, headers: {} })
    await expect(api.screenshotRaw('dev1')).resolves.toEqual({
      blob: BLOB,
      format: 'png',
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

describe('系统 API（方案 23 T2）', () => {
  it('shutdownSystem 请求 POST /system/shutdown 并透传结果', async () => {
    mockClient.post.mockResolvedValue({ data: { accepted: true, message: '服务正在退出' } })
    const result = await api.shutdownSystem()
    expect(mockClient.post).toHaveBeenCalledWith('/system/shutdown')
    expect(result.accepted).toBe(true)
  })

  it('getHealth 以空 baseURL 越过 /api 前缀请求 /health', async () => {
    mockClient.get.mockResolvedValue({ data: { status: 'ok' } })
    const result = await api.getHealth()
    expect(mockClient.get).toHaveBeenCalledWith('/health', { baseURL: '', timeout: 2000 })
    expect(result.status).toBe('ok')
  })
})