/**
 * WebSocket 服务
 * ================
 *
 * 封装浏览器原生 WebSocket API，提供：
 *   - 自动重连（未来实现）
 *   - 消息回调注册
 *   - 二进制数据发送/接收
 *   - 连接状态管理
 *
 * 使用场景：
 *   - 视频流接收（/ws/video/{device_id}）
 *   - 调试数据流（/ws/debug/{session_id}）
 *
 * 使用方式：
 *   const ws = new WebSocketService('ws://localhost:8000/ws/video/xxx')
 *   ws.setMessageHandler((data) => { /* 处理帧数据 *\/ })
 *   ws.connect()
 */

export class WebSocketService {
  private ws: WebSocket | null = null
  private url: string
  private onMessage: ((data: unknown) => void) | null = null
  private onError: ((error: Event) => void) | null = null
  private onClose: (() => void) | null = null

  constructor(url: string) {
    this.url = url
  }

  /**
   * 建立 WebSocket 连接。
   * 注册 onopen/onmessage/onerror/onclose 回调。
   */
  connect() {
    console.log('[WS] Connecting to:', this.url)
    this.ws = new WebSocket(this.url)

    this.ws.onopen = () => {
      console.log('[WS] Connected:', this.url)
    }

    this.ws.onmessage = (event) => {
      const isBinary = event.data instanceof ArrayBuffer || event.data instanceof Blob
      console.log('[WS] Message received:', typeof event.data, isBinary ? `(${event.data instanceof ArrayBuffer ? (event.data as ArrayBuffer).byteLength : 'Blob'} bytes)` : event.data.substring(0, 100))
      if (this.onMessage) {
        this.onMessage(event.data)
      }
    }

    this.ws.onerror = (error) => {
      console.error('[WS] Error:', error)
      if (this.onError) {
        this.onError(error)
      }
    }

    this.ws.onclose = () => {
      console.log('[WS] Closed:', this.url)
      if (this.onClose) {
        this.onClose()
      }
    }
  }

  /**
   * 发送 JSON 或字符串数据。
   * 如果 data 是对象，自动序列化为 JSON。
   */
  send(data: unknown) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(typeof data === 'string' ? data : JSON.stringify(data))
    }
  }

  /**
   * 发送二进制数据（ArrayBuffer）。
   * 用于发送输入事件等二进制消息。
   */
  sendBytes(data: ArrayBuffer) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(data)
    }
  }

  /** 注册消息接收回调。 */
  setMessageHandler(handler: (data: unknown) => void) {
    this.onMessage = handler
  }

  /** 注册错误回调。 */
  setErrorHandler(handler: (error: Event) => void) {
    this.onError = handler
  }

  /** 注册关闭回调。 */
  setCloseHandler(handler: () => void) {
    this.onClose = handler
  }

  /** 关闭连接。 */
  close() {
    if (this.ws) {
      this.ws.close()
      this.ws = null
    }
  }
}
