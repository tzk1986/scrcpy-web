/**
 * WebTransport 服务（Chrome 专属，占位符）
 * ===========================================
 *
 * WebTransport 是基于 HTTP/3 (QUIC) 的低延迟传输协议。
 * 相比 WebSocket，优势：
 *   - 更低的延迟（无 TCP 队头阻塞）
 *   - 支持数据报（datagram）和流（stream）
 *   - 原生多路复用
 *
 * 浏览器兼容性：
 *   - Chrome 97+：完整支持
 *   - Firefox：实验性支持
 *   - Safari：不支持
 *
 * 当前状态：占位符
 *   检测到不支持时自动降级到 WebSocket。
 *
 * 未来实现：
 *   需要后端配置 HTTP/3 端点（aioquic）。
 */

export class WebTransportService {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  private transport: any = null
  private url: string

  constructor(url: string) {
    this.url = url
  }

  /**
   * 尝试建立 WebTransport 连接。
   * 如果浏览器不支持，返回 false（调用方应降级到 WebSocket）。
   */
  async connect(): Promise<boolean> {
    if (!('WebTransport' in window)) {
      console.warn('WebTransport not supported, falling back to WebSocket')
      return false
    }

    try {
      this.transport = new WebTransport(this.url)
      await this.transport.ready
      console.log('WebTransport connected:', this.url)
      return true
    } catch (error) {
      console.error('WebTransport connection failed:', error)
      return false
    }
  }

  /**
   * 发送数据报（datagram）。
   * 数据报是无界的，适合小消息（如输入事件）。
   */
  async sendDatagram(data: Uint8Array) {
    if (!this.transport) return
    const writer = this.transport.datagrams.writable.getWriter()
    await writer.write(data)
    writer.releaseLock()
  }

  /**
   * 接收数据报流。
   * 持续读取数据报并调用回调函数处理。
   */
  async receiveDatagrams(callback: (data: Uint8Array) => void) {
    if (!this.transport) return
    const reader = this.transport.datagrams.readable.getReader()
    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      callback(value)
    }
  }

  /** 关闭连接。 */
  close() {
    if (this.transport) {
      this.transport.close()
      this.transport = null
    }
  }
}
