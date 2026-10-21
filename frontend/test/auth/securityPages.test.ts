import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'

const apiMocks = vi.hoisted(() => ({
  sessions: vi.fn().mockResolvedValue([]),
  revokeSession: vi.fn().mockResolvedValue({ revoked: true }),
  challengeSms: vi.fn().mockImplementation((_phone, purpose) => Promise.resolve({ challenge_id: `${purpose}-challenge` })),
  resetPassword: vi.fn().mockResolvedValue({ password_reset: true }),
  changePhone: vi.fn().mockResolvedValue({ phone_changed: true }),
  requestDeletion: vi.fn().mockResolvedValue({ execute_after: '2026-08-29T00:00:00Z' }),
  deletionStatus: vi.fn().mockResolvedValue({ status: 'none' }),
  cancelDeletion: vi.fn().mockResolvedValue({ cancelled: true }),
  logoutAll: vi.fn().mockResolvedValue(undefined),
  sendReauthCode: vi.fn().mockResolvedValue({ challenge_id: 'admin-reauth-challenge' }),
  reauthenticate: vi.fn().mockResolvedValue({ reauthenticated: true }),
  approve: vi.fn().mockResolvedValue({ status: 'approved' }),
}))

vi.mock('@/api/auth', () => ({
  authApi: {
    challengeSms: apiMocks.challengeSms,
    resetPassword: apiMocks.resetPassword,
  },
  securityApi: {
    sessions: apiMocks.sessions,
    revokeSession: apiMocks.revokeSession,
    changePhone: apiMocks.changePhone,
    requestDeletion: apiMocks.requestDeletion,
    deletionStatus: apiMocks.deletionStatus,
    cancelDeletion: apiMocks.cancelDeletion,
    sendReauthCode: apiMocks.sendReauthCode,
    reauthenticate: apiMocks.reauthenticate,
  },
  adminIdentityApi: { approve: apiMocks.approve },
}))

vi.mock('@/stores/auth', () => ({
  useAuthStore: () => ({ logoutAll: apiMocks.logoutAll }),
}))

import AccountSecurity from '@/pages/AccountSecurity.vue'
import AdminIdentityReview from '@/pages/admin/AdminIdentityReview.vue'

describe('account security and identity administration', () => {
  it('revokes a selected device session', async () => {
    const wrapper = mount(AccountSecurity, {
      props: { initialSessions: [{ id: 's1', device_name: 'Chrome · Windows', current: false, revoked: false }] },
      global: { stubs: { RouterLink: { template: '<a><slot /></a>' } } },
    })
    await wrapper.get('[data-action="revoke-session"]').trigger('click')
    expect(apiMocks.revokeSession).toHaveBeenCalledWith('s1')
    expect(wrapper.text()).toContain('已撤销')
  })

  it('changes phone only after old and new number challenges', async () => {
    const wrapper = mount(AccountSecurity, {
      props: { initialSessions: [], initialDeletion: { status: 'none' } },
      global: { stubs: { RouterLink: { template: '<a><slot /></a>' } } },
    })
    await wrapper.get('[data-action="open-phone-change"]').trigger('click')
    await wrapper.get('[aria-label="当前手机号"]').setValue('13800000000')
    await wrapper.get('[aria-label="新手机号"]').setValue('13900000000')
    await wrapper.get('[data-action="send-old-phone-code"]').trigger('click')
    await wrapper.get('[data-action="send-new-phone-code"]').trigger('click')
    await wrapper.get('[aria-label="当前手机号验证码"]').setValue('123456')
    await wrapper.get('[aria-label="新手机号验证码"]').setValue('654321')
    await wrapper.get('[data-action="submit-phone-change"]').trigger('click')

    expect(apiMocks.challengeSms).toHaveBeenCalledWith('13800000000', 'phone_change_old')
    expect(apiMocks.challengeSms).toHaveBeenCalledWith('13900000000', 'phone_change_new')
    expect(apiMocks.changePhone).toHaveBeenCalledWith({
      old_challenge_id: 'phone_change_old-challenge', old_code: '123456',
      new_phone: '13900000000', new_challenge_id: 'phone_change_new-challenge', new_code: '654321',
    })
    expect(apiMocks.logoutAll).toHaveBeenCalled()
  })

  it('resets password with an isolated challenge and signs out all sessions', async () => {
    const wrapper = mount(AccountSecurity, {
      props: { initialSessions: [], initialDeletion: { status: 'none' } },
      global: { stubs: { RouterLink: { template: '<a><slot /></a>' } } },
    })
    await wrapper.get('[aria-label="重置密码手机号"]').setValue('13800000000')
    await wrapper.get('[data-action="send-password-reset-code"]').trigger('click')
    await wrapper.get('[aria-label="重置密码验证码"]').setValue('123456')
    await wrapper.get('[aria-label="新密码"]').setValue('correct horse battery staple')
    await wrapper.get('[data-action="reset-password"]').trigger('click')

    expect(apiMocks.resetPassword).toHaveBeenCalledWith({
      phone: '13800000000', challenge_id: 'password_reset-challenge',
      code: '123456', password: 'correct horse battery staple',
    })
    expect(apiMocks.logoutAll).toHaveBeenCalled()
  })

  it('cancels a pending deletion during the cooling period', async () => {
    const wrapper = mount(AccountSecurity, {
      props: { initialSessions: [], initialDeletion: { status: 'deletion_pending', execute_after: '2026-08-29T00:00:00Z' } },
      global: { stubs: { RouterLink: { template: '<a><slot /></a>' } } },
    })
    await wrapper.get('[aria-label="注销账号手机号"]').setValue('13800000000')
    await wrapper.get('[data-action="send-deletion-cancel-code"]').trigger('click')
    await wrapper.get('[aria-label="注销取消验证码"]').setValue('123456')
    await wrapper.get('[data-action="cancel-deletion"]').trigger('click')

    expect(apiMocks.cancelDeletion).toHaveBeenCalledWith({
      phone: '13800000000', challenge_id: 'deletion_cancel-challenge', code: '123456',
    })
    expect(wrapper.text()).toContain('注销申请已取消')
  })

  it('logs out every session on explicit request', async () => {
    const wrapper = mount(AccountSecurity, {
      props: { initialSessions: [], initialDeletion: { status: 'none' } },
      global: { stubs: { RouterLink: { template: '<a><slot /></a>' } } },
    })
    await wrapper.get('[data-action="logout-all"]').trigger('click')
    expect(apiMocks.logoutAll).toHaveBeenCalled()
  })

  it('filters pending applications and sends recent re-auth on approval', async () => {
    const wrapper = mount(AdminIdentityReview, {
      props: {
        initialApplications: [{ id: 'a1', role: 'teacher', status: 'pending', organization_name: '示例中学' }],
        initialReauthProof: 'recent-proof',
      },
      global: { stubs: { RouterLink: { template: '<a><slot /></a>' } } },
    })
    await wrapper.get('[data-action="approve"]').trigger('click')
    expect(apiMocks.approve).toHaveBeenCalledWith('a1', expect.any(String))
    expect(wrapper.text()).toContain('已通过')
  })

  it('performs password plus SMS re-authentication before admin mutation', async () => {
    const wrapper = mount(AdminIdentityReview, {
      props: { initialApplications: [] },
      global: { stubs: { RouterLink: { template: '<a><slot /></a>' } } },
    })
    await wrapper.get('[aria-label="管理员手机号"]').setValue('13800000000')
    await wrapper.get('[aria-label="管理员密码"]').setValue('strong password')
    await wrapper.get('[aria-label="二次验证码"]').setValue('123456')
    await wrapper.get('button:nth-of-type(1)')
    await wrapper.findAll('button').find((button) => button.text() === '发送验证码')!.trigger('click')
    await wrapper.findAll('button').find((button) => button.text() === '完成认证')!.trigger('click')

    expect(apiMocks.sendReauthCode).toHaveBeenCalledWith('13800000000')
    expect(apiMocks.reauthenticate).toHaveBeenCalledWith({
      password: 'strong password', challenge_id: 'admin-reauth-challenge', code: '123456',
    })
    expect(wrapper.text()).toContain('近期认证有效')
  })
})
