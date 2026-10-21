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

/**
 * activeRole 与 roles 错位时的自愈（2026-10-15 线上卡死修复）。
 *
 * 毒态：activeRole='teacher' 但 roles 不含 teacher → 路由守卫把
 * roleHome('teacher') 再拒回去 → vue-router "Infinite redirect" 中止 →
 * 全站卡死无出口。recoverActiveRole 必须把身份切回一个已批准角色；
 * 一个可用角色都没有时必须强制登出，绝不把用户留在死循环里。
 */
describe('auth recoverActiveRole (role/binding mismatch self-heal)', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  const applyPoisonedTeacher = (approvedRoles: string[]) => {
    const store = useAuthStore()
    store.applyIdentity({
      id: 'u1',
      nickname: '测试',
      active_role: 'teacher',
      status: 'active',
      onboarding_status: 'completed',
      roles: approvedRoles.map((role) => ({ role, status: 'approved', verified: true })),
    })
    return store
  }

  it('switches back to student when teacher binding is lost but student is approved', async () => {
    const store = applyPoisonedTeacher(['student', 'researcher'])
    authMocks.switchRole.mockResolvedValue({ access_token: 'student-token' })
    authMocks.me.mockResolvedValue({
      id: 'u1',
      active_role: 'student',
      status: 'active',
      onboarding_status: 'completed',
      roles: [{ role: 'student', status: 'approved' }],
    })

    await expect(store.recoverActiveRole()).resolves.toBe(true)

    expect(authMocks.switchRole).toHaveBeenCalledWith('student')
    expect(store.activeRole).toBe('student')
    expect(store.roles).toEqual(['student'])
  })

  it('skips a failing candidate and heals with the next approved role', async () => {
    // 毒态：activeRole=teacher 但 teacher 绑定已失效（不在 roles），student 绑定也坏
    const store = applyPoisonedTeacher(['student', 'researcher'])
    authMocks.switchRole.mockImplementation(async (role: string) => {
      if (role === 'student') throw Object.assign(new Error('role not approved'), { code: 40301 })
      return { access_token: `token-${role}` }
    })
    authMocks.me.mockResolvedValue({
      id: 'u1',
      active_role: 'researcher',
      status: 'active',
      onboarding_status: 'completed',
      roles: [{ role: 'researcher', status: 'approved' }],
    })

    await expect(store.recoverActiveRole()).resolves.toBe(true)
    expect(authMocks.switchRole).toHaveBeenNthCalledWith(1, 'student')
    expect(authMocks.switchRole).toHaveBeenNthCalledWith(2, 'researcher')
    expect(store.activeRole).toBe('researcher')
  })

  it('forces logout when no approved role remains', async () => {
    const store = applyPoisonedTeacher([])
    authMocks.logout.mockResolvedValue({ logged_out: true })

    await expect(store.recoverActiveRole()).resolves.toBe(false)

    expect(authMocks.switchRole).not.toHaveBeenCalled()
    expect(authMocks.logout).toHaveBeenCalledTimes(1)
    expect(store.status).toBe('anonymous')
  })

  it('still ends anonymous when the forced logout API also fails', async () => {
    const store = applyPoisonedTeacher([])
    authMocks.logout.mockRejectedValue(new Error('network dead'))

    await expect(store.recoverActiveRole()).resolves.toBe(false)
    expect(store.status).toBe('anonymous')
    expect(store.user).toBeNull()
  })

  it('recovers a healthy active role without touching anything', async () => {
    const store = applyPoisonedTeacher(['teacher'])
    // activeRole 本身有批准绑定 → 自愈是 no-op，不应误切角色/误登出
    await expect(store.recoverActiveRole()).resolves.toBe(true)
    expect(authMocks.switchRole).not.toHaveBeenCalled()
    expect(authMocks.logout).not.toHaveBeenCalled()
  })
})
