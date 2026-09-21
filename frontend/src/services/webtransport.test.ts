/**
 * WebTransport 服务测试
 * =======================
 *
 * stub window.WebTransport 为可控 Fake，验证：
 *   - 不支持时 connect 返回 false（降级信号）
 *   - ready 成功/失败分支
 *   - sendDatagram / receiveDatagrams 的未连接短路与正常读写
 *   - close 释放与幂等
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { WebTransportService } from './webtransport'

type ReaderChunk = { done: false; value: Uint8Array } | { done: true; value?: undefined }

class FakeWebTransport {
  static instances: FakeWebTransport[] = []
  /** 设置后构造出的 transport.ready 将 reject */
  static readyError: Error | null = null
  /** receiveDatagrams 的读取序列（耗尽后返回 done） */
  static readChunks: Uint8Array[] = []

  url: string
  ready: Promise<void>
  closed = false
  writes: Uint8Array[] = []
  releaseCount = 0
  datagrams: {
    writable: { getWriter: () => unknown }
    readable: { getReader: () => unknown }
  }

  constructor(url: string) {
    this.url = url
    FakeWebTransport.instances.push(this)
    this.ready = FakeWebTransport.readyError
      ? Promise.reject(FakeWebTransport.readyError)
      : Promise.resolve()

    const self = this
    this.datagrams = {
      writable: {
        getWriter: () => ({
          write: async (data: Uint8Array) => { self.writes.push(data) },
          releaseLock: () => { self.releaseCount++ },
        }),
      },
      readable: {
        getReader: () => {
          const chunks = [...FakeWebTransport.readChunks]
          return {
            read: async (): Promise<ReaderChunk> => {
              const value = chunks.shift()
              return value ? { done: false, value } : { done: true }
            },
          }
        },
      },
    }
  }

  close() {
    this.closed = true
  }
}

function transportOf(svc: WebTransportService): FakeWebTransport {
  const t = (svc as unknown as { transport: FakeWebTransport | null }).transport
  if (!t) throw new Error('transport 未建立')
  return t
}

let errorSpy: ReturnType<typeof vi.spyOn>

beforeEach(() => {
  FakeWebTransport.instances = []
  FakeWebTransport.readyError = null
  FakeWebTransport.readChunks = []
  vi.spyOn(console, 'warn').mockImplementation(() => {})
  errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
  vi.stubGlobal('WebTransport', FakeWebTransport)
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('connect', () => {
  it('浏览器不支持 WebTransport 时返回 false（调用方降级）', async () => {
    Reflect.deleteProperty(window, 'WebTransport')
    const svc = new WebTransportService('https://host:443/wt')

    await expect(svc.connect()).resolves.toBe(false)
    expect(FakeWebTransport.instances).toHaveLength(0)
  })

  it('ready 成功后返回 true 并保存 transport', async () => {
    const svc = new WebTransportService('https://host:443/wt')
    await expect(svc.connect()).resolves.toBe(true)

    expect(FakeWebTransport.instances).toHaveLength(1)
    expect(transportOf(svc).url).toBe('https://host:443/wt')
  })

  it('ready 失败时返回 false 并记录错误', async () => {
    FakeWebTransport.readyError = new Error('connection refused')
    const svc = new WebTransportService('https://host:443/wt')

    await expect(svc.connect()).resolves.toBe(false)
    expect(errorSpy).toHaveBeenCalledWith(
      'WebTransport connection failed:', FakeWebTransport.readyError,
    )
  })
})

describe('sendDatagram', () => {
  it('未连接时直接返回，不访问 datagrams', async () => {
    const svc = new WebTransportService('https://host:443/wt')
    await expect(svc.sendDatagram(new Uint8Array([1]))).resolves.toBeUndefined()
    expect(FakeWebTransport.instances).toHaveLength(0)
  })

  it('已连接时写入数据报并释放 writer', async () => {
    const svc = new WebTransportService('https://host:443/wt')
    await svc.connect()

    const payload = new Uint8Array([1, 2, 3])
    await svc.sendDatagram(payload)

    const t = transportOf(svc)
    expect(t.writes).toEqual([payload])
    expect(t.releaseCount).toBe(1)
  })
})

describe('receiveDatagrams', () => {
  it('未连接时直接返回', async () => {
    const svc = new WebTransportService('https://host:443/wt')
    const cb = vi.fn()
    await svc.receiveDatagrams(cb)
    expect(cb).not.toHaveBeenCalled()
  })

  it('持续读取直到 done，逐条回调', async () => {
    FakeWebTransport.readChunks = [new Uint8Array([10]), new Uint8Array([20, 21])]
    const svc = new WebTransportService('https://host:443/wt')
    await svc.connect()

    const received: Uint8Array[] = []
    await svc.receiveDatagrams(d => received.push(d))

    expect(received).toHaveLength(2)
    expect(Array.from(received[0])).toEqual([10])
    expect(Array.from(received[1])).toEqual([20, 21])
  })
})

describe('close', () => {
  it('关闭底层 transport 后 sendDatagram 变为空操作', async () => {
    const svc = new WebTransportService('https://host:443/wt')
    await svc.connect()
    const t = transportOf(svc)

    svc.close()
    expect(t.closed).toBe(true)

    await svc.sendDatagram(new Uint8Array([9]))
    expect(t.writes).toEqual([])
  })

  it('重复 close 不抛异常', async () => {
    const svc = new WebTransportService('https://host:443/wt')
    await svc.connect()
    svc.close()
    expect(() => svc.close()).not.toThrow()
  })
})