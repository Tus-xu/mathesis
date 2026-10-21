import { describe, expect, it } from 'vitest'
import { router, roleHome } from '@/router'
import { copyFor } from '@/features/teacher-prep-v2/i18n/copy'
import { normalizePrepError } from '@/features/teacher-prep-v2/api/prepApi'
import { readFileSync } from 'node:fs'

describe('Teacher hybrid information architecture', () => {
  it('lands teachers on Prep Home and redirects the retired Today destination', () => {
    expect(roleHome('teacher')).toBe('/teacher-v3/prep')
    const today = router.getRoutes().find((route) => route.path === '/teacher-v3/today')
    expect(today?.redirect).toBe('/teacher-v3/prep')
  })

  it('has a canonical session route outside the legacy global navigation shell', () => {
    const session = router.getRoutes().find((route) => route.path === '/teacher-v3/prep/session/:sessionId')
    expect(session?.meta?.requiresRole).toBe('teacher')
    expect(session?.meta?.teacherV3).toBeUndefined()
    expect(session?.components?.default).toBeTypeOf('function')
  })

  it('does not expose the retired Today label in Teacher navigation source', async () => {
    const source = await import('@/layouts/TeacherV3Layout.vue?raw')
    expect(source.default).not.toContain("label: '今日工作台'")
  })

  it('maps 8100 forbidden errors to teacher-facing copy instead of leaking copy keys', () => {
    expect(copyFor('prep.error.forbidden')).toContain('班级备课权限')
    expect(copyFor('prep.error.forbidden')).not.toContain('prep.error.forbidden')
    const source = readFileSync('src/pages/teacher-v3/prep/PrepCenterView.vue', 'utf8')
    expect(source).toContain('copyFor(error.info.copyKey)')
    expect(source).toContain('PrepApiError')
  })

  it('does not present an 8000 authority outage as a teacher permission denial', () => {
    const error = normalizePrepError({
      status: 503,
      code: 50300,
      detail: '8000 权威不可达：成员校验暂不可用',
    })
    expect(error.kind).toBe('error')
    expect(error.copyKey).toBe('prep.error.authority_unavailable')
    expect(copyFor(error.copyKey)).toContain('身份与班级服务')
    expect(copyFor(error.copyKey)).not.toContain('没有该班级备课权限')
  })

  it('guides teachers without an authoritative class to join or create one', () => {
    const copy = copyFor('prep.home.class_empty')
    expect(copy).toContain('已确认的教学班级')
    expect(copy).toContain('创建或加入')
    expect(copy).not.toContain('备课权限')
  })

  it('does not collapse an authority outage into the empty-class state', async () => {
    const source = await import('@/pages/teacher-v3/prep/PrepHome.vue?raw')
    expect(source.default).toContain('classesLoadError')
    expect(source.default).toContain('prep.error.authority_unavailable')
  })

  it('E-3: lets teachers without a confirmed class start prep (send gate no longer requires classId)', async () => {
    const home = await import('@/pages/teacher-v3/prep/PrepHome.vue?raw')
    expect(home.default).not.toContain('!draft.trim() || busy || !selectedClassId')
    expect(home.default).not.toContain('props.busy || !selectedClassId.value')
    expect(home.default).toContain("selectedClassId.value || undefined")
    const center = await import('@/pages/teacher-v3/prep/PrepCenterView.vue?raw')
    expect(center.default).not.toContain('|| !input.classId')
    expect(center.default).toContain('classId?: string')
    // 诚实降级文案：可用性提示替代阻断
    expect(copyFor('prep.home.class_optional')).toContain('未绑定班级也可备课')
    expect(copyFor('prep.home.class_optional_hint')).toContain('学情画像')
    const bridge = await import('@/pages/teacher-v3/prep/legacyKernelBridge.ts?raw')
    expect(bridge.default).toContain('classId?: string')
    expect(bridge.default).toContain('class_id: input.classId')
  })
})
