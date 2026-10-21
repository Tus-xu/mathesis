import { api, ApiError } from '@/api/client'

export interface TeacherRequestOptions {
  body?: unknown
  query?: Record<string, unknown>
  headers?: Record<string, string>
  signal?: AbortSignal
  idempotencyKey?: string
}

export interface TeacherResponse<T> {
  data: T
  status: number
  request_id?: string
}

export const TEACHER_DENIED_ROUTE = '/teacher-v3/denied'

/** 40301 role_denied 统一落点（A0 M1-2）：跳提示页而非白屏；幂等——已在提示页不重复跳 */
export function redirectTeacherDenied() {
  if (typeof location !== 'undefined' && location.pathname === TEACHER_DENIED_ROUTE) return
  import('@/router').then(({ router }) => {
    router.push(TEACHER_DENIED_ROUTE).catch(() => {})
  }).catch(() => { location.href = TEACHER_DENIED_ROUTE })
}

export function teacherRequest<T>(method: string, path: string, opts: TeacherRequestOptions = {}): Promise<TeacherResponse<T>> {
  return (api.raw(method, path, {
    body: opts.body,
    query: opts.query,
    headers: opts.headers,
    signal: opts.signal,
    idempotencyKey: opts.idempotencyKey,
  }) as Promise<TeacherResponse<T>>).catch((e: unknown) => {
    if (e instanceof ApiError && e.code === 40301) redirectTeacherDenied()
    throw e
  })
}

export function teacherGet<T>(path: string, query?: Record<string, unknown>, signal?: AbortSignal) {
  return teacherRequest<T>('GET', path, { query, signal })
}
export function teacherPost<T>(path: string, body?: unknown, idempotencyKey?: string, signal?: AbortSignal) {
  return teacherRequest<T>('POST', path, { body, idempotencyKey, signal })
}
export function teacherPut<T>(path: string, body?: unknown, signal?: AbortSignal) {
  return teacherRequest<T>('PUT', path, { body, signal })
}