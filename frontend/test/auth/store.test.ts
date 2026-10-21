import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

const authMocks = vi.hoisted(() => ({
  refreshSession: vi.fn(),
  me: vi.fn(),
  loginSms: vi.fn(),
  registerSms: vi.fn(),
  logout: vi.fn(),
  logoutAll: vi.fn(),
  switchRole: vi.fn(),
}))

vi.mock('@/api/auth', () => ({ authApi: authMocks }))

import { useAuthStore } from '@/stores/auth'

describe('auth bootstrap state machine', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('restores a cold session and filters roles to approved bindings', async () => {
    authMocks.refreshSession.mockResolvedValue({ access_token: 'fresh' })
    authMocks.me.mockResolvedValue({
      id: 'u1',
      nickname: '小数',
      active_role: 'student',
      onboarding_status: 'completed',
      status: 'active',
      roles: [
        { role: 'student', status: 'approved' },
        { role: 'teacher', status: 'pending' },
      ],
    })
    const store = useAuthStore()

    await store.bootstrap()

    expect(store.status).toBe('authenticated')
    expect(store.roles).toEqual(['student'])
    expect(store.pendingRoles).toEqual(['teacher'])
    expect(store.isLoggedIn).toBe(true)
  })

  it('becomes anonymous when no refresh session exists', async () => {
    authMocks.refreshSession.mockRejectedValue(new Error('no cookie'))
    const store = useAuthStore()

    await store.bootstrap()

    expect(store.status).toBe('anonymous')
    expect(store.user).toBeNull()
  })

  it('keeps a server-reported pending teacher intent ahead of student onboarding', async () => {
    authMocks.loginSms.mockResolvedValue({
      access_token: 'student-base-token',
      identity_status: 'pending_review',
      pending_role: 'teacher',
      onboarding_required: true,
      user: {
        id: 'u1',
        active_role: 'student',
        onboarding_status: 'required',
        roles: [
          { role: 'student', status: 'approved' },
          { role: 'teacher', status: 'pending' },
        ],
      },
    })
    const store = useAuthStore()

    await store.loginSms({ phone: '13800138000', preferred_role: 'teacher' })

    expect(store.status).toBe('pending_review')
    expect(store.activeRole).toBe('student')
    expect(store.user).toMatchObject({ pending_role: 'teacher' })
  })

  it('keeps server-reported needs-more-info ahead of student onboarding', async () => {
    authMocks.registerSms.mockResolvedValue({
      access_token: 'student-base-token',
      identity_status: 'needs_more_info',
      pending_role: 'researcher',
      onboarding_required: true,
      user: {
        id: 'u2',
        active_role: 'student',
        onboarding_status: 'required',
        roles: [
          { role: 'student', status: 'approved' },
          { role: 'researcher', status: 'needs_more_info' },
        ],
      },
    })
    const store = useAuthStore()

    await store.registerSms({ phone: '13800138000', role: 'researcher' })

    expect(store.status).toBe('needs_more_info')
    expect(store.user).toMatchObject({ pending_role: 'researcher' })
  })

  it('honors an onboarding-required student login even when the user body is completed', async () => {
    authMocks.loginSms.mockResolvedValue({
      access_token: 'student-token',
      identity_status: 'authenticated',
      onboarding_required: true,
      user: {
        id: 'u3',
        active_role: 'student',
        onboarding_status: 'completed',
        roles: [{ role: 'student', status: 'approved' }],
      },
    })
    const store = useAuthStore()

    await store.loginSms({ phone: '13800138000' })

    expect(store.status).toBe('onboarding')
  })

  it('honors an onboarding-required student registration even when the user body is completed', async () => {
    authMocks.registerSms.mockResolvedValue({
      access_token: 'student-token',
      identity_status: 'authenticated',
      onboarding_required: true,
      user: {
        id: 'u4',
        active_role: 'student',
        onboarding_status: 'completed',
        roles: [{ role: 'student', status: 'approved' }],
      },
    })
    const store = useAuthStore()

    await store.registerSms({ phone: '13800138000', role: 'student' })

    expect(store.status).toBe('onboarding')
  })
})
