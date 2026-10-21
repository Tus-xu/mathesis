import { describe, it, expect } from 'vitest'
import { router, roleHome } from '@/router'

describe('teacher v3 router contract', () => {
  const v3Paths = [
    '/teacher-v3/today', '/teacher-v3/prep', '/teacher-v3/slides', '/teacher-v3/bank', '/teacher-v3/quiz',
    '/teacher-v3/assign', '/teacher-v3/classroom', '/teacher-v3/insights', '/teacher-v3/resources',
    '/teacher-v3/prep-templates',
    // R1 外框嵌入（G1）：备课中心工作台/历史页同样进 TeacherV3Layout
    '/teacher-v3/prep/w/:sessionId/:tab?', '/teacher-v3/prep/history',
    // tel PLAN §8.2（B5）：班级管理与批改工作台独立成页（8000 直调数据面）
    '/teacher-v3/classes', '/teacher-v3/grading',
  ]

  it('defines exactly the 14 teacher-v3 workspaces nested under TeacherV3Layout', () => {
    const v3Routes = router.getRoutes().filter((r) => r.meta?.teacherV3)
    expect(v3Routes.length).toBe(14)
    const found = new Set(v3Routes.map((r) => r.path))
    for (const p of v3Paths) expect(found.has(p), `missing ${p}`).toBe(true)
    for (const r of v3Routes) {
      expect(r.meta.teacher, `${r.path} keeps teacher role guard`).toBe(true)
      expect(r.meta.requiresRole, `${r.path} requires teacher role`).toBe('teacher')
      expect(String(r.meta.scene), `${r.path} has scene`).toMatch(/^teacher\.v3\./)
      // R1 外框不变量 G1：每条 teacher-v3 路由都必须挂在 TeacherV3Layout 之下
      // （平台侧栏/顶栏/悬浮 AI 助教随之在页面可见；备课中心不得顶层注册脱框）。
      const concrete = r.path.replace(/:sessionId/g, 'x').replace(/:tab\?/g, 'y')
      const resolved = router.resolve(concrete)
      expect(resolved.matched.length, `${r.path} nests under the layout`).toBeGreaterThanOrEqual(2)
      expect(
        String(resolved.matched[0].components?.default),
        `${r.path} parent is TeacherV3Layout`,
      ).toContain('TeacherV3Layout.vue')
    }
  })

  it('no legacy V1/V2 teacher routes remain', () => {
    const legacy = router.getRoutes().filter((r) => r.meta?.teacher && !r.meta?.teacherV3 && r.name !== 'tv3-prep-session')
    expect(legacy.length).toBe(0)
    const existing = new Set(router.getRoutes().map((r) => r.path))
    const gone = ['/teacher/today', '/teacher/prep', '/teacher/assign', '/teacher/grading', '/teacher-v2/today', '/teacher-v2/slides']
    for (const p of gone) expect(existing.has(p), `${p} should be removed`).toBe(false)
  })

  it('each teacher-v3 route lazy-loads a component', async () => {
    const routes = router.getRoutes().filter((r) => r.meta?.teacherV3 && !r.redirect)
    for (const r of routes) {
      const loader = r.components?.default as unknown as () => Promise<unknown> | undefined
      expect(typeof loader).toBe('function')
      await expect((loader as () => Promise<unknown>)()).resolves.toBeTruthy()
    }
  }, 30000)

  it('teacher role home lands on the retained Prep Home', () => {
    expect(roleHome('teacher')).toBe('/teacher-v3/prep')
  })

  // 2026-09-14 契约更新（hybrid kernel → legacy shell）：/teacher-v3/slides 不再是
  // 「直连 SlidesView」的正式路由，而是**兼容闸门**——只有 query 同时含
  // mode=vnext ∧ session ∧ deck 时才加载冻结 SlidesView 作为编辑器壳；其余一律
  // canonical 回退。旧断言（slides 直连 SlidesView 且不得含 SlidesCompatRedirect）
  // 已被该冻结决策推翻，故改为断言闸门形状。细则由 v3SlidesRetired.test.ts 覆盖。
  it('Checkpoint 9 keeps the prep home route, and gates slides behind the VNext compat shell', () => {
    const prep = router.getRoutes().find((r) => r.path === '/teacher-v3/prep')
    const slides = router.getRoutes().find((r) => r.path === '/teacher-v3/slides')
    expect(prep?.redirect).toBeUndefined()
    // G2 单实现冻结：/teacher-v3/prep 唯一实现是 teacher-prep-center/PrepHome
    //（旧 pages/teacher-v3/prep/PrepCenterView 仅冻结保留，不再挂路由）。
    expect(String(prep?.components?.default)).toContain('teacher-prep-center/home/PrepHome.vue')
    expect(String(slides?.components?.default)).toContain('SlidesCompatRedirect.vue')
    expect(String(slides?.components?.default)).not.toContain('SlidesView.vue')
  })
})
