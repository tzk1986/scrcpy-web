/**
 * WebSocket 服务
 * ================
 *
 * 封装浏览器原生 WebSocket API，提供：
 *   - 自动重连（指数退避，1s/2s/4s…30s 封顶）
 *   - 消息回调注册
 *   - 二进制数据发送/接收
 *   - 连接状态管理
 *
 * 使用场景：
 *   - 视频流接收（/ws/video/{device_id}）
 *   - 调试数据流（/ws/debug/{session_id}）
 *
 * 使用方式：
 *   const ws = new WebSocketService('ws://localhost:8765/ws/video/xxx')
 *   ws.setMessageHandler((data) => { /* 处理帧数据 *\/ })
 *   ws.connect()
 */

export class WebSocketService {
  private ws: WebSocket | null = null
  private url: string
  private onMessage: ((data: unknown) => void) | null = null
  private onError: ((error: Event) => void) | null = null
  private onClose: (() => void) | null = null
  private reconnectAttempts = 0
  private manualClose = false
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private onReconnect: (() => void) | null = null
  /** 本次 connect() 是定时器驱动的重连，onopen 时触发 onReconnect（首次连接不触发） */
  private pendingReconnect = false

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
    // 默认设置为 arraybuffer，确保二进制数据直接可用
    this.ws.binaryType = 'arraybuffer'

    this.ws.onopen = () => {
      this.reconnectAttempts = 0
      console.log('[WS] Connected:', this.url, 'binaryType:', this.ws?.binaryType)
      // 重连成功（区别于首次连接）：在 onopen 中触发 onReconnect——
      // 此刻该 socket 已 OPEN，handler 内发送的消息（如 resume 的
      // request_keyframe）不会被 CONNECTING 态的 send 守卫丢弃。
      if (this.pendingReconnect) {
        this.pendingReconnect = false
        this.onReconnect?.()
      }
    }

    this.ws.onmessage = (event) => {
      // 视频帧为高频二进制消息，逐条日志是纯浪费；仅记录低频文本控制消息
      const isBinary = event.data instanceof ArrayBuffer || event.data instanceof Blob
      if (!isBinary) {
        console.log('[WS] Text message:', (event.data as string).substring(0, 100))
      }
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
      if (!this.manualClose) {
        this.scheduleReconnect()
      }
    }
  }

  /** 设置 binaryType */
  setBinaryType(type: 'arraybuffer' | 'blob') {
    if (this.ws) {
      this.ws.binaryType = type
      console.log('[WS] binaryType set to:', type)
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

  /** 注册重连成功回调（重连 socket 的 onopen 中触发；首次连接不触发） */
  setReconnectHandler(handler: () => void) {
    this.onReconnect = handler
  }

  private scheduleReconnect() {
    const delay = Math.min(30000, 1000 * 2 ** this.reconnectAttempts)
    this.reconnectAttempts += 1
    console.log(`[WS] Reconnect scheduled in ${delay}ms (attempt ${this.reconnectAttempts})`)
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null
      // 标记本次 connect 为重连：onReconnect 延后到其 onopen 中触发，
      // 保证 handler 执行时 socket 已 OPEN（R1）。
      this.pendingReconnect = true
      this.connect()
    }, delay)
  }

  /** 关闭连接。 */
  close() {
    this.manualClose = true
    this.pendingReconnect = false
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    if (this.ws) {
      this.ws.close()
      this.ws = null
    }
  }
}
