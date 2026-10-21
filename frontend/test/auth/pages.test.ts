import { mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { describe, expect, it, vi } from 'vitest'

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn() }),
  useRoute: () => ({ query: {} }),
  RouterLink: { template: '<a><slot /></a>' },
  createRouter: () => ({ beforeEach: vi.fn() }),
  createWebHistory: () => ({}),
}))

vi.mock('@/api/auth', () => ({
  authApi: {
    challengeSms: vi.fn(),
    resetPassword: vi.fn().mockResolvedValue({ password_reset: true }),
    currentRoleApplications: vi.fn().mockResolvedValue([]),
    submitRoleApplication: vi.fn(),
    onboardStudent: vi.fn(),
  },
  securityApi: {
    cancelDeletion: vi.fn().mockResolvedValue({ status: 'cancelled' }),
  },
}))

import { authApi, securityApi } from '@/api/auth'
import OtpField from '@/components/auth/OtpField.vue'
import PhoneField from '@/components/auth/PhoneField.vue'
import DeletionCancellation from '@/pages/DeletionCancellation.vue'
import Login from '@/pages/Login.vue'
import PasswordReset from '@/pages/PasswordReset.vue'
import PendingReview from '@/pages/PendingReview.vue'
import Register from '@/pages/Register.vue'
import RoleApplication from '@/pages/RoleApplication.vue'

describe('unified authentication pages', () => {
  it('normalizes +86 phone input and exposes an accessible label', async () => {
    const wrapper = mount(PhoneField, { props: { modelValue: '' } })
    const input = wrapper.get('input')
    await input.setValue('+86 138-0000-0000')
    expect(wrapper.emitted('update:modelValue')?.at(-1)).toEqual(['13800000000'])
    expect(input.attributes('aria-label')).toBe('手机号')
  })

  it('lets users select student, teacher, or researcher before SMS login', () => {
    const wrapper = mount(Login, {
      global: { plugins: [createPinia()], stubs: { RouterLink: { template: '<a><slot /></a>' } } },
    })
    expect(wrapper.get('[role="tablist"]').text()).toContain('短信验证码')
    expect(wrapper.get('[role="tablist"]').text()).toContain('密码登录')
    expect(wrapper.get('select[name="preferred_role"]').text()).toContain('学生端')
    expect(wrapper.get('select[name="preferred_role"]').text()).toContain('教师端')
    expect(wrapper.get('select[name="preferred_role"]').text()).toContain('科研端')
    expect(wrapper.find('select[name="role"]').exists()).toBe(false)
    expect(wrapper.find('select[name="preferred_role"]').find('option[value="admin"]').exists()).toBe(false)
  })

  it('shows teacher fields and requests a registration-purpose verification code', async () => {
    const wrapper = mount(Register, {
      global: { plugins: [createPinia()], stubs: { RouterLink: { template: '<a><slot /></a>' } } },
    })
    expect(wrapper.findComponent(OtpField).props('purpose')).toBe('registration')
    await wrapper.get('select[name="role"]').setValue('teacher')
    expect(wrapper.find('input[name="organization_name"]').exists()).toBe(true)
    expect(wrapper.find('input[name="teaching_stage"]').exists()).toBe(true)
    expect(wrapper.find('input[name="subject"]').exists()).toBe(true)
    expect(wrapper.find('input[name="staff_or_student_id"]').exists()).toBe(true)

    await wrapper.get('select[name="role"]').setValue('researcher')
    expect(wrapper.find('input[name="research_direction"]').exists()).toBe(true)
    expect(wrapper.find('input[name="teaching_stage"]').exists()).toBe(false)
  })

  it('shows role-specific application fields and a clear pending state', async () => {
    const wrapper = mount(RoleApplication, { global: { plugins: [createPinia()] } })
    expect(wrapper.find('[name="teaching_stage"]').exists()).toBe(true)
    await wrapper.get('[name="role"]').setValue('researcher')
    expect(wrapper.find('[name="research_direction"]').exists()).toBe(true)

    const pending = mount(PendingReview, {
      props: { initialApplications: [{ id: 'a1', role: 'teacher', status: 'pending' }] },
      global: { stubs: { RouterLink: { template: '<a><slot /></a>' } } },
    })
    expect(pending.text()).toContain('审核中')
    expect(pending.text()).toContain('教师')
  })

  it('resets a password from a public recovery page', async () => {
    vi.mocked(authApi.challengeSms).mockResolvedValueOnce({ challenge_id: 'reset-challenge' })
    const wrapper = mount(PasswordReset, { global: { stubs: { RouterLink: { template: '<a><slot /></a>' } } } })
    await wrapper.get('[aria-label="手机号"]').setValue('13800000000')
    await wrapper.get('[data-action="send-reset-code"]').trigger('click')
    await wrapper.get('[aria-label="短信验证码"]').setValue('123456')
    await wrapper.get('[aria-label="新密码"]').setValue('correct horse battery staple')
    await wrapper.get('[data-action="submit-password-reset"]').trigger('click')
    expect(authApi.resetPassword).toHaveBeenCalledWith({
      phone: '13800000000', challenge_id: 'reset-challenge', code: '123456', password: 'correct horse battery staple',
    })
    expect(wrapper.text()).toContain('密码已重置')
  })

  it('cancels account deletion without requiring an active session', async () => {
    vi.mocked(authApi.challengeSms).mockResolvedValueOnce({ challenge_id: 'cancel-challenge' })
    const wrapper = mount(DeletionCancellation, { global: { stubs: { RouterLink: { template: '<a><slot /></a>' } } } })
    await wrapper.get('[aria-label="手机号"]').setValue('13800000000')
    await wrapper.get('[data-action="send-cancel-code"]').trigger('click')
    await wrapper.get('[aria-label="短信验证码"]').setValue('123456')
    await wrapper.get('[data-action="submit-deletion-cancel"]').trigger('click')
    expect(securityApi.cancelDeletion).toHaveBeenCalledWith({
      phone: '13800000000', challenge_id: 'cancel-challenge', code: '123456',
    })
    expect(wrapper.text()).toContain('注销申请已取消')
  })
})
