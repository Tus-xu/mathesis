/**
 * 科研端路由收敛（**隔离后保留的覆盖**）。
 *
 * 背景：`test/research-vnext/rv16-route-convergence.test.ts` 随上一代沉浸式科研面
 * （`src/features/research-vnext`，2026-09-17 隔离移出）一并作废。但它的**一半断言
 * 测的是仍然存在的行为** —— `src/router/index.js` 里那组「旧入口 → 新工作台」的重定向。
 * 丢掉那部分覆盖会留下真空，所以这里用精简版补回来。
 *
 * 断言（全部针对**现存**代码）：
 *  1. 4 条退役入口仍注册，且 `redirect === '/research-workspace'`
 *  2. 上一代的会话路由 `/research-vnext/session*` **不再注册**（旧面已移出）
 *  3. 新工作台三条路由注册到位（root / :projectId/read / :projectId/write）
 */
import { describe, expect, it } from 'vitest'

import { router } from '@/router'

/** 与 `src/router/index.js` 的 `retiredResearchPaths` 保持一致。 */
const RETIRED = [
  '/research-vnext/welcome',
  '/research-vnext/workbench',
  '/research-vnext/reader',
  '/research-vnext/writing',
]

describe('科研端路由收敛（隔离后）', () => {
  it('4 条旧入口仍注册且一律重定向到 /research-workspace', () => {
    const records = router.getRoutes()
    for (const path of RETIRED) {
      const rec = records.find((r) => r.path === path)
      expect(rec, `旧入口 ${path} 应仍注册（旧书签/深链不 404）`).toBeTruthy()
      expect(rec?.redirect, `${path} 应收敛到新工作台`).toBe('/research-workspace')
    }
  })

  it('上一代会话路由已随旧面移出，不再注册', () => {
    const leaked = router.getRoutes().filter((r) => r.path.startsWith('/research-vnext/session'))
    expect(leaked.map((r) => r.path)).toEqual([])
  })

  it('新工作台路由注册到位', () => {
    const paths = router.getRoutes().map((r) => r.path)
    expect(paths).toContain('/research-workspace')
    expect(paths).toContain('/research-workspace/:projectId/read')
    expect(paths).toContain('/research-workspace/:projectId/write')
  })

  it('`/research` 老入口仍收敛到新工作台（不落 catch-all）', () => {
    const rec = router.getRoutes().find((r) => r.path === '/research')
    expect(rec?.redirect).toBe('/research-workspace')
  })
})
