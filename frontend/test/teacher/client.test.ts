import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, ApiError } from '@/api/client'

function mockFetch(impl: (...a: unknown[]) => Promise<Response> | Response) {
  const fn = vi.fn(impl)
  vi.stubGlobal('fetch', fn)
  return fn
}

describe('extended api client (backward compatible)', () => {
  afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

  it('sends custom headers, idempotency key, body and returns envelope data', async () => {
    const fn = mockFetch(async (url: unknown, init: any) => {
      expect(init.headers['X-Test']).toBe('yes')
      expect(init.headers['Content-Type']).toBe('application/json')
      expect(init.headers['Idempotency-Key']).toBe('idem-1')
      return new Response(JSON.stringify({ code: 0, message: 'ok', data: { ok: true }, request_id: 'r1' }), {
        status: 200, headers: { 'content-type': 'application/json' },
      })
    })
    const job: any = await api.raw('POST', '/teacher/x', { body: { a: 1 }, headers: { 'X-Test': 'yes' }, idempotencyKey: 'idem-1' })
    expect(job.data).toEqual({ ok: true })
    expect(job.request_id).toBe('r1')
    expect(fn).toHaveBeenCalledTimes(1)
  })

  it('handles 204 without parsing JSON', async () => {
    mockFetch(async () => new Response(null, { status: 204 }))
    const job: any = await api.raw('DELETE', '/teacher/x')
    expect(job.status).toBe(204)
    expect(job.data).toBeUndefined()
  })

  it('maps envelope error to ApiError code', async () => {
    mockFetch(async () => new Response(JSON.stringify({ code: 40901, message: 'version_conflict', data: null }), { status: 200 }))
    await expect(api.raw('GET', '/teacher/artifacts/a')).rejects.toMatchObject({ code: 40901 })
  })

  it('propagates abort signal and surfaces ApiError(-2)', async () => {
    const ctrl = new AbortController()
    mockFetch(async (_url: unknown, init: any) => new Promise((_res, rej) => {
      init.signal.addEventListener('abort', () => rej(Object.assign(new Error('aborted'), { name: 'AbortError' })))
    }))
    const p = api.raw('GET', '/teacher/today', { signal: ctrl.signal })
    ctrl.abort()
    await expect(p).rejects.toMatchObject({ code: -2 })
  })
})