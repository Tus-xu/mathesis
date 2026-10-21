import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { api } from '@/api/client'
import {
  __resetAuthSessionForTests,
  getAccessToken,
  setAccessToken,
} from '@/api/authSession'

function envelope(data: unknown, status = 200) {
  return new Response(JSON.stringify({ code: 0, message: 'ok', data }), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

describe('in-memory authenticated client', () => {
  beforeEach(() => {
    localStorage.clear()
    document.cookie = 'ma_csrf=csrf-test; path=/'
    __resetAuthSessionForTests()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('keeps access tokens only in memory and includes cookies plus CSRF', async () => {
    setAccessToken('memory-token')
    const fetchMock = vi.fn(async (_url: unknown, init: RequestInit) => {
      expect(init.credentials).toBe('include')
      expect((init.headers as Record<string, string>).Authorization).toBe('Bearer memory-token')
      expect((init.headers as Record<string, string>)['X-CSRF-Token']).toBe('csrf-test')
      return envelope({ saved: true })
    })
    vi.stubGlobal('fetch', fetchMock)

    await expect(api.post('/identity/save', { value: 1 })).resolves.toEqual({ saved: true })
    expect(getAccessToken()).toBe('memory-token')
    expect(localStorage.getItem('ma_token')).toBeNull()
    expect(sessionStorage.getItem('ma_token')).toBeNull()
  })

  it('singleflights refresh for three simultaneous 401 responses and replays once', async () => {
    setAccessToken('expired-token')
    const attempts = new Map<string, number>()
    const fetchMock = vi.fn(async (url: string, init: RequestInit) => {
      if (url === '/api/auth/token/refresh') {
        expect(init.credentials).toBe('include')
        return envelope({ access_token: 'fresh-token', expires_in: 900 })
      }
      const count = (attempts.get(url) || 0) + 1
      attempts.set(url, count)
      if (count === 1) return new Response('{}', { status: 401 })
      expect((init.headers as Record<string, string>).Authorization).toBe('Bearer fresh-token')
      return envelope({ url })
    })
    vi.stubGlobal('fetch', fetchMock)

    const results = await Promise.all([
      api.get('/student/a'),
      api.get('/student/b'),
      api.get('/student/c'),
    ])

    expect(results).toHaveLength(3)
    expect(fetchMock.mock.calls.filter(([url]) => url === '/api/auth/token/refresh')).toHaveLength(1)
    expect([...attempts.values()]).toEqual([2, 2, 2])
    expect(getAccessToken()).toBe('fresh-token')
  })
})
