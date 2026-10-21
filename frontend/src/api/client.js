/**
 * 统一 API 客户端
 * - 信封约定：{ code, message, data, request_id }，code === 0 成功，其余抛 ApiError
 * - 401 自动清登并跳转登录页
 * - 扩展（向后兼容）：自定义 headers / AbortSignal / Idempotency-Key / request_id / 204 与非 JSON 安全处理。
 *        api.get/post/put/patch/del 签名与返回值保持原样（返回 envelope.data）；
 *        新增 api.raw(method, path, opts) 返回 { data, status, request_id } 供教师端使用。
 */
import { clearAccessToken, getAccessToken, getCsrfToken, refreshAccessToken, setAccessToken } from './authSession'
import { getHttpPolicy, runWithGate, setHttpPolicy, resetHttpPolicy } from './httpPolicy'

const BASE = '/api'
const USER_KEY = 'ma_user'

export class ApiError extends Error {
  constructor(code, message, opts = {}) {
    super(message || `请求失败 (${code})`)
    this.name = 'ApiError'
    this.code = code
    if (opts.status !== undefined) this.status = opts.status
    if (opts.retryAfter !== undefined) this.retryAfter = opts.retryAfter
  }
}

/** 网络层错误（断网/无法连接）。保留 code=-1 以兼容既有 e.code 判断。 */
export class NetworkError extends ApiError {
  constructor(message) { super(-1, message || '网络连接失败，请确认后端已启动', { status: undefined }); this.name = 'NetworkError' }
}
/** 超时（超过 policy 设定的阈值，区别于用户主动取消）。 */
export class TimeoutError extends ApiError {
  constructor(message) { super(-3, message || '请求超时，请稍后重试', { status: undefined }); this.name = 'TimeoutError' }
}
/** 用户主动取消（AbortSignal）。保留 code=-2 兼容既有 e.code===-2 判断，不计入重试。 */
export class AbortError extends ApiError {
  constructor(message) { super(-2, message || '请求已取消', { status: undefined }); this.name = 'AbortError' }
}
/** 触发限流（HTTP 429 / Retry-After）。保留 code 与 status 以兼容既有 e.code 判断。 */
export class RateLimitError extends ApiError {
  constructor(code, message, retryAfter) {
    super(code ?? 429, message || '请求过于频繁，请稍后重试', { status: 429, retryAfter })
    this.name = 'RateLimitError'
  }
}

export function getToken() { return getAccessToken() }
export function setToken(t) { setAccessToken(t) }
export function getCachedUser() {
  try { return JSON.parse(localStorage.getItem(USER_KEY) || 'null') } catch { return null }
}
export function setCachedUser(u) { u ? localStorage.setItem(USER_KEY, JSON.stringify(u)) : localStorage.removeItem(USER_KEY) }

export function authHeaders() {
  const t = getAccessToken()
  return t ? { Authorization: `Bearer ${t}` } : {}
}

export function redirectLogin() {
  if (location.pathname.startsWith('/login')) return
  import('@/router').then(({ router }) => {
    router.push({ path: '/login', query: { redirect: location.pathname } }).catch(() => {})
  }).catch(() => { location.href = '/login' })
}

function buildUrl(path, query) {
  let url = BASE + path
  if (query) {
    const qs = new URLSearchParams()
    Object.entries(query).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== '') qs.append(k, String(v))
    })
    const s = qs.toString()
    if (s) url += `?${s}`
  }
  return url
}

// ---- 弹性能力：超时 / 重试 / 错误分类（零依赖，可配置）----

/** 合并调用方 signal 与超时信号：任一方 abort 都会中断请求，且互不替换。 */
function prepareSignal(signal, timeoutMs) {
  const ctrl = new AbortController()
  const timeoutCtrl = new AbortController()
  let timer = null
  const clear = () => { if (timer) clearTimeout(timer) }
  if (timeoutMs && timeoutMs > 0) timer = setTimeout(() => timeoutCtrl.abort(), timeoutMs)
  if (signal) {
    if (signal.aborted) ctrl.abort(signal.reason)
    else signal.addEventListener('abort', () => ctrl.abort(signal.reason), { once: true })
  }
  timeoutCtrl.signal.addEventListener('abort', () => ctrl.abort(), { once: true })
  ctrl.signal.addEventListener('abort', clear, { once: true })
  return { signal: ctrl.signal, timeoutCtrl, clear }
}

function parseRetryAfter(header) {
  if (header == null) return undefined
  const secs = Number(header)
  if (!Number.isNaN(secs)) return secs
  const d = Date.parse(header)
  if (!Number.isNaN(d)) return Math.max(0, Math.round((d - Date.now()) / 1000))
  return undefined
}

function resolveTimeout(opts, path) {
  if (typeof opts.timeout === 'number') return opts.timeout
  const { timeoutByPrefix } = getHttpPolicy()
  for (const prefix of Object.keys(timeoutByPrefix)) {
    if (path.startsWith(prefix)) return timeoutByPrefix[prefix]
  }
  return getHttpPolicy().defaultTimeoutMs
}

function isRetryable(method, opts, err) {
  const m = String(method).toUpperCase()
  const idempotent = m === 'GET' || m === 'HEAD'
  const writeSafe = !idempotent && !!opts.idempotencyKey
  if (!idempotent && !writeSafe) return false // 写操作默认不重试
  if (err instanceof AbortError) return false // 用户主动取消
  if (err instanceof TimeoutError || err instanceof NetworkError) return true
  if (err instanceof ApiError && err.status && getHttpPolicy().retryableStatuses.includes(err.status)) return true
  return false
}

function computeDelay(err, attempt) {
  const p = getHttpPolicy()
  const backoff = Math.min(p.baseRetryDelayMs * 2 ** attempt, p.maxRetryDelayMs)
  const jitter = Math.random() * backoff * 0.3
  let delay = backoff + jitter
  if (typeof err.retryAfter === 'number') {
    delay = Math.min(Math.max(delay, err.retryAfter * 1000), p.maxRetryAfterMs) // 尊重 Retry-After
  }
  return Math.round(delay)
}

function sleep(ms) { return new Promise((r) => setTimeout(r, ms)) }

/** 低层请求（单次尝试）：返回 { data, status, request_id }；204/空 body 返回 data=undefined */
async function coreRequest(method, path, { body, query, headers = {}, signal, idempotencyKey, timeout } = {}, retried = false) {
  const url = buildUrl(path, query)
  const h = { ...authHeaders(), ...headers }
  if (body !== undefined && !(body instanceof FormData)) h['Content-Type'] = 'application/json'
  if (idempotencyKey) h['Idempotency-Key'] = idempotencyKey
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
    const csrf = getCsrfToken()
    if (csrf && !h['X-CSRF-Token']) h['X-CSRF-Token'] = csrf
  }

  const { signal: eff, timeoutCtrl, clear } = prepareSignal(signal, resolveTimeout({ timeout }, path))
  let res
  try {
    res = await fetch(url, {
      method,
      headers: h,
      body: body instanceof FormData ? body : (body !== undefined ? JSON.stringify(body) : undefined),
      signal: eff,
      credentials: 'include',
    })
  } catch (e) {
    clear()
    if (eff.aborted) {
      if (timeoutCtrl.signal.aborted) throw new TimeoutError()
      throw new AbortError()
    }
    throw new NetworkError(e?.message || '网络连接失败，请确认后端已启动')
  }
  clear()

  if (res.status === 401) {
    if (!retried && path !== '/auth/token/refresh') {
      try {
        await refreshAccessToken()
        return coreRequest(method, path, { body, query, headers, signal, idempotencyKey, timeout }, true)
      } catch { /* terminal refresh failure below */ }
    }
    clearAccessToken(); setCachedUser(null)
    redirectLogin()
    throw new ApiError(401, '登录已过期')
  }

  if (res.status === 204) {
    return { data: undefined, status: res.status, request_id: res.headers.get('x-request-id') || '' }
  }

  let json
  try { json = await res.json() } catch {
    if (res.ok) {
      return { data: undefined, status: res.status, request_id: res.headers.get('x-request-id') || '' }
    }
    throw new ApiError(res.status, `响应解析失败 (HTTP ${res.status})`)
  }

  if (json && typeof json.code !== 'undefined') {
    if (json.code === 0) return { data: json.data ?? null, status: res.status, request_id: json.request_id || '' }
    const ra = parseRetryAfter(res.headers.get('retry-after'))
    const error = res.status === 429
      ? new RateLimitError(json.code, json.message || '请求过于频繁', ra)
      : new ApiError(json.code, json.message || '请求失败')
    error.errorKey = json.error_key || ''
    throw error
  }
  if (!res.ok) {
    // detail 可能是对象（如 {code, message}）——转成字符串，避免页面渲染成 [object Object]
    const d = json?.detail
    const msg = typeof d === 'string' ? d : (d?.message || d?.detail?.message || `HTTP ${res.status}`)
    const ra = parseRetryAfter(res.headers.get('retry-after'))
    const error = res.status === 429 ? new RateLimitError(res.status, msg, ra) : new ApiError(res.status, msg)
    throw error
  }
  return { data: json, status: res.status, request_id: res.headers.get('x-request-id') || '' }
}

/** 统一重试（指数退避 + jitter + 尊重 Retry-After），仅对幂等/带幂等键且可重试的错误重试。 */
async function withRetry(method, path, opts, attempt) {
  try {
    return await coreRequest(method, path, opts, attempt > 0)
  } catch (err) {
    if (attempt >= getHttpPolicy().maxRetries || !isRetryable(method, opts, err)) throw err
    const delay = computeDelay(err, attempt)
    if (delay > 0) await sleep(delay)
    return withRetry(method, path, opts, attempt + 1)
  }
}

/** 公开低层请求：经并发闸门 + 重试包装，返回 { data, status, request_id }。 */
async function requestRaw(method, path, opts = {}) {
  return runWithGate(path, () => withRetry(method, path, opts, 0))
}

async function request(method, path, options) {
  const r = await requestRaw(method, path, options)
  return r.data
}

/** 文件下载：GET 字节流（同源 /api 路径），401 自动刷新重试一次；返回 { blob, filename } */
async function downloadRaw(path, { signal, timeout } = {}, retried = false) {
  const { signal: eff, timeoutCtrl, clear } = prepareSignal(signal, resolveTimeout({ timeout }, path))
  let res
  try {
    res = await fetch(BASE + path, { headers: { ...authHeaders() }, signal: eff, credentials: 'include', cache: 'no-store' })
  } catch (e) {
    clear()
    if (eff.aborted) {
      if (timeoutCtrl.signal.aborted) throw new TimeoutError()
      throw new AbortError()
    }
    throw new NetworkError(e?.message || '网络连接失败，请确认后端已启动')
  }
  clear()
  if (res.status === 401 && !retried) {
    try {
      await refreshAccessToken()
      return await downloadRaw(path, { signal, timeout }, true)
    } catch { /* 落到下方终态处理 */ }
  }
  if (res.status === 401) {
    clearAccessToken(); setCachedUser(null)
    redirectLogin()
    throw new ApiError(401, '登录已过期')
  }
  if (!res.ok) throw new ApiError(res.status, `文件下载失败 (HTTP ${res.status})`)
  const disposition = res.headers.get('content-disposition') || ''
  const match = disposition.match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/i)
  const filename = match ? decodeURIComponent(match[1]) : ''
  return { blob: await res.blob(), filename }
}

export const api = {
  get: (path, query, opts) => request('GET', path, { ...opts, query }),
  post: (path, body, query, opts) => request('POST', path, { ...opts, body, query }),
  put: (path, body, opts) => request('PUT', path, { ...opts, body }),
  patch: (path, body, opts) => request('PATCH', path, { ...opts, body }),
  delete: (path, opts) => request('DELETE', path, { ...opts }),
  del: (path, opts) => request('DELETE', path, opts),
  raw: (method, path, opts) => requestRaw(method, path, opts),
  download: (path, opts) => downloadRaw(path, opts),
}

// 弹性策略控制（测试或特殊场景可临时调整并发/超时/重试）
export { setHttpPolicy, resetHttpPolicy, getHttpPolicy }
