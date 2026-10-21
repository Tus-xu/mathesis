/**
 * T22 · teacher-prep-v2 REST 客户端（唯一数据源：8100 `/api/teacher-v3/prep/**`）。
 *
 * 铁律：
 * - **只**打真实后端；本文件在测试环境下也不提供 mock 分支（生产 mock = 反模式）。
 * - 401/403/409 不吞、不重试、不伪装成功 —— 一律归一化为 `NormalizedError` 上抛，
 *   由 store 映射到可见状态（forbidden = 可见但禁用，不隐藏）。
 * - 所有请求显式带 `AbortSignal`（离开后返回 / 竞态由调用方掌控）。
 */
import { getAccessToken } from '@/api/authSession'
import type {
  NormalizedError,
  WorkspaceSnapshot,
} from '@/features/teacher-prep-v2/types/workspace'

/** 8100 教师端前缀（与 vite proxy `/api/teacher-v3` 对齐）。 */
export const PREP_API_BASE = '/api/teacher-v3/prep'

/** T24 产物编辑 / QA / 导出前缀（同一 8100 服务，另一命名空间）。 */
export const ARTIFACTS_API_BASE = '/api/teacher-v3/artifacts'

/** 业务错误：携带归一化后的 kind / status / code。 */
export class PrepApiError extends Error {
  readonly info: NormalizedError
  readonly requestId: string | null
  /**
   * 服务端 `data` 载荷原样保留（T24 的 409 冲突解决**必须**用服务端 diff，
   * 不允许前端自己推算"谁的版本更新"）。
   */
  readonly data: unknown
  constructor(info: NormalizedError, requestId: string | null = null, data: unknown = null) {
    super(info.copyKey)
    this.name = 'PrepApiError'
    this.info = info
    this.requestId = requestId
    this.data = data
  }

  /** 409 冲突载荷（kind !== conflict 时恒为 null）。 */
  get conflictPayload(): unknown {
    return this.info.kind === 'conflict' ? this.data : null
  }
}

const COPY_BY_KIND: Record<NormalizedError['kind'], string> = {
  unauthenticated: 'prep.error.unauthenticated',
  forbidden: 'prep.error.forbidden',
  conflict: 'prep.error.conflict',
  not_found: 'prep.error.not_found',
  gone: 'prep.error.gone',
  rate_limited: 'prep.error.rate_limited',
  error: 'prep.error.server',
  network: 'prep.error.network',
}

/**
 * 8000 是教师班级/身份关系的权威服务。它暂时不可达时，不能把 503
 * 渲染成“没有权限”；真正的 403 仍沿用 forbidden。兼容 50300（8100
 * 平台依赖错误）、50301（契约错误码）以及旧服务只返回文本的形态。
 */
function isAuthorityUnavailable(input: { status: number | null; code: number | null; detail?: string }): boolean {
  if (input.status === null || input.status < 500) return false
  return input.code === 50300 || input.code === 50301 || /8000|权威不可达|成员校验/.test(input.detail || '')
}

/** HTTP 状态 + 业务 code → 归一化错误（唯一映射入口，避免各处 if-else 漂移）。 */
export function normalizePrepError(input: {
  status?: number | null
  code?: number | null
  detail?: string
  requestId?: string | null
}): NormalizedError {
  const status = input.status ?? null
  const code = input.code ?? null
  let kind: NormalizedError['kind']
  if (status === 401) kind = 'unauthenticated'
  else if (status === 403) kind = 'forbidden'
  else if (status === 409) kind = 'conflict'
  else if (status === 404) kind = 'not_found'
  else if (status === 410) kind = 'gone'
  else if (status === 429) kind = 'rate_limited'
  else if (status === null || status === -1) kind = 'network'
  else kind = 'error'
  const copyKey = isAuthorityUnavailable({ status, code, detail: input.detail })
    ? 'prep.error.authority_unavailable'
    : COPY_BY_KIND[kind]
  return { kind, status, code, copyKey, detail: input.detail, requestId: input.requestId ?? null }
}

/** 当前访问令牌（与主应用 authSession 同源；SSE 与 REST 共用）。 */
export function currentToken(): string | null {
  const t = getAccessToken()
  return t || null
}

/** 读 token：复用既有会话存储，不新增第二套令牌来源。 */
function authHeader(): Record<string, string> {
  const t = currentToken()
  return t ? { Authorization: `Bearer ${t}` } : {}
}

interface Envelope<T> {
  code?: number
  message?: string
  data?: T
  request_id?: string
}

async function request<T>(
  path: string,
  init: RequestInit & { signal?: AbortSignal } = {},
  base: string = PREP_API_BASE,
): Promise<T> {
  let res: Response
  try {
    res = await fetch(`${base}${path}`, {
      ...init,
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
        ...authHeader(),
        ...(init.headers || {}),
      },
    })
  } catch (e) {
    throw new PrepApiError(normalizePrepError({ status: null, detail: (e as Error)?.message }))
  }

  if (!res.ok) {
    let code: number | null = null
    let detail: string | undefined
    let payload: unknown = null
    let requestId: string | null = res.headers.get('x-request-id')
    try {
      const body = (await res.json()) as Envelope<unknown> & { detail?: string }
      code = typeof body?.code === 'number' ? body.code : null
      detail = typeof body?.message === 'string' ? body.message : body?.detail
      payload = body?.data ?? null
      requestId = requestId ?? (body?.request_id ?? null)
    } catch {
      /* 非 JSON 错误体：只用状态判定 */
    }
    throw new PrepApiError(
      normalizePrepError({ status: res.status, code, detail, requestId }),
      requestId,
      payload,
    )
  }

  if (res.status === 204) return undefined as T
  const body = (await res.json()) as Envelope<T> | T
  // 信封约定 {code,message,data}；无信封时按裸对象处理（后端 ok() 已统一，这里是防御）
  if (body && typeof body === 'object' && 'data' in (body as object)) {
    const env = body as Envelope<T>
    if (typeof env.code === 'number' && env.code !== 0) {
      throw new PrepApiError(
        normalizePrepError({ status: res.status, code: env.code, detail: env.message, requestId: env.request_id ?? res.headers.get('x-request-id') }),
        env.request_id ?? null,
        env.data ?? null,
      )
    }
    return env.data as T
  }
  return body as T
}

/** 列出可续备会话（真实数据；前端不预设任何候选项）。 */
export function listConversations(signal?: AbortSignal) {
  return request<{ items: unknown[] }>('/conversations', { method: 'GET', signal })
}

/** 新建会话：`POST /prep/conversations`。 */
export function createConversation(payload: {
  class_id?: string | null
  message?: string | null
  idempotency_key?: string | null
}, signal?: AbortSignal) {
  return request<WorkspaceSnapshot>('/conversations', {
    method: 'POST',
    body: JSON.stringify(payload),
    signal,
  })
}

/** 拉取权威工作区快照：`GET /prep/conversations/{id}`。 */
export function getWorkspace(sessionId: string, signal?: AbortSignal) {
  return request<WorkspaceSnapshot>(`/conversations/${encodeURIComponent(sessionId)}`, {
    method: 'GET',
    signal,
  })
}

/** 发送消息：`POST /prep/conversations/{id}/messages`。 */
export function postMessage(
  sessionId: string,
  payload: {
    message: string
    idempotency_key?: string | null
    expected_revision?: number | null
    selection?: { textbook_id?: string; section_id?: string; class_id?: string }
  },
  signal?: AbortSignal,
) {
  return request<WorkspaceSnapshot>(
    `/conversations/${encodeURIComponent(sessionId)}/messages`,
    { method: 'POST', body: JSON.stringify(payload), signal },
  )
}

/** 执行 allowlisted 动作：`POST /prep/conversations/{id}/actions`。 */
export function postAction(
  sessionId: string,
  payload: {
    action: string
    target?: string | null
    expected_revision?: number | null
    idempotency_key?: string | null
    payload?: Record<string, unknown> | null
  },
  signal?: AbortSignal,
) {
  return request<WorkspaceSnapshot>(
    `/conversations/${encodeURIComponent(sessionId)}/actions`,
    { method: 'POST', body: JSON.stringify(payload), signal },
  )
}

// ═══════════════════════════════════════════════════════════════════════════
// T24 · 结构化编辑 / 局部重生成 / 逐页 QA / 真实导出 / 下载票据
//
// 前缀 `/api/teacher-v3/artifacts`（契约 S2.4）。**全部返回裸 data**（信封已由 request 解包），
// 归一化交给 `types/t24.ts` 的纯函数，本层只负责路径/动词/请求体与错误归一化。
// 409 由 `request()` 抛 `PrepApiError{kind:'conflict', data:<冲突载荷>}`。
// ═══════════════════════════════════════════════════════════════════════════

/** T24 请求入口：一律走 artifacts 前缀，并与 prep 共用 token/错误归一化。 */
function t24<T>(path: string, init: RequestInit & { signal?: AbortSignal } = {}) {
  return request<T>(path, init, ARTIFACTS_API_BASE)
}

// ── Deck（TeachingDeckSpec）───────────────────────────────────────────────

export function getDeck(deckId: string, signal?: AbortSignal) {
  return t24<Record<string, unknown>>(`/decks/${encodeURIComponent(deckId)}`, { method: 'GET', signal })
}

export function listDeckRevisions(deckId: string, signal?: AbortSignal) {
  return t24<Record<string, unknown>>(`/decks/${encodeURIComponent(deckId)}/revisions`, { method: 'GET', signal })
}

export function revertDeckRevision(
  deckId: string,
  revision: number,
  payload: { expected_revision: number; summary?: string | null },
  signal?: AbortSignal,
) {
  return t24<Record<string, unknown>>(
    `/decks/${encodeURIComponent(deckId)}/revisions/${revision}/revert`,
    { method: 'POST', body: JSON.stringify(payload), signal },
  )
}

export function proposeDeckPatch(
  deckId: string,
  payload: {
    expected_revision: number
    summary: string
    source: string
    idempotency_key?: string | null
    operations: unknown[]
  },
  signal?: AbortSignal,
) {
  return t24<Record<string, unknown>>(`/decks/${encodeURIComponent(deckId)}/patches`, {
    method: 'POST',
    body: JSON.stringify(payload),
    signal,
  })
}

export function applyDeckPatch(
  deckId: string,
  patchId: string,
  payload: { expected_revision: number },
  signal?: AbortSignal,
) {
  return t24<Record<string, unknown>>(
    `/decks/${encodeURIComponent(deckId)}/patches/${encodeURIComponent(patchId)}/apply`,
    { method: 'POST', body: JSON.stringify(payload), signal },
  )
}

export function rejectDeckPatch(
  deckId: string,
  patchId: string,
  payload: { reason?: string | null } = {},
  signal?: AbortSignal,
) {
  return t24<Record<string, unknown>>(
    `/decks/${encodeURIComponent(deckId)}/patches/${encodeURIComponent(patchId)}/reject`,
    { method: 'POST', body: JSON.stringify(payload), signal },
  )
}

export function regenDeckTarget(
  deckId: string,
  payload: {
    expected_revision: number
    target: { kind: 'slide'; slide_id: string }
    instruction: string
    refs: Record<string, unknown>
  },
  signal?: AbortSignal,
) {
  return t24<Record<string, unknown>>(`/decks/${encodeURIComponent(deckId)}/regen`, {
    method: 'POST',
    body: JSON.stringify(payload),
    signal,
  })
}

export function getDeckQa(deckId: string, signal?: AbortSignal) {
  return t24<Record<string, unknown>>(`/decks/${encodeURIComponent(deckId)}/qa`, { method: 'GET', signal })
}

export function retryPageQa(
  deckId: string,
  payload: { page_no: number; expected_revision: number },
  signal?: AbortSignal,
) {
  return t24<Record<string, unknown>>(`/decks/${encodeURIComponent(deckId)}/qa/retry`, {
    method: 'POST',
    body: JSON.stringify(payload),
    signal,
  })
}

export function requestDeckExport(
  deckId: string,
  payload: { format: 'pptx' | 'pdf'; expected_revision: number; idempotency_key?: string | null },
  signal?: AbortSignal,
) {
  return t24<Record<string, unknown>>(`/decks/${encodeURIComponent(deckId)}/exports`, {
    method: 'POST',
    body: JSON.stringify(payload),
    signal,
  })
}

export function getDeckPage(deckId: string, slideId: string, signal?: AbortSignal) {
  return t24<Record<string, unknown>>(
    `/decks/${encodeURIComponent(deckId)}/pages/${encodeURIComponent(slideId)}`,
    { method: 'GET', signal },
  )
}

// ── Lesson plan（LessonPlanSpec，同形）────────────────────────────────────

export function getLessonPlan(specId: string, signal?: AbortSignal) {
  return t24<Record<string, unknown>>(`/lesson-plans/${encodeURIComponent(specId)}`, { method: 'GET', signal })
}

export function listLessonPlanRevisions(specId: string, signal?: AbortSignal) {
  return t24<Record<string, unknown>>(`/lesson-plans/${encodeURIComponent(specId)}/revisions`, { method: 'GET', signal })
}

export function revertLessonPlanRevision(
  specId: string,
  revision: number,
  payload: { expected_revision: number; summary?: string | null },
  signal?: AbortSignal,
) {
  return t24<Record<string, unknown>>(
    `/lesson-plans/${encodeURIComponent(specId)}/revisions/${revision}/revert`,
    { method: 'POST', body: JSON.stringify(payload), signal },
  )
}

export function proposeLessonPlanPatch(
  specId: string,
  payload: {
    expected_revision: number
    summary: string
    source: string
    idempotency_key?: string | null
    operations: unknown[]
  },
  signal?: AbortSignal,
) {
  return t24<Record<string, unknown>>(`/lesson-plans/${encodeURIComponent(specId)}/patches`, {
    method: 'POST',
    body: JSON.stringify(payload),
    signal,
  })
}

export function applyLessonPlanPatch(
  specId: string,
  patchId: string,
  payload: { expected_revision: number },
  signal?: AbortSignal,
) {
  return t24<Record<string, unknown>>(
    `/lesson-plans/${encodeURIComponent(specId)}/patches/${encodeURIComponent(patchId)}/apply`,
    { method: 'POST', body: JSON.stringify(payload), signal },
  )
}

export function rejectLessonPlanPatch(
  specId: string,
  patchId: string,
  payload: { reason?: string | null } = {},
  signal?: AbortSignal,
) {
  return t24<Record<string, unknown>>(
    `/lesson-plans/${encodeURIComponent(specId)}/patches/${encodeURIComponent(patchId)}/reject`,
    { method: 'POST', body: JSON.stringify(payload), signal },
  )
}

export function regenLessonPlanTarget(
  specId: string,
  payload: {
    expected_revision: number
    target: { kind: 'block'; block_id: string }
    instruction: string
    refs: Record<string, unknown>
  },
  signal?: AbortSignal,
) {
  return t24<Record<string, unknown>>(`/lesson-plans/${encodeURIComponent(specId)}/regen`, {
    method: 'POST',
    body: JSON.stringify(payload),
    signal,
  })
}

export function requestLessonPlanExport(
  specId: string,
  payload: { format: 'docx'; expected_revision: number; idempotency_key?: string | null },
  signal?: AbortSignal,
) {
  return t24<Record<string, unknown>>(`/lesson-plans/${encodeURIComponent(specId)}/exports`, {
    method: 'POST',
    body: JSON.stringify(payload),
    signal,
  })
}

// ── 导出作业 / artifact / 下载票据（deck 与 lesson plan 共用）────────────

export function getExportJob(jobId: string, signal?: AbortSignal) {
  return t24<Record<string, unknown>>(`/exports/${encodeURIComponent(jobId)}`, { method: 'GET', signal })
}

export function cancelExportJob(jobId: string, signal?: AbortSignal) {
  return t24<Record<string, unknown>>(`/exports/${encodeURIComponent(jobId)}/cancel`, {
    method: 'POST',
    signal,
  })
}

export function getArtifact(artifactId: string, signal?: AbortSignal) {
  return t24<Record<string, unknown>>(`/${encodeURIComponent(artifactId)}`, { method: 'GET', signal })
}

export function createDownloadTicket(
  artifactId: string,
  ttlSeconds?: number | null,
  signal?: AbortSignal,
) {
  return t24<Record<string, unknown>>(`/${encodeURIComponent(artifactId)}/tickets`, {
    method: 'POST',
    body: JSON.stringify(ttlSeconds == null ? {} : { ttl_seconds: ttlSeconds }),
    signal,
  })
}

/**
 * 票据 → 下载 URL。
 *
 * 服务端给的 `url` 是权威值（可能带签名/前置 CDN）；只有服务端没给时才按契约
 * `GET /downloads/{ticket}` 自行拼接。**不做**bucket/key 拼装（浏览器不得知道存储细节）。
 */
export function downloadUrlOf(
  ticket: { url?: string | null; ticket?: string | null } | null | undefined,
): string | null {
  if (!ticket) return null
  if (typeof ticket.url === 'string' && ticket.url) return ticket.url
  if (typeof ticket.ticket === 'string' && ticket.ticket) {
    return `${ARTIFACTS_API_BASE}/downloads/${encodeURIComponent(ticket.ticket)}`
  }
  return null
}
