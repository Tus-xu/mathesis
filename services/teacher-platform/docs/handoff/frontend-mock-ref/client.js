/**
 * 统一 API 客户端
 * - 信封约定：{ code, message, data, request_id }，code === 0 成功，其余抛 ApiError
 * - 401 自动清登并跳转登录页
 * - 扩展（向后兼容）：自定义 headers / AbortSignal / Idempotency-Key / request_id / 204 与非 JSON 安全处理。
 *        api.get/post/put/patch/del 签名与返回值保持原样（返回 envelope.data）；
 *        新增 api.raw(method, path, opts) 返回 { data, status, request_id } 供教师端使用。
 */
import { clearAccessToken, getAccessToken, getCsrfToken, refreshAccessToken, setAccessToken } from './authSession'

const BASE = '/api'
const USER_KEY = 'ma_user'

export class ApiError extends Error {
  constructor(code, message) {
    super(message || `请求失败 (${code})`)
    this.code = code
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

/** 低层请求：返回 { data, status, request_id }；204/空 body 返回 data=undefined */
async function requestRaw(method, path, { body, query, headers = {}, signal, idempotencyKey } = {}, retried = false) {
  const url = buildUrl(path, query)
  const h = { ...authHeaders(), ...headers }
  if (body !== undefined) h['Content-Type'] = 'application/json'
  if (idempotencyKey) h['Idempotency-Key'] = idempotencyKey
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
    const csrf = getCsrfToken()
    if (csrf && !h['X-CSRF-Token']) h['X-CSRF-Token'] = csrf
  }

  let res
  try {
    res = await fetch(url, {
      method,
      headers: h,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal,
      credentials: 'include',
    })
  } catch (e) {
    if (e?.name === 'AbortError') throw new ApiError(-2, '请求已取消')
    throw new ApiError(-1, '网络连接失败，请确认后端已启动')
  }

  if (res.status === 401) {
    if (!retried && path !== '/auth/token/refresh') {
      try {
        await refreshAccessToken()
        return requestRaw(method, path, { body, query, headers, signal, idempotencyKey }, true)
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
    const error = new ApiError(json.code, json.message || '请求失败')
    error.errorKey = json.error_key || ''
    throw error
  }
  if (!res.ok) throw new ApiError(res.status, json?.detail || `HTTP ${res.status}`)
  return { data: json, status: res.status, request_id: res.headers.get('x-request-id') || '' }
}

async function request(method, path, options) {
  const r = await requestRaw(method, path, options)
  return r.data
}

/** 文件下载：GET 字节流（同源 /api 路径），401 自动刷新重试一次；返回 { blob, filename } */
async function downloadRaw(path, { signal } = {}, retried = false) {
  let res
  try {
    res = await fetch(BASE + path, { headers: { ...authHeaders() }, signal, credentials: 'include', cache: 'no-store' })
  } catch (e) {
    if (e?.name === 'AbortError') throw new ApiError(-2, '请求已取消')
    throw new ApiError(-1, '网络连接失败，请确认后端已启动')
  }
  if (res.status === 401 && !retried) {
    try {
      await refreshAccessToken()
      return await downloadRaw(path, { signal }, true)
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
