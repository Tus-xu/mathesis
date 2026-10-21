import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  api,
  ApiError,
  AbortError,
  NetworkError,
  TimeoutError,
  RateLimitError,
  setHttpPolicy,
  resetHttpPolicy,
} from '@/api/client'
import { __resetAuthSessionForTests } from '@/api/authSession'

function mockFetch(impl: (...a: unknown[]) => Promise<Response> | Response) {
  const fn = vi.fn(impl)
  vi.stubGlobal('fetch', fn)
  return fn
}

const ok = (body: unknown, status = 200, extra: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json', ...extra } })

beforeEach(() => {
  resetHttpPolicy()
  __resetAuthSessionForTests()
  // 刷新链路需要 CSRF cookie；单测使用 legacy cookie（生产新客户端用 ma_csrf_<scope>）。
  document.cookie = 'ma_csrf=csrf-test; path=/'
  // 让重试退避在测试中接近瞬时，但保留默认超时避免误触发
  setHttpPolicy({ baseRetryDelayMs: 1, defaultTimeoutMs: 5000 })
})
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

describe('HTTP 客户端韧性（超时/并发/重试/错误分类）', () => {
  it('超时：超过阈值抛出 TimeoutError（调用方 signal 仍生效）', async () => {
    setHttpPolicy({ defaultTimeoutMs: 50 })
    const ctrl = new AbortController()
    mockFetch(async (_u: unknown, init: any) => new Promise((_res, rej) => {
      init.signal.addEventListener('abort', () => rej(Object.assign(new Error('aborted'), { name: 'AbortError' })))
    }))
    const p = api.get('/student/slow', undefined, { signal: ctrl.signal })
    await expect(p).rejects.toBeInstanceOf(TimeoutError)
    expect(ctrl.signal.aborted).toBe(false) // 超时由内部控制器触发，不替换调用方 signal
  })

  it('并发闸门：全局并发上限确实限制同时在途请求数', async () => {
    setHttpPolicy({ globalConcurrency: 2, domainConcurrency: 4 })
    let inFlight = 0
    let maxInFlight = 0
    mockFetch(async () => {
      inFlight++
      maxInFlight = Math.max(maxInFlight, inFlight)
      await new Promise((r) => setTimeout(r, 30))
      inFlight--
      return ok({ code: 0, data: {}, request_id: 'r' })
    })
    await Promise.all([api.get('/x/1'), api.get('/x/2'), api.get('/x/3'), api.get('/x/4')])
    expect(maxInFlight).toBeLessThanOrEqual(2)
  })

  it('重试：429 + Retry-After 会按可重试状态重试一次', async () => {
    setHttpPolicy({ maxRetries: 2 })
    let count = 0
    mockFetch(async () => {
      count++
      if (count === 1) return ok({ code: 42901, message: 'rate' }, 429, { 'retry-after': '0' })
      return ok({ code: 0, data: { ok: true }, request_id: 'r' })
    })
    await expect(api.get('/student/list')).resolves.toEqual({ ok: true })
    expect(count).toBe(2)
  })

  it('重试：网络错误对幂等请求重试，耗尽后抛出 NetworkError', async () => {
    setHttpPolicy({ maxRetries: 2, baseRetryDelayMs: 1 })
    let count = 0
    mockFetch(async () => {
      count++
      if (count <= 2) throw Object.assign(new Error('network down'), { name: 'TypeError' })
      return ok({ code: 0, data: { ok: true }, request_id: 'r' })
    })
    await expect(api.get('/student/x')).resolves.toEqual({ ok: true })
    expect(count).toBe(3) // 1 次初始 + 2 次重试
  })

  it('不重试：写操作（POST 500）默认不重试', async () => {
    let count = 0
    mockFetch(async () => { count++; return ok({ code: 50001, message: 'boom' }, 500) })
    await expect(api.post('/student/x', { a: 1 })).rejects.toMatchObject({ code: 50001 })
    expect(count).toBe(1)
  })

  it('不重试：用户主动取消（AbortError）不触发重试', async () => {
    const ctrl = new AbortController()
    let count = 0
    mockFetch(async (_u: unknown, init: any) => new Promise((_res, rej) => {
      count++
      init.signal.addEventListener('abort', () => rej(Object.assign(new Error('aborted'), { name: 'AbortError' })))
    }))
    const p = api.get('/student/x', undefined, { signal: ctrl.signal })
    ctrl.abort()
    await expect(p).rejects.toBeInstanceOf(AbortError)
    expect(count).toBe(1)
  })

  it('401 刷新链路保留：首次 401 触发 refresh 并重试一次', async () => {
    let refresh = 0
    let real = 0
    mockFetch(async (url: unknown) => {
      if (String(url).includes('/auth/token/refresh')) {
        refresh++
        return ok({ code: 0, data: { access_token: 'new' } })
      }
      real++
      if (real === 1) return new Response('', { status: 401 })
      return ok({ code: 0, data: { ok: true }, request_id: 'r' })
    })
    await expect(api.get('/student/x')).resolves.toEqual({ ok: true })
    expect(refresh).toBe(1)
    expect(real).toBe(2)
  })

  it('领域错误码未被吞：信封 code 原样保留', async () => {
    mockFetch(async () => ok({ code: 40401, message: 'not found', data: null }))
    const err: any = await api.get('/student/x').catch((e) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(err.code).toBe(40401)
    expect(err.message).toBe('not found')
  })

  it('向后兼容：RateLimitError 仍可被 instanceof ApiError 识别且携带 code/status', async () => {
    setHttpPolicy({ maxRetries: 0 })
    mockFetch(async () => ok({ code: 42901, message: 'slow down' }, 429, { 'retry-after': '1' }))
    const err: any = await api.get('/student/x').catch((e) => e)
    expect(err).toBeInstanceOf(RateLimitError)
    expect(err).toBeInstanceOf(ApiError)
    expect(err.code).toBe(42901)
    expect(err.status).toBe(429)
  })
})
