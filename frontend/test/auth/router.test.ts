import { describe, expect, it } from 'vitest'

import { resolveAuthNavigation } from '@/router'

const route = (path: string, meta: Record<string, unknown> = {}) => ({ path, fullPath: path, meta })

describe('role-aware auth navigation', () => {
  it('preserves deep links for anonymous users', () => {
    expect(resolveAuthNavigation(route('/teacher/prep', { requiresRole: 'teacher' }), { status: 'anonymous' }))
      .toEqual({ path: '/login', query: { redirect: '/teacher/prep' } })
  })

  it('routes onboarding, pending review, and deletion identities to their only allowed areas', () => {
    expect(resolveAuthNavigation(route('/overview'), { status: 'onboarding' })).toEqual({ path: '/onboarding/student' })
    expect(resolveAuthNavigation(route('/'), { status: 'onboarding' })).toEqual({ path: '/onboarding/student' })
    expect(resolveAuthNavigation(route('/teacher/today'), { status: 'pending_review' })).toEqual({ path: '/identity/pending' })
    expect(resolveAuthNavigation(route('/overview'), { status: 'deletion_pending' })).toEqual({ path: '/account/security' })
  })

  it('requires an approved active role for privileged workspaces', () => {
    expect(resolveAuthNavigation(route('/teacher/today', { requiresRole: 'teacher' }), {
      status: 'authenticated', activeRole: 'student', roles: ['student'],
    })).toEqual({ path: '/dialog' })
    expect(resolveAuthNavigation(route('/admin/identity/applications', { requiresRole: 'admin' }), {
      status: 'authenticated', activeRole: 'admin', roles: ['student', 'admin'],
    })).toBe(true)
  })

  it('routes an approved researcher root to the research workspace', () => {
    // P1：科研端唯一落地是新的 Research Workspace 工作台（旧 paper-workbench 已退役）。
    expect(resolveAuthNavigation(route('/'), {
      status: 'authenticated', activeRole: 'researcher', roles: ['researcher'],
    })).toEqual({ path: '/research-workspace' })
  })

  it('keeps needs-more-info identities out of the student home', () => {
    expect(resolveAuthNavigation(route('/overview'), { status: 'needs_more_info' }))
      .toEqual({ path: '/identity/pending' })
  })
})
