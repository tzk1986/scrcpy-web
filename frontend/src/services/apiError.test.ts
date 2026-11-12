/**
 * apiError 助手单测（方案 58 E-17 共享提取）
 */
import { describe, expect, it } from 'vitest'
import { errOf, errText } from './apiError'

describe('errOf', () => {
  it('解析后端统一 {error:{code,message}} 形状', () => {
    const e = { response: { data: { error: { code: 'FILE_NOT_FOUND', message: '文件不存在' } } } }
    expect(errOf(e)).toEqual({ code: 'FILE_NOT_FOUND', message: '文件不存在' })
  })

  it('无响应体时退 e.message、code 为空串', () => {
    expect(errOf(new Error('network down'))).toEqual({ code: '', message: 'network down' })
  })

  it('非 Error 值退 String(e)', () => {
    expect(errOf('oops').message).toBe('oops')
  })
})

describe('errText', () => {
  it('error.message 优先于 detail', () => {
    const e = { response: { data: { error: { message: '占用' }, detail: 'ignored' } } }
    expect(errText(e, '兜底')).toBe('占用')
  })

  it('无 error 形状时兼容旧 {detail} 体', () => {
    expect(errText({ response: { data: { detail: 'device busy' } } }, '兜底')).toBe('device busy')
  })

  it('两者皆无取 fallback', () => {
    expect(errText(new Error('x'), '未知错误')).toBe('未知错误')
  })
})
