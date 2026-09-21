/**
 * router 路由表测试（顺带补充，非硬指标）
 * =========================================
 *
 * 只做静态配置与匹配断言，不触发懒加载组件（避免拉起视图层依赖）。
 */
import { describe, expect, it } from 'vitest'
import router from '@/router'

describe('router 路由表', () => {
  it('包含 dashboard 与 device-detail 两条路由', () => {
    const routes = router.getRoutes()
    const byName = new Map(routes.map((r) => [r.name, r]))
    expect([...byName.keys()].sort()).toEqual(['dashboard', 'device-detail'])
    expect(byName.get('dashboard')!.path).toBe('/')
    expect(byName.get('device-detail')!.path).toBe('/device/:id')
  })

  it('路由组件均为懒加载（动态 import 函数）', () => {
    for (const route of router.getRoutes()) {
      expect(typeof route.components?.default).toBe('function')
    }
  })

  it('resolve 生成正确的路径、命名路由与参数', () => {
    const byPath = router.resolve('/device/abc-123')
    expect(byPath.name).toBe('device-detail')
    expect(byPath.params.id).toBe('abc-123')
    expect(byPath.href).toBe('/device/abc-123')

    const byName = router.resolve({ name: 'device-detail', params: { id: 'x' } })
    expect(byName.href).toBe('/device/x')

    expect(router.resolve('/').name).toBe('dashboard')
    expect(router.resolve('/no-such-page').matched).toHaveLength(0)
  })
})