/**
 * 指标导出工具（方案 51 F2-11）
 * ==============================
 *
 * PerfView ≡ NetworkView 逐字重复的导出块（下拉命令 → blob 下载 →
 * X-Export-* 头回显 → 404/错误提示）纯下沉至此；LogcatView 同型的
 * triggerDownload/errText 一并并入。两视图唯一差异是 api 调用与文件
 * 名前缀，抽为 runExport 参数，组件侧行为不变。
 */
import type { AxiosResponse } from 'axios'
import { ElMessage } from 'element-plus'

export type ExportFormat = 'csv' | 'json'
export type ExportSource = 'buffer' | 'cache'
export type ExportCommand = `${ExportSource}-${ExportFormat}`

interface AxiosErrorLike {
  response?: { status?: number }
}

export interface RunExportOptions {
  command: ExportCommand
  deviceId: string
  /** 下载文件名前缀，如 perf / network */
  prefix: string
  fetch: (
    deviceId: string,
    format: ExportFormat,
    source: ExportSource,
  ) => Promise<AxiosResponse<Blob>>
}

/** 下拉命令 → 指标导出：下载 + 成功回显；404 提示「暂无数据可导出」。 */
export async function runExport({ command, deviceId, prefix, fetch }: RunExportOptions): Promise<void> {
  const [source, format] = command.split('-') as [ExportSource, ExportFormat]
  try {
    const res = await fetch(deviceId, format, source)
    triggerDownload(res.data, `${prefix}_${deviceId}_${source}.${format}`)
    ElMessage.success(formatExportMessage(res))
  } catch (e) {
    if ((e as AxiosErrorLike).response?.status === 404) {
      ElMessage.warning('暂无数据可导出')
    } else {
      ElMessage.error(`导出失败: ${errText(e, '未知错误')}`)
    }
  }
}

/** 从响应头提取导出元数据（后端 X-Export-* 头）。 */
export function formatExportMessage(res: AxiosResponse<Blob>): string {
  const count = res.headers['x-export-count']
  const oldest = res.headers['x-export-oldest-ts']
  const newest = res.headers['x-export-newest-ts']
  if (!count) return '导出完成'
  const range =
    oldest && newest
      ? `（${formatTs(Number(oldest))} – ${formatTs(Number(newest))}）`
      : ''
  return `导出完成：${count} 条${range}`
}

/** Blob 下载（LogcatView 同款范式）。 */
export function triggerDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

export function errText(e: unknown, fallback: string): string {
  const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
  return detail || fallback
}

function formatTs(ts: number): string {
  if (!Number.isFinite(ts)) return '-'
  return new Date(ts * 1000).toLocaleTimeString()
}
