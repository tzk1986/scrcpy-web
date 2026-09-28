/**
 * 性能阈值告警共享模块（方案 24）
 * ================================
 *
 * 层：services（纯逻辑，无组件/Store 依赖）。
 *
 * 职责：
 *   - AlertItem 类型与后端快照 JSON 对齐（方案 §4.2）
 *   - extractAlerts：防御式解析（脏数据 → []，既有消息无 alerts 键安全）
 *   - alertLabel：指标 id → 中文标签（展示无关的引擎只发原始 id）
 *   - createAlertNotifier：notify=true 条目按 id:since(episode) 去重弹 toast；
 *     服务端降噪（迟滞+冷却）与客户端去重两层职责分明
 */
import { ElMessage } from 'element-plus'

export interface AlertItem {
  id: string
  value: number | null
  threshold: number
  direction: 'above' | 'below'
  since: number
  notify?: boolean
}

const ALERT_LABELS: Record<string, string> = {
  cpu_percent: 'CPU 使用率',
  memory_percent: '内存使用率',
  fps: '帧率',
  rx_rate_kbps: '接收速率',
  tx_rate_kbps: '发送速率',
}

const ALERT_UNITS: Record<string, string> = {
  cpu_percent: '%',
  memory_percent: '%',
  fps: 'fps',
  rx_rate_kbps: 'kbps',
  tx_rate_kbps: 'kbps',
}

export function alertLabel(id: string): string {
  return ALERT_LABELS[id] ?? id
}

export function extractAlerts(data: unknown): AlertItem[] {
  if (!Array.isArray(data)) return []
  return data.filter(
    (item): item is AlertItem =>
      !!item &&
      typeof item === 'object' &&
      typeof (item as AlertItem).id === 'string' &&
      typeof (item as AlertItem).since === 'number',
  )
}

function formatAlert(item: AlertItem): string {
  const label = alertLabel(item.id)
  const unit = ALERT_UNITS[item.id] ?? ''
  const value = typeof item.value === 'number' ? `${item.value.toFixed(1)}${unit}` : '-'
  const threshold = `${item.threshold.toFixed(1)}${unit}`
  return item.direction === 'above'
    ? `${label} ${value} 超过阈值 ${threshold}`
    : `${label} ${value} 低于阈值 ${threshold}`
}

/** 每个视图实例创建一个：episode 去重状态随视图生命周期（多设备多实例互不串扰）。 */
export function createAlertNotifier(): { update(alerts: AlertItem[]): void } {
  const notified = new Set<string>()

  function update(alerts: AlertItem[]): void {
    const activeKeys = new Set<string>()
    for (const item of alerts) {
      const key = `${item.id}:${item.since}`
      activeKeys.add(key)
      if (item.notify === true && !notified.has(key)) {
        notified.add(key)
        ElMessage.warning({ message: formatAlert(item), grouping: true })
      }
    }
    // 已通知集随当前活动列表裁剪（episode 解除后释放记录；列表清空整体重置）
    for (const key of notified) {
      if (!activeKeys.has(key)) notified.delete(key)
    }
  }

  return { update }
}
