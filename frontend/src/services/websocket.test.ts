/**
 * WebSocket 服务测试
 * ====================
 *
 * stub 全局 WebSocket 为可控 Fake，验证连接建立、binaryType 设置、
 * 消息分发（文本/二进制）、send 的开关条件与回调注册。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { WebSocketService } from './websocket'

class FakeWebSocket {
  static instances: FakeWebSocket[] = []
  static readonly CONNECTING = 0
  static readonly OPEN = 1
  static readonly CLOSING = 2
  static readonly CLOSED = 3

  url: string
  binaryType = 'blob'
  readyState: number = FakeWebSocket.OPEN
  sent: unknown[] = []
  closed = false
  onopen: ((e: Event) => void) | null = null
  onmessage: ((e: MessageEvent) => void) | null = null
  onerror: ((e: Event) => void) | null = null
  onclose: (() => void) | null = null

  constructor(url: string) {
    this.url = url
    FakeWebSocket.instances.push(this)
  }
  send(data: unknown) {
    this.sent.push(data)
  }
  close() {
    this.closed = true
    this.readyState = FakeWebSocket.CLOSED
  }
}

function latest(): FakeWebSocket {
  const ws = FakeWebSocket.instances.at(-1)
  if (!ws) throw new Error('WebSocket 未创建')
  return ws
}

let logSpy: ReturnType<typeof vi.spyOn>
let errorSpy: ReturnType<typeof vi.spyOn>

beforeEach(() => {
  FakeWebSocket.instances = []
  vi.stubGlobal('WebSocket', FakeWebSocket)
  logSpy = vi.spyOn(console, 'log').mockImplementation(() => {})
  errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('connect', () => {
  it('以给定 URL 建立连接并设置 arraybuffer binaryType', () => {
    const svc = new WebSocketService('ws://host/ws/video/dev1')
    svc.connect()

    expect(FakeWebSocket.instances).toHaveLength(1)
    expect(latest().url).toBe('ws://host/ws/video/dev1')
    expect(latest().binaryType).toBe('arraybuffer')
    expect(logSpy).toHaveBeenCalledWith('[WS] Connecting to:', 'ws://host/ws/video/dev1')
  })

  it('注册 onopen/onmessage/onerror/onclose 四个回调', () => {
    const svc = new WebSocketService('ws://host/ws/video/dev1')
    svc.connect()

    const ws = latest()
    expect(ws.onopen).toBeTypeOf('function')
    expect(ws.onmessage).toBeTypeOf('function')
    expect(ws.onerror).toBeTypeOf('function')
    expect(ws.onclose).toBeTypeOf('function')

    // 触发 onopen 只记日志，不抛异常
    expect(() => ws.onopen!(new Event('open'))).not.toThrow()
    expect(logSpy).toHaveBeenCalledWith(
      '[WS] Connected:', 'ws://host/ws/video/dev1', 'binaryType:', 'arraybuffer',
    )
  })

  it('再次 connect 会重建连接实例', () => {
    const svc = new WebSocketService('ws://host/ws/video/dev1')
    svc.connect()
    svc.connect()
    expect(FakeWebSocket.instances).toHaveLength(2)
  })
})

describe('消息分发', () => {
  it('文本消息记录日志并转交 handler', () => {
    const svc = new WebSocketService('ws://host/ws/debug')
    const handler = vi.fn()
    svc.setMessageHandler(handler)
    svc.connect()

    latest().onmessage!({ data: 'hello' } as MessageEvent)

    expect(handler).toHaveBeenCalledWith('hello')
    expect(logSpy).toHaveBeenCalledWith('[WS] Text message:', 'hello')
  })

  it('二进制（ArrayBuffer）消息不记日志但转交 handler', () => {
    const svc = new WebSocketService('ws://host/ws/video/dev1')
    const handler = vi.fn()
    svc.setMessageHandler(handler)
    svc.connect()

    const buf = new ArrayBuffer(8)
    latest().onmessage!({ data: buf } as MessageEvent)

    expect(handler).toHaveBeenCalledWith(buf)
    expect(logSpy).not.toHaveBeenCalledWith('[WS] Text message:', expect.anything())
  })

  it('Blob 消息按二进制处理（不记文本日志）', () => {
    const svc = new WebSocketService('ws://host/ws/video/dev1')
    const handler = vi.fn()
    svc.setMessageHandler(handler)
    svc.connect()

    const blob = new Blob(['frame'])
    latest().onmessage!({ data: blob } as MessageEvent)

    expect(handler).toHaveBeenCalledWith(blob)
    expect(logSpy).not.toHaveBeenCalledWith('[WS] Text message:', expect.anything())
  })

  it('未注册 handler 时消息被静默忽略', () => {
    const svc = new WebSocketService('ws://host/ws/video/dev1')
    svc.connect()
    expect(() => latest().onmessage!({ data: 'x' } as MessageEvent)).not.toThrow()
  })

  it('error 事件记录错误并转交 error handler', () => {
    const svc = new WebSocketService('ws://host/ws/video/dev1')
    const onError = vi.fn()
    svc.setErrorHandler(onError)
    svc.connect()

    const evt = new Event('error')
    latest().onerror!(evt)

    expect(onError).toHaveBeenCalledWith(evt)
    expect(errorSpy).toHaveBeenCalledWith('[WS] Error:', evt)
  })

  it('未注册 error handler 时错误被吞掉', () => {
    const svc = new WebSocketService('ws://host/ws/video/dev1')
    svc.connect()
    expect(() => latest().onerror!(new Event('error'))).not.toThrow()
  })

  it('close 事件转交 close handler', () => {
    const svc = new WebSocketService('ws://host/ws/video/dev1')
    const onClose = vi.fn()
    svc.setCloseHandler(onClose)
    svc.connect()

    latest().onclose!()

    expect(onClose).toHaveBeenCalledTimes(1)
    expect(logSpy).toHaveBeenCalledWith('[WS] Closed:', 'ws://host/ws/video/dev1')
  })

  it('未注册 close handler 时关闭被吞掉', () => {
    const svc = new WebSocketService('ws://host/ws/video/dev1')
    svc.connect()
    expect(() => latest().onclose!()).not.toThrow()
  })
})

describe('setBinaryType', () => {
  it('连接后切换 binaryType 为 blob', () => {
    const svc = new WebSocketService('ws://host/ws/video/dev1')
    svc.connect()
    svc.setBinaryType('blob')

    expect(latest().binaryType).toBe('blob')
    expect(logSpy).toHaveBeenCalledWith('[WS] binaryType set to:', 'blob')
  })

  it('未连接时调用不抛异常', () => {
    const svc = new WebSocketService('ws://host/ws/video/dev1')
    expect(() => svc.setBinaryType('arraybuffer')).not.toThrow()
  })
})

describe('send / sendBytes', () => {
  it('对象序列化为 JSON 发送', () => {
    const svc = new WebSocketService('ws://host/ws/video/dev1')
    svc.connect()
    svc.send({ action: 'touch', x: 1, y: 2 })

    expect(latest().sent).toEqual(['{"action":"touch","x":1,"y":2}'])
  })

  it('字符串原样发送', () => {
    const svc = new WebSocketService('ws://host/ws/video/dev1')
    svc.connect()
    svc.send('ping')

    expect(latest().sent).toEqual(['ping'])
  })

  it('连接未打开时 send 被丢弃', () => {
    const svc = new WebSocketService('ws://host/ws/video/dev1')
    svc.connect()
    latest().readyState = FakeWebSocket.CONNECTING

    svc.send({ action: 'key' })
    expect(latest().sent).toEqual([])
  })

  it('尚未 connect 时 send 不抛异常', () => {
    const svc = new WebSocketService('ws://host/ws/video/dev1')
    expect(() => svc.send({ action: 'key' })).not.toThrow()
  })

  it('sendBytes 发送 ArrayBuffer，未打开时丢弃', () => {
    const svc = new WebSocketService('ws://host/ws/video/dev1')
    svc.connect()

    const buf = new ArrayBuffer(4)
    svc.sendBytes(buf)
    expect(latest().sent).toEqual([buf])

    latest().readyState = FakeWebSocket.CLOSED
    svc.sendBytes(new ArrayBuffer(2))
    expect(latest().sent).toEqual([buf])
  })

  it('尚未 connect 时 sendBytes 不抛异常', () => {
    const svc = new WebSocketService('ws://host/ws/video/dev1')
    expect(() => svc.sendBytes(new ArrayBuffer(1))).not.toThrow()
  })
})

describe('close', () => {
  it('关闭底层连接并置空，后续 send 静默丢弃', () => {
    const svc = new WebSocketService('ws://host/ws/video/dev1')
    svc.connect()
    const ws = latest()

    svc.close()
    expect(ws.closed).toBe(true)

    // ws 已置 null：send 不再触碰旧连接
    svc.send({ action: 'key' })
    expect(ws.sent).toEqual([])
  })

  it('重复 close 不抛异常', () => {
    const svc = new WebSocketService('ws://host/ws/video/dev1')
    svc.connect()
    svc.close()
    expect(() => svc.close()).not.toThrow()
  })

  it('未连接时 close 不抛异常', () => {
    const svc = new WebSocketService('ws://host/ws/video/dev1')
    expect(() => svc.close()).not.toThrow()
  })
})