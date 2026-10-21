// A0 M1-3 · v3Sse 统一重连层单测（fetch-event-source 语义，fetch.js 源码结论见 teacher-research/notes/A0-notes.md）
// 覆盖：默认不重连（既有纪律）／reconnect 指数退避×3／Last-Event-ID 自动携带／致命错误（40301）不重试并跳提示页
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { v3Sse, SseFatalError } from '@/api/teacherV3'

const routerPush = vi.fn(async (_to?: string) => undefined)
vi.mock('@/router', () => ({ router: { push: (to: string) => routerPush(to) } }))

const enc = new TextEncoder()

/** SSE 响应（实测 fetch-event-source + ReadableStream 语义，见 notes/A0-notes.md 主题一）：
 * - 事件入队与 error 必须分离——error 会清空「已入队未读」chunk（WHATWG 规范），
 *   先入队、延时 5ms 再 error 才能模拟「事件送达后断线」；
 * - error 前 close 会让 error 变 no-op（正常收流 → onclose → resolve，不重试）。 */
function sseResponse(segments: string[], { error = false, close = true, status = 200, body }: { error?: boolean; close?: boolean; status?: number; body?: string } = {}) {
  if (body !== undefined) {
    return new Response(body, { status, headers: { 'content-type': 'application/json' } })
  }
  const stream = new ReadableStream({
    start(c) {
      queueMicrotask(() => { for (const s of segments) c.enqueue(enc.encode(s)) })
      if (error) setTimeout(() => c.error(new Error('net down')), 5)
      else if (close) queueMicrotask(() => c.close())
    },
  })
  return new Response(stream, { status, headers: { 'content-type': 'text/event-stream' } })
}

function evt(id: number, type: string, data: unknown) {
  return `id: ${id}\nevent: ${type}\ndata: ${JSON.stringify(data)}\n\n`
}

beforeEach(() => { vi.useFakeTimers() })
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); vi.clearAllMocks() })

describe('v3Sse · 默认（无 reconnect）＝既有纪律：断线即终止', () => {
  it('连接失败后不重试，finished 拒绝，fetch 仅一次且 body 完整', async () => {
    const fetchFn = vi.fn(async (_url: any, init: any) => { throw new Error('net down') })
    vi.stubGlobal('fetch', fetchFn)
    const onEvent = vi.fn()
    const { finished } = v3Sse('POST', '/teacher-v3/generation/deck', { topic: '椭圆' }, onEvent)

    // 假时钟下 reject 发生在 advance 的 tick 内：必须先同步挂 handler，避免 unhandled rejection
    let rejection: any = null
    const handled = finished.catch((e) => { rejection = e })
    await vi.advanceTimersByTimeAsync(5000)
    await handled
    expect(rejection?.message).toBe('net down')
    expect(fetchFn).toHaveBeenCalledTimes(1)
    // POST body 仍随请求发送
    expect(fetchFn.mock.calls[0][1].body).toBe(JSON.stringify({ topic: '椭圆' }))
  })
})

describe('v3Sse · reconnect=true（课堂双通道语义）', () => {
  it('断线后指数退避重连，Last-Event-ID 自动携带，恢复触发 onRecover', async () => {
    // 库会跨重试复用并持续改写同一 headers 对象（fetch.js getMessages→headers[last-event-id]），
    // 因此必须在 fetch 调用时快照头，事后断言的是「请求当时」的补拉锚点
    const seen: Record<string, string>[] = []
    const fetchFn = vi.fn(async (_url: unknown, init: any) => {
      seen.push({ ...(init.headers || {}) })
      if (fetchFn.mock.calls.length === 1) return sseResponse([evt(7, 'event', { seq: 7 })], { error: true })
      return sseResponse([evt(8, 'event', { seq: 8 }), evt(9, 'event', { seq: 9 })])
    })
    vi.stubGlobal('fetch', fetchFn)
    const onEvent = vi.fn()
    const onRecover = vi.fn()
    const onRetry = vi.fn()
    const { finished } = v3Sse('GET', '/teacher-v3/classroom/sessions/s1/stream', undefined, onEvent, undefined, { reconnect: true, onRecover, onRetry })

    await vi.advanceTimersByTimeAsync(100) // 首次连接 + 断流
    expect(fetchFn).toHaveBeenCalledTimes(1)
    expect(onRetry).toHaveBeenCalledWith(1, 1000)

    await vi.advanceTimersByTimeAsync(1000) // 退避 1s → 第二次连接
    await expect(finished).resolves.toBeUndefined() // 第二次正常收流 → resolve
    expect(fetchFn).toHaveBeenCalledTimes(2)
    // 事件 id=7 已写入重连请求头（fetch-event-source 内建补拉锚点，§7.1）
    expect(seen[1]['last-event-id']).toBe('7')
    expect(onRecover).toHaveBeenCalledTimes(1)
    // 两次连接的事件都到达调用方
    expect(onEvent.mock.calls.map((c) => c[0])).toEqual(['event', 'event', 'event'])
    expect(onEvent.mock.calls[2][1]).toEqual({ seq: 9 })
  })

  it('退避序列 1s/2s/4s，×3 用尽后终止', async () => {
    const fetchFn = vi.fn(async () => { throw new Error('net down') })
    vi.stubGlobal('fetch', fetchFn)
    const onRetry = vi.fn()
    const { finished } = v3Sse('GET', '/teacher-v3/classroom/sessions/s1/stream', undefined, () => {}, undefined, { reconnect: true, onRetry })

    let rejection: any = null
    const handled = finished.catch((e) => { rejection = e })
    await vi.advanceTimersByTimeAsync(100)
    await vi.advanceTimersByTimeAsync(1000)
    await vi.advanceTimersByTimeAsync(2000)
    await vi.advanceTimersByTimeAsync(4000)
    await handled
    expect(rejection?.message).toBe('net down')
    expect(fetchFn).toHaveBeenCalledTimes(4) // 首次 + 3 次重试
    expect(onRetry.mock.calls.map((c) => [c[0], c[1]])).toEqual([[1, 1000], [2, 2000], [3, 4000]])
  })
})

describe('v3Sse · 致命错误（HTTP/业务）永不重试', () => {
  it('40301 role_denied → SseFatalError 且跳提示页，不重试', async () => {
    const fetchFn = vi.fn(async () => sseResponse([], { body: JSON.stringify({ code: 40301, message: 'role_denied' }) }))
    vi.stubGlobal('fetch', fetchFn)
    const { finished } = v3Sse('GET', '/teacher-v3/classroom/sessions/s1/stream', undefined, () => {}, undefined, { reconnect: true })

    let rejection: any = null
    const handled = finished.catch((e) => { rejection = e })
    await vi.advanceTimersByTimeAsync(100)
    await handled
    expect(rejection).toBeInstanceOf(SseFatalError)
    await vi.advanceTimersByTimeAsync(10000) // 若误重试早已再次 fetch
    expect(fetchFn).toHaveBeenCalledTimes(1)
    expect(routerPush).toHaveBeenCalledWith('/teacher-v3/denied')
  })

  it('课堂双通道客户端：studentStream 以课堂 token 覆盖 Authorization，reconnect 默认开启', async () => {
    const fetchFn = vi.fn(async (_url: any, _init: any) => sseResponse([evt(1, 'snapshot', {})]))
    vi.stubGlobal('fetch', fetchFn)
    const { v3Api } = await import('@/api/teacherV3')
    const { finished } = v3Api.classroom.studentStream('s1', 'classroom-token-x', () => {})
    let rejection: any = null
    const handled = finished.catch((e: any) => { rejection = e })
    await vi.advanceTimersByTimeAsync(100)
    await handled
    expect(rejection).toBeNull()
    const init = fetchFn.mock.calls[0][1]
    expect(init.headers.Authorization).toBe('Bearer classroom-token-x')
    expect(fetchFn.mock.calls[0][0]).toContain('/teacher-v3/classroom/sessions/s1/student-stream')
  })
})
