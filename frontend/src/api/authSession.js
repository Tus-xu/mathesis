let accessToken = ''
let refreshPromise = null

const SCOPES = ['student', 'teacher', 'researcher', 'admin']
const SCOPE_KEY = 'ma_auth_scope'

function cookieValue(name) {
  if (typeof document === 'undefined') return ''
  const match = document.cookie.split(';').map((part) => part.trim())
    .find((part) => part.startsWith(`${name}=`))
  return match ? decodeURIComponent(match.slice(name.length + 1)) : ''
}

/** 当前标签页的会话作用域（sessionStorage 按标签页隔离，端口不隔离 cookie）。 */
export function getAuthScope() {
  try {
    const stored = sessionStorage.getItem(SCOPE_KEY)
    if (SCOPES.includes(stored)) return stored
  } catch { /* 隐私模式 */ }
  const path = typeof location !== 'undefined' ? location.pathname : ''
  if (path.startsWith('/teacher')) return 'teacher'
  if (path.startsWith('/research')) return 'researcher'
  if (path.startsWith('/admin')) return 'admin'
  for (const scope of SCOPES) {
    if (cookieValue(`ma_csrf_${scope}`)) return scope
  }
  return 'student'
}

export function setAuthScope(scope) {
  if (!SCOPES.includes(scope)) return
  try { sessionStorage.setItem(SCOPE_KEY, scope) } catch { /* 隐私模式 */ }
}

export function clearAuthScope() {
  try { sessionStorage.removeItem(SCOPE_KEY) } catch { /* 隐私模式 */ }
}

export function getAccessToken() { return accessToken }
export function setAccessToken(token) { accessToken = token || '' }
export function clearAccessToken() { accessToken = '' }

export function getCsrfToken(scope = getAuthScope()) {
  return cookieValue(`ma_csrf_${scope}`) || cookieValue('ma_csrf')
}

function candidateScopes(scope, allowFallback) {
  const out = [scope]
  if (allowFallback) {
    for (const item of SCOPES) {
      if (item !== scope && cookieValue(`ma_csrf_${item}`)) out.push(item)
    }
  }
  return out
}

/**
 * 刷新 access token。
 *
 * - 默认只刷新当前标签页作用域（教师/学生/科研各有独立 refresh cookie）；
 * - bootstrap 时 allowFallback=true：如果新标签页没有 sessionStorage scope，则按
 *   cookie 里实际存在的角色选择一个，避免“只有教师 cookie 的新标签页被当成学生”；
 * - 同一标签页内的并发 401 仍然单飞（refreshPromise）。
 */
export async function refreshAccessToken({ scope = getAuthScope(), allowFallback = false } = {}) {
  if (refreshPromise) return refreshPromise
  refreshPromise = (async () => {
    let lastError = null
    for (const candidate of candidateScopes(scope, allowFallback)) {
      const csrf = cookieValue(`ma_csrf_${candidate}`)
      if (!csrf) continue
      const response = await fetch('/api/auth/token/refresh', {
        method: 'POST',
        credentials: 'include',
        headers: { 'X-CSRF-Token': csrf, 'X-Auth-Scope': candidate },
      })
      let payload = null
      try { payload = await response.json() } catch { /* handled below */ }
      if (response.ok && payload?.code === 0 && payload?.data?.access_token) {
        setAuthScope(payload.data.active_role || candidate)
        setAccessToken(payload.data.access_token)
        return payload.data
      }
      lastError = new Error(payload?.message || '登录状态已失效')
      lastError.code = payload?.code || response.status
      lastError.errorKey = payload?.error_key || 'AUTH_REFRESH_INVALID'
    }
    // 旧客户端只写 legacy cookie：没有 scoped CSRF 时再试一次 legacy。
    const legacyCsrf = cookieValue('ma_csrf')
    if (legacyCsrf) {
      const response = await fetch('/api/auth/token/refresh', {
        method: 'POST',
        credentials: 'include',
        headers: { 'X-CSRF-Token': legacyCsrf },
      })
      let payload = null
      try { payload = await response.json() } catch { /* handled below */ }
      if (response.ok && payload?.code === 0 && payload?.data?.access_token) {
        setAuthScope(payload.data.active_role || scope)
        setAccessToken(payload.data.access_token)
        return payload.data
      }
      lastError = new Error(payload?.message || '登录状态已失效')
      lastError.code = payload?.code || response.status
      lastError.errorKey = payload?.error_key || 'AUTH_REFRESH_INVALID'
    }
    clearAccessToken()
    throw lastError || Object.assign(new Error('登录状态已失效'), { errorKey: 'AUTH_REFRESH_INVALID' })
  })().finally(() => { refreshPromise = null })
  return refreshPromise
}

export function authScopeHeaders() {
  return { 'X-Auth-Scope': getAuthScope() }
}

export function __resetAuthSessionForTests() {
  accessToken = ''
  refreshPromise = null
  try { sessionStorage.removeItem(SCOPE_KEY) } catch { /* ignore */ }
}
