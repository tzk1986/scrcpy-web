/**
 * device store 测试
 * ===================
 *
 * 覆盖：
 *   - fetchDevices：成功覆盖列表、失败写入 error（loading 的翻转）
 *   - connectDevice：成功（默认/自定义端口）后延时刷新、success=false 不刷新、异常向上抛
 *   - disconnectDevice：成功先本地移除再重拉列表、success=false 保持原状、异常向上抛
 *   - startSSE/stopSSE：URL 组装、connected 去重添加、disconnected 移除、非法 JSON/onerror
 *   - installApk / getDevice（含失败返回 null）
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useDeviceStore, type DeviceInfo } from '@/stores/device'

const mockApi = vi.hoisted(() => ({
  listDevices: vi.fn(),
  connectDevice: vi.fn(),
  disconnectDevice: vi.fn(),
  installApk: vi.fn(),
  getDevice: vi.fn(),
}))
vi.mock('@/services/api', () => ({ api: mockApi }))

const h = vi.hoisted(() => {
  class FakeEventSource {
    static instances: FakeEventSource[] = []
    url: string
    onmessage: ((event: { data: string }) => void) | null = null
    onerror: (() => void) | null = null
    close = vi.fn()

    constructor(url: string) {
      this.url = url
      FakeEventSource.instances.push(this)
    }
  }
  FakeEventSource.instances = []
  return { FakeEventSource }
})

vi.stubGlobal('EventSource', h.FakeEventSource)

function dev(id: string, extra: Partial<DeviceInfo> = {}): DeviceInfo {
  return {
    id,
    model: 'Pixel 8',
    os_version: '14',
    resolution: [1080, 2400],
    battery: 90,
    status: 'device',
    ...extra,
  }
}

function lastEventSource() {
  return h.FakeEventSource.instances.at(-1)!
}

beforeEach(() => {
  setActivePinia(createPinia())
  h.FakeEventSource.instances = []
  mockApi.listDevices.mockClear().mockResolvedValue([])
  mockApi.connectDevice.mockClear().mockResolvedValue({ success: true, device_id: 'd' })
  mockApi.disconnectDevice.mockClear().mockResolvedValue({ success: true })
  mockApi.installApk.mockClear().mockResolvedValue({ success: true })
  mockApi.getDevice.mockClear().mockResolvedValue(null)
  vi.spyOn(console, 'error').mockImplementation(() => {})
})

afterEach(() => {
  vi.mocked(console.error).mockRestore()
})

describe('device store - fetchDevices', () => {
  it('成功时覆盖设备列表并复位 loading/error', async () => {
    const store = useDeviceStore()
    store.error = 'stale'
    mockApi.listDevices.mockResolvedValueOnce([dev('a'), dev('b', { ip: '10.0.0.2' })])

    const p = store.fetchDevices()
    expect(store.loading).toBe(true)
    await p

    expect(store.devices.map((d) => d.id)).toEqual(['a', 'b'])
    expect(store.loading).toBe(false)
    expect(store.error).toBeNull()
  })

  it('失败时写入 error 且 loading 复位（不抛出）', async () => {
    const store = useDeviceStore()
    mockApi.listDevices.mockRejectedValueOnce(new Error('adb down'))

    await expect(store.fetchDevices()).resolves.toBeUndefined()
    expect(store.error).toBe('adb down')
    expect(store.loading).toBe(false)
    expect(store.devices).toEqual([])
  })

  it('silent 刷新全程不置 loading，正常覆盖列表', async () => {
    const store = useDeviceStore()
    let resolveList!: (value: DeviceInfo[]) => void
    mockApi.listDevices.mockImplementationOnce(
      () =>
        new Promise<DeviceInfo[]>((resolve) => {
          resolveList = resolve
        })
    )

    const p = store.fetchDevices({ silent: true })
    expect(store.loading).toBe(false) // 发起后：silent 不置 loading

    resolveList([dev('a')])
    await p
    expect(store.loading).toBe(false)
    expect(store.devices.map((d) => d.id)).toEqual(['a'])
  })

  it('过期响应被代次检查丢弃：慢响应不覆盖新代次结果', async () => {
    const store = useDeviceStore()
    let resolveSlow!: (value: DeviceInfo[]) => void
    let resolveFast!: (value: DeviceInfo[]) => void
    mockApi.listDevices
      .mockImplementationOnce(
        () =>
          new Promise<DeviceInfo[]>((resolve) => {
            resolveSlow = resolve
          })
      )
      .mockImplementationOnce(
        () =>
          new Promise<DeviceInfo[]>((resolve) => {
            resolveFast = resolve
          })
      )

    const p1 = store.fetchDevices() // 旧代次（慢）
    const p2 = store.fetchDevices() // 新代次（快）

    resolveFast([dev('fresh')])
    await p2
    expect(store.devices.map((d) => d.id)).toEqual(['fresh'])

    resolveSlow([dev('stale')]) // 慢响应此刻才到，应被丢弃
    await p1
    expect(store.devices.map((d) => d.id)).toEqual(['fresh'])
  })
})

describe('device store - connectDevice', () => {
  it('连接成功后静默重拉设备列表（默认端口 5555）', async () => {
    const store = useDeviceStore()
    mockApi.connectDevice.mockResolvedValueOnce({ success: true, device_id: '1.2.3.4:5555' })
    mockApi.listDevices.mockResolvedValueOnce([dev('1.2.3.4:5555')])

    const result = await store.connectDevice('1.2.3.4')

    expect(result).toEqual({ success: true, device_id: '1.2.3.4:5555' })
    expect(mockApi.connectDevice).toHaveBeenCalledWith('1.2.3.4', 5555)
    await vi.waitFor(() => expect(store.devices.map((d) => d.id)).toEqual(['1.2.3.4:5555']))
    expect(mockApi.listDevices).toHaveBeenCalledTimes(1)
    expect(store.error).toBeNull()
  })

  it('重拉不阻塞连接返回：列表挂起时仍立即返回成功', async () => {
    const store = useDeviceStore()
    mockApi.connectDevice.mockResolvedValueOnce({ success: true, device_id: '1.2.3.4:5555' })
    mockApi.listDevices.mockImplementation(() => new Promise<DeviceInfo[]>(() => {}))

    let deadline: ReturnType<typeof setTimeout> | undefined
    try {
      const result = await Promise.race([
        store.connectDevice('1.2.3.4'),
        new Promise<never>((_, reject) => {
          deadline = setTimeout(() => reject(new Error('列表刷新阻塞了连接返回')), 500)
        }),
      ])

      expect(result).toEqual({ success: true, device_id: '1.2.3.4:5555' })
      expect(mockApi.listDevices).toHaveBeenCalled() // 静默重拉已发起
    } finally {
      clearTimeout(deadline)
    }
  })

  it('自定义端口透传给 API', async () => {
    const store = useDeviceStore()
    await store.connectDevice('10.0.0.5', 5037)
    expect(mockApi.connectDevice).toHaveBeenCalledWith('10.0.0.5', 5037)
  })

  it('success=false 时返回结果但不刷新列表', async () => {
    const store = useDeviceStore()
    mockApi.connectDevice.mockResolvedValueOnce({ success: false, device_id: '' })

    const result = await store.connectDevice('10.0.0.9')
    expect(result).toEqual({ success: false, device_id: '' })
    expect(mockApi.listDevices).not.toHaveBeenCalled()
  })

  it('异常时写入 error 并向上抛出', async () => {
    const store = useDeviceStore()
    mockApi.connectDevice.mockRejectedValueOnce(new Error('connect timeout'))

    await expect(store.connectDevice('10.0.0.9')).rejects.toThrow('connect timeout')
    expect(store.error).toBe('connect timeout')
  })
})

describe('device store - disconnectDevice', () => {
  it('成功后先本地移除再重拉列表（列表返回前已生效）', async () => {
    const store = useDeviceStore()
    store.devices = [dev('a'), dev('b')]
    let resolveList!: (value: DeviceInfo[]) => void
    mockApi.listDevices.mockImplementationOnce(
      () =>
        new Promise<DeviceInfo[]>((resolve) => {
          resolveList = resolve
        })
    )

    const p = store.disconnectDevice('a')
    await vi.waitFor(() => expect(mockApi.listDevices).toHaveBeenCalled())
    // adb 侧表项可能短暂残留，重拉未返回前应已本地移除
    expect(store.devices.map((d) => d.id)).toEqual(['b'])
    expect(mockApi.disconnectDevice).toHaveBeenCalledWith('a')

    resolveList([dev('b'), dev('c')])
    await p
    expect(store.devices.map((d) => d.id)).toEqual(['b', 'c'])
  })

  it('重拉不阻塞断开返回：列表挂起时仍立即返回成功且本地已移除', async () => {
    const store = useDeviceStore()
    store.devices = [dev('a'), dev('b')]
    mockApi.listDevices.mockImplementation(() => new Promise<DeviceInfo[]>(() => {}))

    let deadline: ReturnType<typeof setTimeout> | undefined
    try {
      const result = await Promise.race([
        store.disconnectDevice('a'),
        new Promise<never>((_, reject) => {
          deadline = setTimeout(() => reject(new Error('列表刷新阻塞了断开返回')), 500)
        }),
      ])

      expect(result).toEqual({ success: true })
      expect(store.devices.map((d) => d.id)).toEqual(['b']) // 本地移除已生效
      expect(mockApi.listDevices).toHaveBeenCalled() // 静默重拉已发起
    } finally {
      clearTimeout(deadline)
    }
  })

  it('success=false 时保持列表不变且不重拉', async () => {
    const store = useDeviceStore()
    store.devices = [dev('a')]
    mockApi.disconnectDevice.mockResolvedValueOnce({ success: false })

    const result = await store.disconnectDevice('a')
    expect(result).toEqual({ success: false })
    expect(store.devices.map((d) => d.id)).toEqual(['a'])
    expect(mockApi.listDevices).not.toHaveBeenCalled()
  })

  it('异常时写入 error 并向上抛出', async () => {
    const store = useDeviceStore()
    mockApi.disconnectDevice.mockRejectedValueOnce(new Error('disconnect failed'))

    await expect(store.disconnectDevice('a')).rejects.toThrow('disconnect failed')
    expect(store.error).toBe('disconnect failed')
  })
})

describe('device store - SSE', () => {
  it('startSSE 按当前地址组装 URL，connected 去重添加、disconnected 移除', () => {
    const store = useDeviceStore()
    store.devices = [dev('a')]

    store.startSSE()
    const es = lastEventSource()
    expect(es.url).toBe(`${window.location.protocol}//${window.location.host}/api/devices/events`)

    es.onmessage!({ data: JSON.stringify({ type: 'connected', device: dev('b') }) })
    expect(store.devices.map((d) => d.id)).toEqual(['a', 'b'])

    // 同一设备重复上报不重复添加
    es.onmessage!({ data: JSON.stringify({ type: 'connected', device: dev('b') }) })
    expect(store.devices.map((d) => d.id)).toEqual(['a', 'b'])

    es.onmessage!({ data: JSON.stringify({ type: 'disconnected', device_id: 'a' }) })
    expect(store.devices.map((d) => d.id)).toEqual(['b'])
  })

  it('https 页面使用同源 https SSE 地址', () => {
    const original = window.location
    Object.defineProperty(window, 'location', {
      value: { protocol: 'https:', host: 'example.com' },
      configurable: true,
    })
    try {
      const store = useDeviceStore()
      store.startSSE()
      expect(lastEventSource().url).toBe('https://example.com/api/devices/events')
      store.stopSSE()
    } finally {
      Object.defineProperty(window, 'location', { value: original, configurable: true })
    }
  })

  it('重复 startSSE 先关闭旧连接，stopSSE 关闭并支持重复调用', () => {
    const store = useDeviceStore()
    store.startSSE()
    const first = lastEventSource()

    store.startSSE()
    expect(first.close).toHaveBeenCalledTimes(1)
    expect(lastEventSource()).not.toBe(first)
    expect(h.FakeEventSource.instances).toHaveLength(2)

    store.stopSSE()
    expect(lastEventSource().close).toHaveBeenCalledTimes(1)
    store.stopSSE() // 幂等：无连接时不再报错
    expect(h.FakeEventSource.instances).toHaveLength(2)
  })

  it('非法 JSON 与连接错误只记录日志不崩溃', () => {
    const store = useDeviceStore()
    store.devices = [dev('a')]
    store.startSSE()
    const es = lastEventSource()

    es.onmessage!({ data: 'not-json' })
    expect(console.error).toHaveBeenCalledWith('Failed to parse SSE event:', expect.anything())

    es.onerror!()
    expect(console.error).toHaveBeenCalledWith('SSE connection error')
    expect(store.devices.map((d) => d.id)).toEqual(['a'])
  })
})

describe('device store - 其他操作', () => {
  it('installApk 透传参数并返回结果', async () => {
    const store = useDeviceStore()
    mockApi.installApk.mockResolvedValueOnce({ success: true, message: 'ok' })

    await expect(store.installApk('dev-1', '/tmp/demo.apk')).resolves.toEqual({
      success: true,
      message: 'ok',
    })
    expect(mockApi.installApk).toHaveBeenCalledWith('dev-1', '/tmp/demo.apk')
  })

  it('getDevice 成功返回设备，失败返回 null', async () => {
    const store = useDeviceStore()
    mockApi.getDevice.mockResolvedValueOnce(dev('a', { battery: 42 }))
    await expect(store.getDevice('a')).resolves.toMatchObject({ id: 'a', battery: 42 })

    mockApi.getDevice.mockRejectedValueOnce(new Error('404'))
    await expect(store.getDevice('missing')).resolves.toBeNull()
    expect(store.error).toBeNull() // getDevice 失败不污染全局 error
  })
})