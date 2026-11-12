/**
 * exportKit 直接测试（方案 58 F-14）
 * ===================================
 *
 * 覆盖 runExport 命令解析/文件名拼接/404 与其他错误分级、
 * formatExportMessage 头回显各分支、triggerDownload 下载链路。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { AxiosResponse } from 'axios'

const fm = vi.hoisted(() => ({
  success: vi.fn(),
  warning: vi.fn(),
  error: vi.fn(),
}))
vi.mock('element-plus', () => ({ ElMessage: fm }))

const errTextStub = vi.hoisted(() =>
  vi.fn((e: unknown, fallback: string) => (e as Error)?.message ?? fallback),
)
vi.mock('./apiError', () => ({ errText: errTextStub }))

import { formatExportMessage, runExport, triggerDownload } from './exportKit'

/** Blob 下载环境 stub（happy-dom 无 createObjectURL / anchor.click 实现，PerfView.test.ts 同款）。 */
function stubBlobDownload() {
  const createObjectURL = vi.fn(() => 'blob:mock-url')
  const revokeObjectURL = vi.fn()
  Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: createObjectURL })
  Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: revokeObjectURL })
  let clickedAnchor: HTMLAnchorElement | null = null
  const clickSpy = vi
    .spyOn(HTMLAnchorElement.prototype, 'click')
    .mockImplementation(function (this: HTMLAnchorElement) {
      clickedAnchor = this
    })
  return { createObjectURL, revokeObjectURL, clickSpy, getAnchor: () => clickedAnchor }
}

function makeRes(headers: Record<string, string>): AxiosResponse<Blob> {
  return { data: new Blob(['x']), headers } as unknown as AxiosResponse<Blob>
}

beforeEach(() => {
  fm.success.mockReset()
  fm.warning.mockReset()
  fm.error.mockReset()
  errTextStub.mockClear()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('runExport', () => {
  it('cache-csv：解析命令、透传参数、拼接文件名并回显成功', async () => {
    const { getAnchor } = stubBlobDownload()
    const fetchMock = vi.fn().mockResolvedValue(makeRes({ 'x-export-count': '5' }))
    await runExport({
      command: 'cache-csv',
      deviceId: 'dev1',
      prefix: 'perf',
      fetch: fetchMock,
    })
    expect(fetchMock).toHaveBeenCalledWith('dev1', 'csv', 'cache')
    expect(getAnchor()?.download).toBe('perf_dev1_cache.csv')
    expect(fm.success).toHaveBeenCalledWith('导出完成：5 条')
  })

  it('buffer-json：source/format 拆分正确', async () => {
    stubBlobDownload()
    const fetchMock = vi.fn().mockResolvedValue(makeRes({}))
    await runExport({
      command: 'buffer-json',
      deviceId: 'dev9',
      prefix: 'network',
      fetch: fetchMock,
    })
    expect(fetchMock).toHaveBeenCalledWith('dev9', 'json', 'buffer')
    expect(fm.success).toHaveBeenCalledWith('导出完成')
  })

  it('404 分级提示「暂无数据可导出」', async () => {
    stubBlobDownload()
    const fetchMock = vi
      .fn()
      .mockRejectedValue({ response: { status: 404 } })
    await runExport({
      command: 'cache-csv',
      deviceId: 'dev1',
      prefix: 'perf',
      fetch: fetchMock,
    })
    expect(fm.warning).toHaveBeenCalledWith('暂无数据可导出')
    expect(fm.error).not.toHaveBeenCalled()
  })

  it('其他错误走 errText 汇总为导出失败', async () => {
    stubBlobDownload()
    const fetchMock = vi.fn().mockRejectedValue(new Error('boom'))
    await runExport({
      command: 'cache-csv',
      deviceId: 'dev1',
      prefix: 'perf',
      fetch: fetchMock,
    })
    expect(errTextStub).toHaveBeenCalled()
    expect(fm.error).toHaveBeenCalledWith('导出失败: boom')
    expect(fm.warning).not.toHaveBeenCalled()
  })
})

describe('formatExportMessage', () => {
  it('无 count 头返回兜底文案', () => {
    expect(formatExportMessage(makeRes({}))).toBe('导出完成')
  })

  it('有 count 无时间戳头不拼范围', () => {
    expect(formatExportMessage(makeRes({ 'x-export-count': '3' }))).toBe(
      '导出完成：3 条',
    )
  })

  it('oldest/newest 齐备时拼时间范围', () => {
    const msg = formatExportMessage(
      makeRes({
        'x-export-count': '5',
        'x-export-oldest-ts': '1700000000',
        'x-export-newest-ts': '1700003600',
      }),
    )
    expect(msg.startsWith('导出完成：5 条（')).toBe(true)
    expect(msg.endsWith('）')).toBe(true)
  })

  it('时间戳非数字按 - 占位', () => {
    const msg = formatExportMessage(
      makeRes({
        'x-export-count': '2',
        'x-export-oldest-ts': 'abc',
        'x-export-newest-ts': '1700000000',
      }),
    )
    expect(msg).toContain('-')
    expect(msg.startsWith('导出完成：2 条（- –')).toBe(true)
  })

  it('仅一端时间戳不拼范围', () => {
    expect(
      formatExportMessage(makeRes({ 'x-export-count': '1', 'x-export-oldest-ts': '1700000000' })),
    ).toBe('导出完成：1 条')
  })
})

describe('triggerDownload', () => {
  it('走 createObjectURL→a.download→click→revokeObjectURL 链路', () => {
    const { createObjectURL, revokeObjectURL, clickSpy, getAnchor } = stubBlobDownload()
    const blob = new Blob(['payload'])
    triggerDownload(blob, 'a_b_c.csv')
    expect(createObjectURL).toHaveBeenCalledWith(blob)
    const anchor = getAnchor()
    expect(anchor?.download).toBe('a_b_c.csv')
    expect(anchor?.href).toBe('blob:mock-url')
    expect(clickSpy).toHaveBeenCalledTimes(1)
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:mock-url')
  })
})
