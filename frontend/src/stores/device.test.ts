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
 *   - scanSubnet（方案 36）：参数透传、静默重拉、ok 集合自动加载缩略图、失败抛错
 *   - 缩略图缓存（方案 36 D5）：TTL 命中/过期 revoke、force 重拉、并发池 ≤3、
 *     同设备去重、失败不缓存、断开清理
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
  scanSubnet: vi.fn(),
  screenshot: vi.fn(),
}))
vi.mock('@/services/api', () => ({ api: mockApi }))

// happy-dom 无 createObjectURL / revokeObjectURL 实现（videoStream.test.ts 同款桩）
const urlStub = vi.hoisted(() => ({
  createObjectURL: vi.fn(),
  revokeObjectURL: vi.fn(),
}))
let urlSeq = 0

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
  mockApi.scanSubnet.mockClear()
  mockApi.screenshot.mockClear().mockResolvedValue(new Blob(['png']))
  urlSeq = 0
  urlStub.createObjectURL.mockReset().mockImplementation(() => `blob:thumb-${++urlSeq}`)
  urlStub.revokeObjectURL.mockReset()
  Object.defineProperty(URL, 'createObjectURL', {
    configurable: true,
    value: urlStub.createObjectURL,
  })
  Object.defineProperty(URL, 'revokeObjectURL', {
    configurable: true,
    value: urlStub.revokeObjectURL,
  })
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

function scanResult(overrides: Record<string, unknown> = {}) {
  return {
    cidr: '192.168.8.0/24',
    probed: 254,
    open_hosts: ['192.168.8.18', '192.168.8.25'],
    connect_results: [
      { ip: '192.168.8.18', ok: true, device_id: '192.168.8.18:5555', reason: null, message: null },
      { ip: '192.168.8.25', ok: false, device_id: null, reason: 'connect_failed', message: 'timeout' },
    ],
    truncated: false,
    ...overrides,
  }
}

describe('device store - scanSubnet（方案 36）', () => {
  it('透传参数；成功后静默重拉列表并对 ok 设备自动加载缩略图', async () => {
    const store = useDeviceStore()
    const result = scanResult()
    mockApi.scanSubnet.mockResolvedValueOnce(result)
    mockApi.listDevices.mockResolvedValueOnce([dev('192.168.8.18:5555')])

    await expect(store.scanSubnet('192.168.8.0/24')).resolves.toBe(result)

    expect(mockApi.scanSubnet).toHaveBeenCalledWith('192.168.8.0/24', true, 5555)
    await vi.waitFor(() => expect(mockApi.listDevices).toHaveBeenCalledTimes(1)) // 静默重拉
    await vi.waitFor(() =>
      expect(mockApi.screenshot).toHaveBeenCalledWith('192.168.8.18:5555')
    )
    expect(mockApi.screenshot).toHaveBeenCalledTimes(1) // 仅 ok 设备，失败设备不加载
    expect(store.error).toBeNull()
  })

  it('自定义 connect/port；无 ok 集合不加载缩略图', async () => {
    const store = useDeviceStore()
    mockApi.scanSubnet.mockResolvedValueOnce(scanResult({ connect_results: [] }))

    await store.scanSubnet('10.0.0.0/24', false, 4444)

    expect(mockApi.scanSubnet).toHaveBeenCalledWith('10.0.0.0/24', false, 4444)
    expect(mockApi.screenshot).not.toHaveBeenCalled()
  })

  it('重拉不阻塞扫描返回：列表挂起时仍立即返回结果', async () => {
    const store = useDeviceStore()
    const result = scanResult({ connect_results: [], open_hosts: [] })
    mockApi.scanSubnet.mockResolvedValueOnce(result)
    mockApi.listDevices.mockImplementation(() => new Promise<DeviceInfo[]>(() => {}))

    let deadline: ReturnType<typeof setTimeout> | undefined
    try {
      const raced = await Promise.race([
        store.scanSubnet('192.168.8.0/24'),
        new Promise<never>((_, reject) => {
          deadline = setTimeout(() => reject(new Error('列表刷新阻塞了扫描返回')), 500)
        }),
      ])
      expect(raced).toBe(result)
      expect(mockApi.listDevices).toHaveBeenCalled() // 静默重拉已发起
    } finally {
      clearTimeout(deadline)
    }
  })

  it('异常时写入 error 并向上抛出，不触发重拉', async () => {
    const store = useDeviceStore()
    mockApi.scanSubnet.mockRejectedValueOnce(new Error('已有扫描任务进行中'))

    await expect(store.scanSubnet('192.168.8.0/24')).rejects.toThrow('已有扫描任务进行中')
    expect(store.error).toBe('已有扫描任务进行中')
    expect(mockApi.listDevices).not.toHaveBeenCalled()
  })
})

describe('device store - 缩略图缓存（方案 36 D5）', () => {
  it('loadThumbnail 成功缓存（url + 快照时刻），TTL 内重复调用命中缓存', async () => {
    const store = useDeviceStore()
    await store.loadThumbnail('a')

    expect(mockApi.screenshot).toHaveBeenCalledWith('a')
    expect(store.thumbnails['a'].url).toBe('blob:thumb-1')
    expect(store.thumbnails['a'].ts).toBeGreaterThan(0)
    expect(store.thumbLoading['a']).toBeUndefined()

    await store.loadThumbnail('a') // TTL 内命中，不重拉
    expect(mockApi.screenshot).toHaveBeenCalledTimes(1)
  })

  it('TTL 过期后重拉并 revoke 旧 objectURL', async () => {
    const store = useDeviceStore()
    await store.loadThumbnail('a')
    const oldUrl = store.thumbnails['a'].url
    store.thumbnails = { a: { url: oldUrl, ts: Date.now() - 30_001 } } // 模拟过期

    await store.loadThumbnail('a')
    expect(mockApi.screenshot).toHaveBeenCalledTimes(2)
    expect(urlStub.revokeObjectURL).toHaveBeenCalledWith(oldUrl)
    expect(store.thumbnails['a'].url).toBe('blob:thumb-2')
  })

  it('force=true 绕过 TTL 强制重拉（刷新场景）', async () => {
    const store = useDeviceStore()
    await store.loadThumbnail('a')
    const oldUrl = store.thumbnails['a'].url

    await store.loadThumbnail('a', true)
    expect(mockApi.screenshot).toHaveBeenCalledTimes(2)
    expect(urlStub.revokeObjectURL).toHaveBeenCalledWith(oldUrl)
    expect(store.thumbnails['a'].url).toBe('blob:thumb-2')
  })

  it('加载中 loading 态可见，完成后清除', async () => {
    const store = useDeviceStore()
    let resolveShot!: (b: Blob) => void
    mockApi.screenshot.mockImplementationOnce(
      () => new Promise<Blob>((resolve) => { resolveShot = resolve })
    )

    const p = store.loadThumbnail('a')
    expect(store.thumbLoading['a']).toBe(true) // 请求发起即置（含队列等待期）

    await vi.waitFor(() => expect(mockApi.screenshot).toHaveBeenCalledTimes(1))
    resolveShot(new Blob(['x']))
    await p
    expect(store.thumbLoading['a']).toBeUndefined()
  })

  it('同设备 in-flight 去重合并，不重复请求', async () => {
    const store = useDeviceStore()
    let resolveShot!: (b: Blob) => void
    mockApi.screenshot.mockImplementationOnce(
      () => new Promise<Blob>((resolve) => { resolveShot = resolve })
    )

    const p1 = store.loadThumbnail('a')
    const p2 = store.loadThumbnail('a')
    await vi.waitFor(() => expect(mockApi.screenshot).toHaveBeenCalledTimes(1))

    resolveShot(new Blob(['x']))
    await Promise.all([p1, p2])
    expect(mockApi.screenshot).toHaveBeenCalledTimes(1)
    expect(store.thumbnails['a']).toBeDefined()
  })

  it('批量加载并发峰值 ≤3（对设备温和），且逐台推进', async () => {
    const store = useDeviceStore()
    let inflight = 0
    let peak = 0
    const resolvers: Array<() => void> = []
    mockApi.screenshot.mockImplementation(() => {
      inflight++
      peak = Math.max(peak, inflight)
      return new Promise<Blob>((resolve) => {
        resolvers.push(() => {
          inflight--
          resolve(new Blob(['x']))
        })
      })
    })

    const p = store.loadThumbnails(['a', 'b', 'c', 'd', 'e'])
    await vi.waitFor(() => expect(mockApi.screenshot).toHaveBeenCalledTimes(3))
    resolvers.shift()!() // 释放一槽 → 第 4 台启动
    await vi.waitFor(() => expect(mockApi.screenshot).toHaveBeenCalledTimes(4))
    resolvers.shift()!() // 再释一槽 → 第 5 台启动
    await vi.waitFor(() => expect(mockApi.screenshot).toHaveBeenCalledTimes(5))
    while (resolvers.length > 0) resolvers.shift()!() // 放行剩余
    await p

    expect(peak).toBe(3) // 同时最多 3 台 screencap
    expect(mockApi.screenshot).toHaveBeenCalledTimes(5)
  })

  it('loadThumbnails 批量加载，force 透传（刷新全部场景）', async () => {
    const store = useDeviceStore()
    await store.loadThumbnails(['a', 'b'])
    expect(mockApi.screenshot).toHaveBeenCalledTimes(2)
    expect(store.thumbnails['a']).toBeDefined()
    expect(store.thumbnails['b']).toBeDefined()

    await store.loadThumbnails(['a', 'b'], true) // TTL 内但强制刷新
    expect(mockApi.screenshot).toHaveBeenCalledTimes(4)
  })

  it('加载失败置失败态且不写缓存，重试成功后恢复', async () => {
    const store = useDeviceStore()
    mockApi.screenshot.mockRejectedValueOnce(new Error('offline'))
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})

    await store.loadThumbnail('a')
    expect(warnSpy).toHaveBeenCalled() // 终评 M-4：失败留诊断痕迹
    warnSpy.mockRestore()
    expect(store.thumbFailed['a']).toBe(true)
    expect(store.thumbnails['a']).toBeUndefined() // 失败不写 TTL 缓存
    expect(store.thumbLoading['a']).toBeUndefined()

    await store.loadThumbnail('a') // 失败未缓存 → 直接重拉
    expect(mockApi.screenshot).toHaveBeenCalledTimes(2)
    expect(store.thumbFailed['a']).toBeUndefined()
    expect(store.thumbnails['a']).toBeDefined()
  })

  it('断开设备时清理缩略图条目并 revoke', async () => {
    const store = useDeviceStore()
    store.devices = [dev('a')]
    await store.loadThumbnail('a')
    const url = store.thumbnails['a'].url

    await store.disconnectDevice('a')
    expect(store.thumbnails['a']).toBeUndefined()
    expect(urlStub.revokeObjectURL).toHaveBeenCalledWith(url)
  })

  it('列表静默重拉后消失的设备：缩略图条目清理并 revoke（终评 A-3/M-3）', async () => {
    const store = useDeviceStore()
    store.devices = [dev('a'), dev('b')]
    await store.loadThumbnail('a')
    const url = store.thumbnails['a'].url

    mockApi.listDevices.mockResolvedValue([dev('b')])
    await store.fetchDevices()

    expect(store.thumbnails['a']).toBeUndefined()
    expect(urlStub.revokeObjectURL).toHaveBeenCalledWith(url)
    expect(store.thumbnails['b']).toBeUndefined() // 无条目设备不误伤
  })

  it('移除设备时在途加载作废：完成后不回写条目且 revoke 新 blob（终评 A-3/M-2）', async () => {
    const store = useDeviceStore()
    store.devices = [dev('a')]
    mockApi.listDevices.mockResolvedValue([])
    mockApi.disconnectDevice.mockResolvedValue({ success: true })
    let resolveShot!: (b: Blob) => void
    mockApi.screenshot.mockImplementationOnce(
      () => new Promise<Blob>((resolve) => { resolveShot = resolve })
    )

    const p = store.loadThumbnail('a')
    await vi.waitFor(() => expect(mockApi.screenshot).toHaveBeenCalledTimes(1))
    await store.disconnectDevice('a') // 在途期间移除设备
    resolveShot(new Blob(['x']))
    await p

    expect(store.thumbnails['a']).toBeUndefined() // 不复活
    expect(urlStub.revokeObjectURL).toHaveBeenCalledWith('blob:thumb-1') // 新 blob 不泄漏
  })

  it('SSE 断开事件同步清理缩略图条目', () => {
    const store = useDeviceStore()
    store.devices = [dev('a')]
    store.thumbnails = { a: { url: 'blob:x', ts: 1 } }

    store.startSSE()
    lastEventSource().onmessage!({
      data: JSON.stringify({ type: 'disconnected', device_id: 'a' }),
    })

    expect(store.thumbnails['a']).toBeUndefined()
    expect(urlStub.revokeObjectURL).toHaveBeenCalledWith('blob:x')
  })
})
