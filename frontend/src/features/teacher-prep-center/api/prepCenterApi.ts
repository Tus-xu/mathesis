/**
 * 备课中心（teacher-prep-center）REST 客户端。
 *
 * 唯一数据源：8100 `/api/teacher-v3/prep/**`（经 vite 代理，同源）。
 * 铁律：
 * - 只打真实后端；本文件不提供任何 mock 分支。
 * - Bearer token 复用主应用 `@/api/authSession`（不新增第二套令牌来源）。
 * - 响应信封 `{code,message,data}`：code!==0 一律抛错，不吞不伪装成功。
 * - 所有请求显式携带 AbortSignal，由调用方掌控竞态。
 */
import { getAccessToken } from '@/api/authSession'

export const PREP_CENTER_API_BASE = '/api/teacher-v3/prep'

/** 归一化业务错误（kind 与 teacher-prep-v2 的语义保持一致，便于上层统一文案）。 */
export type PrepCenterErrorKind =
  | 'unauthenticated'
  | 'forbidden'
  | 'conflict'
  | 'not_found'
  | 'rate_limited'
  | 'network'
  | 'error'

export class PrepCenterApiError extends Error {
  readonly kind: PrepCenterErrorKind
  readonly status: number | null
  readonly code: number | null
  readonly detail?: string
  readonly requestId: string | null
  /** 服务端 fail 信封 data（如 time_budget_exceeded 的超额分钟数）。 */
  readonly data: Record<string, unknown> | null
  /** 服务端 error_key（契约化业务错误标识，如 time_budget_exceeded）。 */
  readonly errorKey: string | null

  constructor(info: {
    kind: PrepCenterErrorKind
    status: number | null
    code: number | null
    detail?: string
    requestId?: string | null
    data?: Record<string, unknown> | null
    errorKey?: string | null
  }) {
    super(info.detail || `prep-center api error: ${info.kind}`)
    this.name = 'PrepCenterApiError'
    this.kind = info.kind
    this.status = info.status
    this.code = info.code
    this.detail = info.detail
    this.requestId = info.requestId ?? null
    this.data = info.data ?? null
    this.errorKey = info.errorKey ?? ((info.data?.error_key as string) ?? null)
  }
}

function normalizeError(input: {
  status?: number | null
  code?: number | null
  detail?: string
  requestId?: string | null
  data?: Record<string, unknown> | null
  errorKey?: string | null
}): PrepCenterApiError {
  const status = input.status ?? null
  let kind: PrepCenterErrorKind
  if (status === 401) kind = 'unauthenticated'
  else if (status === 403) kind = 'forbidden'
  else if (status === 409) kind = 'conflict'
  else if (status === 404) kind = 'not_found'
  else if (status === 429) kind = 'rate_limited'
  else if (status === null || status === -1) kind = 'network'
  else kind = 'error'
  return new PrepCenterApiError({
    kind,
    status,
    code: input.code ?? null,
    detail: input.detail,
    requestId: input.requestId,
    data: input.data ?? null,
    errorKey: input.errorKey ?? null,
  })
}

function authHeader(): Record<string, string> {
  const token = getAccessToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
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
): Promise<T> {
  let res: Response
  try {
    res = await fetch(`${PREP_CENTER_API_BASE}${path}`, {
      ...init,
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
        ...authHeader(),
        ...(init.headers || {}),
      },
    })
  } catch (e) {
    if ((e as Error)?.name === 'AbortError') throw e
    throw normalizeError({ status: null, detail: (e as Error)?.message })
  }

  if (!res.ok) {
    let code: number | null = null
    let detail: string | undefined
    let data: Record<string, unknown> | null = null
    let errorKey: string | null = res.headers.get('x-error-key')
    let requestId: string | null = res.headers.get('x-request-id')
    try {
      const body = (await res.json()) as Envelope<unknown> & { detail?: string; error_key?: string }
      code = typeof body?.code === 'number' ? body.code : null
      detail = typeof body?.message === 'string' ? body?.message : body?.detail
      requestId = requestId ?? (body?.request_id ?? null)
      if (body?.data && typeof body.data === 'object') data = body.data as Record<string, unknown>
      errorKey = errorKey ?? (typeof body?.error_key === 'string' ? body.error_key : null)
    } catch {
      /* 非 JSON 错误体：仅用状态判定 */
    }
    throw normalizeError({ status: res.status, code, detail, requestId, data, errorKey })
  }

  if (res.status === 204) return undefined as T
  const body = (await res.json()) as Envelope<T> | T
  if (body && typeof body === 'object' && 'data' in (body as object)) {
    const env = body as Envelope<T> & { error_key?: string }
    if (typeof env.code === 'number' && env.code !== 0) {
      throw normalizeError({
        status: res.status,
        code: env.code,
        detail: env.message,
        requestId: env.request_id ?? res.headers.get('x-request-id'),
        data: (env.data && typeof env.data === 'object' ? env.data : null) as Record<string, unknown> | null,
        errorKey: typeof env.error_key === 'string' ? env.error_key : null,
      })
    }
    return env.data as T
  }
  return body as T
}

// ═══════════════════════════════════════════════════════════════════
// 类型（依据 2026-09-19 实测响应形状；宽松保留未知字段）
// ═══════════════════════════════════════════════════════════════════

/** GET /prep/sessions · 会话摘要（含意图）。 */
export interface PrepSessionSummary {
  session_id: string
  teacher_id?: string
  class_id: string | null
  class_name: string | null
  status: string
  revision: number
  title: string | null
  intent: {
    source_id: string | null
    section_id: string | null
    lesson_hours: number | null
    lesson_scope: string | null
    knowledge_points: string[] | null
    objectives?: unknown[] | null
  } | null
  opened_at: string | null
  created_at: string | null
  updated_at: string | null
  last_event_seq?: number
}

/** 会话消息（timeline 单元）。component 为服务端投影的 UI 组件。 */
export interface PrepTimelineMessage {
  message_id: string
  seq: number
  role: 'user' | 'assistant' | string
  kind: string
  component: { type: string; props: Record<string, unknown> } | null
  text: string | null
  intent: Record<string, unknown> | null
  run_status: string | null
  error_label: string | null
  error_code: string | null
  editable: boolean
  created_at: string | null
}

/** living artifact.context：上下文快照展示摘要（服务端权威投影）。 */
export interface PrepContextSummary {
  snapshot_id: string
  snapshot_hash: string
  /** 章节级 SourceLocator ref（教材查看/依据卡权威锚；缺省 = 未锚定）。 */
  locator?: string | null
  selection: Record<string, unknown> | null
  source: { id: string | null; title: string | null }
  section: { id: string | null; title: string | null; section_path: string | null }
  objectives: { text: string | null; source_ref: string | null }[]
  knowledge_points: string[]
  warnings: string[]
}

/** 蓝图（教学路径）摘要：dto + 完整 body。 */
export interface PrepBlueprintArtifact {
  blueprint_id?: string
  status?: string
  revision?: number
  body: {
    title?: string
    objectives?: { text: string; source_ref?: string | null }[]
    key_points?: { text: string }[]
    difficult_points?: { text: string }[]
    lesson_flow?: PrepBlueprintStage[]
  } | null
}

/** living artifact.lesson_plan 摘要。 */
export interface PrepLessonPlanSummary {
  spec_id: string
  blueprint_id: string
  revision: number
  title: string
  approved_blueprint_revision: number | null
  context_snapshot_hash: string | null
  updated_at: string | null
}

/** living artifact.teaching_deck：完整 deck spec（另带 deck_id/spec_id 别名）。 */
export interface PrepDeckArtifact {
  deck_id?: string
  spec_id?: string
  revision?: number
  title?: string
  metadata?: Record<string, unknown>
  pages?: unknown[]
  [key: string]: unknown
}

/** GET /prep/conversations/{id} · 权威工作区快照。 */
export interface PrepWorkspaceSnapshot {
  session: PrepSessionSummary
  timeline: PrepTimelineMessage[]
  artifact: {
    context: PrepContextSummary | null
    blueprint: PrepBlueprintArtifact | null
    diagnostic: unknown
    lesson_plan: PrepLessonPlanSummary | null
    teaching_deck: PrepDeckArtifact | null
    /** R3「已考虑」胶囊：最新 published 班级画像学情摘要（无画像 → 缺省）。 */
    class_profile_summary?: string | null
  }
  evidence: { ref: string; kind: string; title: string; snippet: string }[]
  available_actions: { action: string; target: string | null; copy_key: string; disabled: boolean }[]
  job_summary: Record<string, unknown>[]
  basic_mode: boolean
}

/** GET /prep/conversations/{id}/specs · 会话教案 spec 列表。 */
export interface PrepSpecSummary {
  spec_id: string
  blueprint_id: string
  revision: number
  title: string
  approved_blueprint_revision: number | null
  context_snapshot_hash: string | null
  lineage?: unknown
  updated_at: string | null
}

// ═══════════════════════════════════════════════════════════════════
// 端点
// ═══════════════════════════════════════════════════════════════════

/** 会话列表（`GET /prep/sessions`：含 intent 摘要，供首页「最近备课」）。 */
export function listSessions(signal?: AbortSignal) {
  return request<{ items: PrepSessionSummary[] }>('/sessions', { method: 'GET', signal })
}

/** NL 新建会话（`POST /prep/conversations`；同步受理：意图解析在请求路径内，可达分钟级）。 */
export function createConversation(
  payload: { idempotency_key: string; message: string; class_id?: string | null },
  signal?: AbortSignal,
) {
  return request<PrepWorkspaceSnapshot>('/conversations', {
    method: 'POST',
    body: JSON.stringify(payload),
    signal,
  })
}

/** 拉取权威工作区快照。 */
export function getWorkspace(sessionId: string, signal?: AbortSignal) {
  return request<PrepWorkspaceSnapshot>(
    `/conversations/${encodeURIComponent(sessionId)}`,
    { method: 'GET', signal },
  )
}

/** 会话教案 spec 列表。 */
export function listSessionSpecs(sessionId: string, signal?: AbortSignal) {
  return request<{ items: PrepSpecSummary[]; total: number }>(
    `/conversations/${encodeURIComponent(sessionId)}/specs`,
    { method: 'GET', signal },
  )
}

/* ═══════════════════════════════════════════════════════════════════
 * 发布到班级课堂（教师确认的 deck 快照 → 8000 班级课件）
 * 8100 编排：校验教师/班级/课件归属 → 打包 slides/资产 → 调 8000 创建 artifact。
 * 返回 preparing 表示已受理，讲稿/练习在后台准备；学生端只看到 published。
 * ═══════════════════════════════════════════════════════════════════ */

export interface PublishClassroomBody {
  class_id: string
  /** 缺省 → 8100 取会话最新 deck projection */
  deck_id?: string
  title?: string
  /** 是否把随堂练习一并下发（默认由对话框勾选） */
  include_practice?: boolean
}

export interface PublishClassroomResult {
  courseware_id: string
  status: string
  title: string
  class_id: string
  warnings: string[]
}

/** 发布到班级（`POST /prep/sessions/{sid}/publish-classroom`；返回已受理的 courseware_id）。 */
export function publishClassroom(
  sessionId: string,
  body: PublishClassroomBody,
  signal?: AbortSignal,
) {
  return request<PublishClassroomResult>(
    `/sessions/${encodeURIComponent(sessionId)}/publish-classroom`,
    { method: 'POST', body: JSON.stringify(body), signal },
  )
}

/**
 * 发送消息（选择卡点击走结构化 selection 直传，不经 LLM；
 * 自然语言走服务端意图解析）。
 */
export function sendMessage(
  sessionId: string,
  payload: {
    message: string
    idempotency_key?: string | null
    expected_revision?: number | null
    selection?: { textbook_id?: string; section_id?: string; class_id?: string }
    /** R3 AI 定向修改：lesson_flow 节点下标（十进制字符串）——存在即走节点修订链路 */
    target_node_id?: string | null
  },
  signal?: AbortSignal,
) {
  return request<PrepWorkspaceSnapshot>(
    `/conversations/${encodeURIComponent(sessionId)}/messages`,
    { method: 'POST', body: JSON.stringify(payload), signal },
  )
}

/**
 * 执行服务端下发的动作（`POST /prep/conversations/{id}/actions`）。
 *
 * - `action`/`target` 必须来自快照 `available_actions`（前端不发明动作与目标）；
 * - `expected_revision` 为**会话 revision**（服务端按会话行强校验，冲突返回 409）；
 * - `idempotencyKey` 必填：网络重试不得产生第二次命令；
 * - 返回完整工作区快照（与 GET 同结构），调用方直接替换 store.current。
 */
export function runPrepAction(
  sessionId: string,
  payload: {
    action: string
    target?: string | null
    expectedRevision: number
    payload?: Record<string, unknown>
    idempotencyKey: string
  },
  signal?: AbortSignal,
) {
  return request<PrepWorkspaceSnapshot>(
    `/conversations/${encodeURIComponent(sessionId)}/actions`,
    {
      method: 'POST',
      body: JSON.stringify({
        idempotency_key: payload.idempotencyKey,
        action: payload.action,
        target: payload.target ?? null,
        expected_revision: payload.expectedRevision,
        ...(payload.payload ? { payload: payload.payload } : {}),
      }),
      signal,
    },
  )
}

// ═══════════════════════════════════════════════════════════════════
// R3 课堂设计页：节点编辑 / AI 定向修改 / 历史版本 / 教材查看
// ═══════════════════════════════════════════════════════════════════

/** 蓝图节点条目（{text, source_ref?}）。 */
export interface PrepNodeItem {
  text: string
  source_ref?: string | null
}

/** 蓝图教学环节（lesson_flow 单元；R3 additive 三组+提示为可选）。 */
export interface PrepBlueprintStage {
  stage: string
  title: string
  duration_minutes: number | null
  /** 节点描述性摘要（W1.3 additive，可选；旧 body 无该字段 → 回退 items[0]，不影响类型检查）。 */
  summary?: string | null
  items: PrepNodeItem[]
  teacher_questions?: PrepNodeItem[] | null
  student_activities?: PrepNodeItem[] | null
  board_notes?: PrepNodeItem[] | null
  hints?: PrepNodeItem[] | null
  edited_by?: 'teacher' | 'ai' | string | null
  edited_at?: string | null
}

/** PATCH node 请求载荷（缺省键=不修改；duration_minutes 显式 null=清除）。 */
export interface PrepNodeEditPayload {
  idempotency_key: string
  expected_revision: number
  node_index: number
  title?: string
  duration_minutes?: number | null
  teacher_questions?: PrepNodeItem[]
  student_activities?: PrepNodeItem[]
  board_notes?: PrepNodeItem[]
  hints?: PrepNodeItem[]
}

/** 教师在线编辑单节点（真实落库：新 revision，source=teacher）。 */
export function editBlueprintNode(sessionId: string, payload: PrepNodeEditPayload, signal?: AbortSignal) {
  return request<PrepWorkspaceSnapshot>(
    `/conversations/${encodeURIComponent(sessionId)}/blueprint/node`,
    { method: 'PATCH', body: JSON.stringify(payload), signal },
  )
}

/** 蓝图历史版本（不可变快照；时间/来源/裁决/完整 body）。 */
export interface PrepBlueprintRevision {
  revision_no: number
  verdict: string
  source: 'teacher' | 'ai_suggestion' | 'system' | string
  actor_role: string
  note: string | null
  title: string | null
  stages: number
  total_minutes: number | null
  created_at: string | null
  body: Record<string, unknown>
}

export function listBlueprintRevisions(sessionId: string, signal?: AbortSignal) {
  return request<{ blueprint_id: string | null; current_revision: number; items: PrepBlueprintRevision[] }>(
    `/conversations/${encodeURIComponent(sessionId)}/blueprint/revisions`,
    { method: 'GET', signal },
  )
}

/** 教材查看（真实切片原文；章节名/页码/LaTeX chunk 全文）。 */
export interface PrepTextbookView {
  ref: string
  kind: string
  textbook: string
  source_title: string
  subject?: string
  section: { id: string; title: string; section_path: string | null; page_range: [number, number] | null }
  page_range: [number, number] | null
  text: string
  chunks: { id: string; order: number | null; page_range: [number, number] | null; text: string }[]
  rights_status: string
}

export function fetchTextbookView(sessionId: string, ref: string, signal?: AbortSignal) {
  return request<PrepTextbookView>(
    `/conversations/${encodeURIComponent(sessionId)}/textbook/view?ref=${encodeURIComponent(ref)}`,
    { method: 'GET', signal },
  )
}

/** SSE 事件帧（wrapped 契约：data={seq,event_type,payload,...}）。 */
export interface PrepSseFrame {
  seq: number
  event_id?: string
  event_type: string
  payload: Record<string, unknown>
  created_at?: string | null
  revision?: number | null
}

/** 会话 durable 事件列表（SSE replay 同源；AI 流程的代理安全轮询通道）。 */
export function listSessionEvents(sessionId: string, signal?: AbortSignal) {
  return request<{ items: PrepSseFrame[] }>(
    `/sessions/${encodeURIComponent(sessionId)}/events?limit=1000`,
    { method: 'GET', signal },
  )
}

/**
 * 会话消息流（`GET /prep/conversations/{id}/messages`）。
 *
 * 权威消息流通道（W1-C 对话面）：返回 `MessageView[]`（与 workspace.timeline 同构）。
 * 用于进入工作台 / 刷新 / 重进时**完整还原**历史消息；SSE 增量到达后由此重新拉取，
 * 避免只依赖内存里的增量拼装（会话切换 / 断线重连均可自愈）。
 *
 * 说明：本函数为 W1-C **加法式**新增端点，不改动既有任何方法语义/签名。
 */
export function listMessages(sessionId: string, limit = 200, signal?: AbortSignal) {
  return request<PrepTimelineMessage[]>(
    `/conversations/${encodeURIComponent(sessionId)}/messages?limit=${limit}`,
    { method: 'GET', signal },
  )
}
