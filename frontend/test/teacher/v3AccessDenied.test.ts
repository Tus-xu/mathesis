// A0 M1-2 · 40301 role_denied 统一落点：教师端 REST 拿到 40301 → 跳 /teacher-v3/denied 提示页（不白屏）
// 提示页自身可渲染、有出路（返回工作台/重新登录）。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createRouter, createMemoryHistory } from 'vue-router'

const rawFn = vi.fn()
const routerPush = vi.fn(async (_to?: string) => undefined)

vi.mock('@/api/client', () => ({
  ApiError: class ApiError extends Error {
    code: number
    constructor(code: number, message?: string) { super(message || `请求失败 (${code})`); this.code = code }
  },
  api: { raw: (...a: unknown[]) => rawFn(...a) },
}))
vi.mock('@/router', () => ({ router: { push: (to: string) => routerPush(to) } }))

import { ApiError } from '@/api/client'
import { teacherRequest } from '@/api/teacher/client'
import AccessDeniedPage from '@/pages/teacher-v3/AccessDeniedPage.vue'

beforeEach(() => {
  vi.clearAllMocks()
})

describe('teacher/client · 40301 统一跳提示页', () => {
  it('40301 → 跳 /teacher-v3/denied 且错误照常抛出', async () => {
    rawFn.mockRejectedValue(new ApiError(40301, 'role_denied'))
    await expect(teacherRequest('GET', '/teacher-v3/today')).rejects.toMatchObject({ code: 40301 })
    await flushPromises() // 跳转走动态 import 异步解析
    expect(routerPush).toHaveBeenCalledWith('/teacher-v3/denied')
  })

  it('非 40301 错误不触发跳转', async () => {
    rawFn.mockRejectedValue(new ApiError(500, 'boom'))
    await expect(teacherRequest('GET', '/teacher-v3/today')).rejects.toMatchObject({ code: 500 })
    await flushPromises()
    expect(routerPush).not.toHaveBeenCalled()
  })
})

describe('AccessDeniedPage 提示页', () => {
  it('渲染解释文案与两条出路；「返回我的工作台」回到 /', async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/teacher-v3/denied', component: AccessDeniedPage },
        { path: '/', component: { template: '<div>home</div>' } },
      ],
    })
    router.push('/teacher-v3/denied')
    await router.isReady()
    const w = mount(AccessDeniedPage, { global: { plugins: [router] } })

    expect(w.text()).toContain('当前账号无教师权限')
    expect(w.text()).toContain('教师身份')
    const buttons = w.findAll('button')
    expect(buttons).toHaveLength(2)
    await buttons[0].trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/')
  })
})
