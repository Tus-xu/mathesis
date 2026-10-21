import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  __resetAuthSessionForTests,
  getAuthScope,
  getCsrfToken,
  refreshAccessToken,
  setAuthScope,
} from '@/api/authSession'

function envelope(data: unknown, status = 200) {
  return new Response(JSON.stringify({ code: 0, message: 'ok', data }), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

describe('per-tab auth scope', () => {
  beforeEach(() => {
    __resetAuthSessionForTests()
    document.cookie = 'ma_csrf=; Max-Age=0; path=/'
    document.cookie = 'ma_csrf_teacher=; Max-Age=0; path=/'
    document.cookie = 'ma_csrf_student=; Max-Age=0; path=/'
    window.history.replaceState({}, '', '/')
  })
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('uses the tab scope and its scoped CSRF cookie', async () => {
    setAuthScope('teacher')
    document.cookie = 'ma_csrf_teacher=teacher-csrf; path=/'
    document.cookie = 'ma_csrf=legacy-csrf; path=/'
    expect(getAuthScope()).toBe('teacher')
    expect(getCsrfToken()).toBe('teacher-csrf')

    const fetchMock = vi.fn(async (url: string, init: RequestInit) => {
      expect(url).toBe('/api/auth/token/refresh')
      expect((init.headers as Record<string, string>)['X-Auth-Scope']).toBe('teacher')
      expect((init.headers as Record<string, string>)['X-CSRF-Token']).toBe('teacher-csrf')
      return envelope({ access_token: 'teacher-access', expires_in: 900, active_role: 'teacher' })
    })
    vi.stubGlobal('fetch', fetchMock)
    await expect(refreshAccessToken()).resolves.toMatchObject({ active_role: 'teacher' })
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('falls back to another role cookie only during bootstrap', async () => {
    document.cookie = 'ma_csrf_teacher=teacher-csrf; path=/'
    const fetchMock = vi.fn(async (_url: string, init: RequestInit) => {
      expect((init.headers as Record<string, string>)['X-Auth-Scope']).toBe('teacher')
      return envelope({ access_token: 'teacher-access', expires_in: 900, active_role: 'teacher' })
    })
    vi.stubGlobal('fetch', fetchMock)
    await expect(
      refreshAccessToken({ scope: 'student', allowFallback: true }),
    ).resolves.toMatchObject({ active_role: 'teacher' })
  })
})
