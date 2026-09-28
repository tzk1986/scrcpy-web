/**
 * 告警共享模块测试（方案 24 §6 前端-1）
 * ======================================
 *
 * 覆盖：extractAlerts 防御式解析 / alertLabel 映射与兜底 /
 * createAlertNotifier（episode 去重、窗口内多帧只弹一次、换 since 再弹、列表清空重置、文案）
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const elMessage = vi.hoisted(() => ({ warning: vi.fn() }))
vi.mock('element-plus', () => ({ ElMessage: elMessage }))

import { alertLabel, createAlertNotifier, extractAlerts, type AlertItem } from './alerts'

function makeAlert(overrides: Partial<AlertItem> = {}): AlertItem {
  return {
    id: 'cpu_percent',
    value: 87.2,
    threshold: 80,
    direction: 'above',
    since: 1726000000.0,
    notify: true,
    ...overrides,
  }
}

beforeEach(() => {
  elMessage.warning.mockClear()
})

describe('extractAlerts', () => {
  it('非数组/脏字段 → []', () => {
    expect(extractAlerts(undefined)).toEqual([])
    expect(extractAlerts(null)).toEqual([])
    expect(extractAlerts('x')).toEqual([])
    expect(extractAlerts({ id: 'cpu_percent' })).toEqual([])
    expect(extractAlerts([{ id: 1, since: 2 }, null, {}])).toEqual([])
  })

  it('合法数组原样返回', () => {
    const alert = makeAlert()
    expect(extractAlerts([alert])).toEqual([alert])
  })

  it('畸形条目（缺 threshold / value 为字符串 / direction 非法）全被过滤，合法条目照常保留', () => {
    const malformed = [
      { id: 'cpu_percent', value: 87.2, direction: 'above', since: 1 }, // 缺 threshold
      { id: 'fps', value: '25.4', threshold: 30, direction: 'below', since: 1 }, // value 为字符串
      { id: 'memory_percent', value: 91, threshold: 90, direction: 'up', since: 1 }, // direction 非法
      { id: 'rx_rate_kbps', value: 900, threshold: 800, direction: 'above' }, // 缺 since
    ]
    expect(extractAlerts(malformed)).toEqual([])

    const valid = makeAlert()
    expect(extractAlerts([...malformed, valid])).toEqual([valid])

    // 畸形数据喂给 notifier 不 throw、不弹 toast（过滤后为空；即使直接喂原始数组也安全）
    const notifier = createAlertNotifier()
    expect(() => notifier.update(extractAlerts(malformed))).not.toThrow()
    expect(() => notifier.update(malformed as unknown as AlertItem[])).not.toThrow()
    expect(elMessage.warning).not.toHaveBeenCalled()
  })
})

describe('alertLabel', () => {
  it('已知 id 映射中文，未知 id 兜底原样', () => {
    expect(alertLabel('cpu_percent')).toBe('CPU 使用率')
    expect(alertLabel('fps')).toBe('帧率')
    expect(alertLabel('custom_metric')).toBe('custom_metric')
  })
})

describe('createAlertNotifier', () => {
  it('窗口内多帧同 episode 只弹一次', () => {
    const notifier = createAlertNotifier()
    const alert = makeAlert()
    notifier.update([alert])
    notifier.update([alert])
    notifier.update([alert])
    expect(elMessage.warning).toHaveBeenCalledTimes(1)
  })

  it('换 since 的新 episode 再弹', () => {
    const notifier = createAlertNotifier()
    notifier.update([makeAlert()])
    notifier.update([makeAlert({ since: 1726000100.0 })])
    expect(elMessage.warning).toHaveBeenCalledTimes(2)
  })

  it('notify=false 不弹（仅徽标）', () => {
    const notifier = createAlertNotifier()
    notifier.update([makeAlert({ notify: false })])
    expect(elMessage.warning).not.toHaveBeenCalled()
  })

  it('活动列表清空后重置，同 key 再来会再弹', () => {
    const notifier = createAlertNotifier()
    const alert = makeAlert()
    notifier.update([alert])
    notifier.update([])
    notifier.update([alert])
    expect(elMessage.warning).toHaveBeenCalledTimes(2)
  })

  it('文案：above 为「超过阈值」、below 为「低于阈值」，数值带单位（spec §5）', () => {
    const notifier = createAlertNotifier()
    notifier.update([makeAlert()])
    notifier.update([makeAlert({ id: 'fps', direction: 'below', threshold: 30, value: 25.4, since: 1 })])
    notifier.update([
      makeAlert({ id: 'rx_rate_kbps', direction: 'above', threshold: 800, value: 900, since: 2 }),
    ])
    notifier.update([
      makeAlert({ id: 'custom_metric', direction: 'above', threshold: 3, value: 5, since: 3 }),
    ])
    notifier.update([
      makeAlert({ id: 'memory_percent', direction: 'above', threshold: 90, value: null, since: 4 }),
    ])

    const first = elMessage.warning.mock.calls[0][0] as { message: string; grouping: boolean }
    expect(first.message).toBe('CPU 使用率 87.2% 超过阈值 80.0%')
    expect(first.grouping).toBe(true)

    const second = elMessage.warning.mock.calls[1][0] as { message: string }
    expect(second.message).toBe('帧率 25.4fps 低于阈值 30.0fps')

    const third = elMessage.warning.mock.calls[2][0] as { message: string }
    expect(third.message).toBe('接收速率 900.0kbps 超过阈值 800.0kbps')

    // 未知 id：单位兜底为空串
    const fourth = elMessage.warning.mock.calls[3][0] as { message: string }
    expect(fourth.message).toBe('custom_metric 5.0 超过阈值 3.0')

    // value=null：数值兜底为 '-'（不带单位），阈值仍带单位
    const fifth = elMessage.warning.mock.calls[4][0] as { message: string }
    expect(fifth.message).toBe('内存使用率 - 超过阈值 90.0%')
  })
})
