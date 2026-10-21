/**
 * 科研工作台统一 API client（FRONTEND-ARCH §4 / BACKEND-CONTRACT §1 D2）。
 *
 * 关键纪律：
 *  1. 基址恒为 `/api/research-vnext`，**必须经 8000 网关注入 HMAC**；禁止直连 8300；
 *  2. 鉴权复用 `@/api/authSession`（Bearer access token + refresh cookie），**不自造
 *     `X-Research-Access-*` 头**（废弃 researchApi.ts 的开发签名桥）；
 *  3. 成功响应：research-vnext 直接返回裸 JSON（如 `{"items":[]}`）；兼容 Web 统一
 *     `{code,message,data}` 信封（若出现则解包）；
 *  4. 错误一律经 `mapResearchError()` 转成**中文人话** `RvError`，
 *     **禁止**把 `WS_LLM_TIMEOUT` / 裸错误码 / traceback 暴露给 UI（宪法 §60/§61）。
 */
import { clearAccessToken, getAccessToken, refreshAccessToken } from '@/api/authSession'

const BASE = '/api/research-vnext'

/** 统一错误对象（宪法 §87）：恒有中文 message。 */
export interface RvError {
  code: string | number
  status: number
  message: string
  taskId?: string
  retryable: boolean
}

export class RvApiError extends Error {
  code: string | number
  status: number
  taskId?: string
  retryable: boolean

  constructor(e: RvError) {
    super(e.message)
    this.name = 'RvApiError'
    this.code = e.code
    this.status = e.status
    this.taskId = e.taskId
    this.retryable = e.retryable
  }

  toError(): RvError {
    return {
      code: this.code,
      status: this.status,
      message: this.message,
      taskId: this.taskId,
      retryable: this.retryable,
    }
  }
}

/** 原始错误输入（来自 HTTP 层或业务 envelope）。 */
export interface RawResearchError {
  code?: string | number
  status?: number
  message?: string
  taskId?: string
}

const LLM_UNAVAILABLE_RE = /(_LLM_|LLM_UNAVAILABLE|LLM_TIMEOUT|TIMEOUT|UPSTREAM|GATEWAY_)/i

/** 判定是否为「AI 服务不可用 / 超时」类（-1/-3/503/504/*_LLM_TIMEOUT）。 */
function isLlmUnavailable(code: string | number, status: number): boolean {
  const n = Number(code)
  if (n === -1 || n === -3) return true
  if (status === 503 || status === 504) return true
  if (n === 503 || n === 504) return true
  return LLM_UNAVAILABLE_RE.test(String(code))
}

/**
 * 把任意原始错误映射为**中文人话** `RvError`（映射表见 FRONTEND-ARCH §4）。
 * 恒不泄露内部错误码 / 堆栈。
 */
export function mapResearchError(raw: RawResearchError = {}): RvError {
  const code = raw.code ?? -1
  const status = raw.status ?? 0
  const taskId = raw.taskId

  if (status === 401 || Number(code) === 401) {
    return { code, status: 401, message: '登录已过期，请重新登录。', taskId, retryable: false }
  }
  if (status === 403 || Number(code) === 403) {
    return { code, status: 403, message: '你没有该项目的访问权限。', taskId, retryable: false }
  }
  if (status === 404 || Number(code) === 404) {
    return { code, status: 404, message: '内容不存在或已被删除。', taskId, retryable: false }
  }
  if (status === 409 || Number(code) === 409) {
    return { code, status: 409, message: '文件已在别处修改，已为你刷新最新版本。', taskId, retryable: false }
  }
  if (status === 429 || Number(code) === 429) {
    return { code, status: 429, message: '操作过于频繁，请稍后重试。', taskId, retryable: true }
  }
  if (status >= 500 || isLlmUnavailable(code, status)) {
    return {
      code,
      status: status || 503,
      message: 'AI 服务暂时不可用。你的论文已保存。',
      taskId,
      retryable: true,
    }
  }
  // 兜底：任何未识别错误都给通用中文文案，绝不透出裸 code。
  return {
    code,
    status: status || 0,
    message: '操作未能完成，请稍后重试。',
    taskId,
    retryable: isLlmUnavailable(code, status),
  }
}

/** 把已捕获的异常统一成 RvError（store 用）。 */
export function toRvError(err: unknown): RvError {
  if (err instanceof RvApiError) return err.toError()
  if (err && typeof err === 'object') {
    const e = err as { code?: string | number; status?: number; message?: string; taskId?: string }
    return mapResearchError({ code: e.code, status: e.status, message: e.message, taskId: e.taskId })
  }
  return mapResearchError({})
}

type Query = Record<string, string | number | boolean | null | undefined>

interface RequestOptions {
  body?: unknown
  query?: Query
  signal?: AbortSignal
  headers?: Record<string, string>
}

function buildUrl(path: string, query?: Query): string {
  let url = BASE + path
  if (query) {
    const qs = new URLSearchParams()
    for (const [k, v] of Object.entries(query)) {
      if (v !== undefined && v !== null && v !== '') qs.append(k, String(v))
    }
    const s = qs.toString()
    if (s) url += `?${s}`
  }
  return url
}

function extractRawError(json: unknown, status: number): RawResearchError {
  if (json && typeof json === 'object') {
    const obj = json as Record<string, unknown>
    // research-vnext：{"error":{"code":"...","message":"..."}}
    const errorObj = obj.error
    if (errorObj && typeof errorObj === 'object') {
      const e = errorObj as Record<string, unknown>
      return {
        code: (e.code as string | number) ?? status,
        status,
        message: (e.message as string) ?? '',
        taskId: (e.task_id as string) ?? undefined,
      }
    }
    // 兼容 FastAPI：{"detail": "..."} 或 {"detail":{"code","message"}}
    const detail = obj.detail
    if (typeof detail === 'string') return { code: status, status, message: detail }
    if (detail && typeof detail === 'object') {
      const d = detail as Record<string, unknown>
      return { code: (d.code as string | number) ?? status, status, message: (d.message as string) ?? '' }
    }
    if (typeof obj.message === 'string') {
      return { code: (obj.code as string | number) ?? status, status, message: obj.message }
    }
  }
  return { code: status, status }
}

async function request<T>(
  method: string,
  path: string,
  opts: RequestOptions = {},
  retried = false,
): Promise<T> {
  const url = buildUrl(path, opts.query)
  const headers: Record<string, string> = { ...(opts.headers ?? {}) }
  const token = getAccessToken()
  if (token) headers.Authorization = `Bearer ${token}`

  let bodyInit: BodyInit | undefined
  if (opts.body instanceof FormData) {
    bodyInit = opts.body
  } else if (opts.body !== undefined) {
    headers['Content-Type'] = 'application/json'
    bodyInit = JSON.stringify(opts.body)
  }

  let res: Response
  try {
    res = await fetch(url, {
      method,
      headers,
      body: bodyInit,
      signal: opts.signal,
      credentials: 'include',
    })
  } catch {
    throw new RvApiError(mapResearchError({ code: -1, status: 0 }))
  }

  // 401：尝试用 refresh cookie 续期一次，再重放。
  if (res.status === 401 && !retried) {
    try {
      await refreshAccessToken()
      return await request<T>(method, path, opts, true)
    } catch {
      clearAccessToken()
    }
  }

  const text = await res.text()
  let json: unknown = null
  if (text) {
    try {
      json = JSON.parse(text)
    } catch {
      json = null
    }
  }

  // Web 统一信封 {code,message,data}（若后端启用）
  if (json && typeof json === 'object' && typeof (json as Record<string, unknown>).code !== 'undefined') {
    const env = json as { code: number | string; message?: string; data?: unknown; task_id?: string }
    if (Number(env.code) === 0) return (env.data ?? null) as T
    throw new RvApiError(
      mapResearchError({
        code: env.code,
        status: res.status,
        message: env.message,
        taskId: env.task_id,
      }),
    )
  }

  if (!res.ok) {
    throw new RvApiError(mapResearchError(extractRawError(json, res.status)))
  }
  if (res.status === 204 || !text) return null as T
  return json as T
}

export function rwGet<T>(path: string, query?: Query, opts?: Omit<RequestOptions, 'body' | 'query'>): Promise<T> {
  return request<T>('GET', path, { ...opts, query })
}

export function rwPost<T>(path: string, body?: unknown, query?: Query, opts?: Omit<RequestOptions, 'body' | 'query'>): Promise<T> {
  return request<T>('POST', path, { ...opts, body, query })
}

export function rwPut<T>(path: string, body?: unknown, query?: Query, opts?: Omit<RequestOptions, 'body' | 'query'>): Promise<T> {
  return request<T>('PUT', path, { ...opts, body, query })
}

export function rwPatch<T>(path: string, body?: unknown, opts?: Omit<RequestOptions, 'body' | 'query'>): Promise<T> {
  return request<T>('PATCH', path, { ...opts, body })
}

export function rwDel<T>(path: string, opts?: Omit<RequestOptions, 'body' | 'query'>): Promise<T> {
  return request<T>('DELETE', path, opts)
}

export const RESEARCH_API_BASE = BASE
