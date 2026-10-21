import { describe, expect, it } from 'vitest'

import { router, roleHome } from '@/router'

describe('Checkpoint 9 legacy formal Teacher routes', () => {
  // 2026-09-14 契约更新（hybrid kernel → legacy shell）：slides 正式路由改为兼容闸门，
  // 由 SlidesCompatRedirect 按 mode=vnext ∧ session ∧ deck 决定是否加载冻结 SlidesView。
  // 旧断言「slides 直连 SlidesView」与冻结边界冲突，已修正为闸门形状。
  it('keeps formal prep on the prep-center home and gates slides through the compat shell', () => {
    const prep = router.getRoutes().find((route) => route.path === '/teacher-v3/prep')
    const slides = router.getRoutes().find((route) => route.path === '/teacher-v3/slides')
    expect(prep?.redirect).toBeUndefined()
    // G2 单实现冻结（R1 外框嵌入）：/teacher-v3/prep 唯一实现 = teacher-prep-center/PrepHome，
    // 且挂在 TeacherV3Layout 之下（旧 pages/teacher-v3/prep/PrepCenterView 冻结保留、不挂路由）。
    expect(String(prep?.components?.default)).toContain('teacher-prep-center/home/PrepHome.vue')
    expect(String(slides?.components?.default)).toContain('SlidesCompatRedirect.vue')
    expect(String(slides?.components?.default)).not.toContain('SlidesView.vue')
  })

  it('never uses the rejected immersive route as Teacher role landing', () => {
    expect(roleHome('teacher')).not.toContain('/teacher-v3/prep-immersive')
  })
})
