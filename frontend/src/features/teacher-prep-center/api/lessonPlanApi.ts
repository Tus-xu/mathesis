/**
 * 教案（LessonPlanSpec）REST 客户端（R5 · 教师端备课中心）。
 *
 * 端点（8100 教案权威面，经 vite 代理同源）：
 * - GET  `/api/teacher-v3/artifacts/lesson-plans/{spec_id}`            读全量 spec
 * - POST `/api/teacher-v3/artifacts/lesson-plans/{spec_id}/patches`    块级编辑提案
 * - POST `/api/teacher-v3/artifacts/lesson-plans/{spec_id}/patches/{id}/apply` 应用（revision+1）
 * - POST `/api/teacher-v3/artifacts/lesson-plans/{spec_id}/regen`      AI 局部重生成（真实 LLM）
 *
 * 铁律：
 * - 只打真实后端；本文件不提供任何 mock 分支。
 * - Bearer token 复用主应用 `@/api/authSession`（不新增第二套令牌来源）。
 * - 响应信封 `{code,message,data}`：code!==0 一律抛 PrepCenterApiError，不吞不伪装成功。
 * - 所有请求显式携带 AbortSignal，由调用方掌控竞态。
 *
 * 结构契约见 `docs/product/teacher-prep-center/FROZEN-CONTRACT.md` §F（additive 可选字段，
 * 缺失时读取方必须优雅退化）。
 */
import { getAccessToken } from '@/api/authSession'
import { PrepCenterApiError, type PrepCenterErrorKind } from './prepCenterApi'

export const LESSON_PLAN_API_BASE = '/api/teacher-v3/artifacts/lesson-plans'

// ═══════════════════════════════════════════════════════════════════
// 类型（依据 FROZEN-CONTRACT §F；全部为读取侧宽松类型，未知字段保留）
// ═══════════════════════════════════════════════════════════════════

/** 一条教案内容项（text + 可解析 SourceLocator）。 */
export interface PlanItem {
  text: string
  source_ref?: string | null
  evidence_refs?: string[]
}

/** 教学重难点项：`kind` 为 §F.1 新增可选枚举，用于区分「重点/难点」胶囊。 */
export interface PlanKeyPointItem extends PlanItem {
  kind?: 'key' | 'difficult' | null
}

/** 数学对象参数（复用 Deck 的 MathObjectSpec，参数化 SVG）。 */
export interface PlanMathObject {
  kind: string
  params?: Record<string, number>
  toggles?: { show_foci?: boolean; show_axes?: boolean; show_aux_lines?: boolean; show_value_labels?: boolean }
  style?: { color?: string; stroke_px?: number; dash?: string }
}

/** 教学过程列（§F.2 additive）。 */
export interface PlanStageColumns {
  teacher_activity?: PlanItem[]
  student_activity?: PlanItem[]
  design_intent?: PlanItem[]
}

/** 教学过程环节（行 = 教学路径节点，与 lesson_flow 一一对应）。 */
export interface PlanStage {
  stage: string
  title: string
  duration_minutes?: number | null
  items?: PlanItem[]
  /** §F.2：五列表所需的活动/意图列（缺省 = 未投影，渲染层退化）。 */
  columns?: PlanStageColumns | null
}

/** 板书设计（§F.3：三列，均须有内容方可判 PASS）。 */
export interface PlanBoardDesign {
  text_points?: PlanItem[]
  math_object?: PlanMathObject | null
  formulas?: PlanItem[]
}

export interface PlanMetadata {
  title: string
  grade?: string | null
  duration_minutes?: number | null
  language?: string | null
  lesson_scope?: string | null
}

/** LessonPlanSpec body（读取侧宽松类型：§F 扩展全为可选）。 */
export interface LessonPlanBody {
  schema_version?: string
  id: string
  revision: number
  purpose?: string
  metadata: PlanMetadata
  blueprint_ref?: { blueprint_id: string; revision: number } | null
  context_ref?: { context_snapshot_hash: string; source_id: string; section_id: string; locator: string } | null
  /* ── 六章内容 ── */
  objectives?: PlanItem[]
  key_difficult_points?: PlanKeyPointItem[]
  lesson_flow?: PlanStage[]
  /** §F.1 新增 */
  board_design?: PlanBoardDesign | null
  /** §F.1 新增 */
  homework?: PlanItem[]
  /** §F.1 新增（可为空 → 渲染层给可点击编辑虚线框） */
  reflection?: PlanItem[]
  /* ── 其它既有桶（保留，供退化/兼容）── */
  activities?: PlanItem[]
  examples?: PlanItem[]
  assessment?: PlanItem[]
  evidence?: PlanItem[]
  warnings?: string[]
  source_refs?: string[]
  projection?: {
    projector?: string
    approved_blueprint_id?: string
    approved_blueprint_revision?: number
    prompt_release_id?: string | null
    run_hash?: string | null
    body_hash?: string | null
    projected_at?: string | null
  } | null
  change_log?: { revision: number; ts?: string; kind?: string; summary?: string }[]
  [key: string]: unknown
}

/** 块地址枚举（与后端 patch.py `lesson_plan_blocks` 同构）。 */
export interface PlanBlock {
  block_id: string
  kind: string
  label_zh: string
  text: string
  items: string[]
}

/** GET /{spec_id} 响应（`spec_view`）：body 在 `spec`。 */
export interface LessonPlanSpecView {
  spec_id: string
  revision: number
  schema_version: string
  spec_sha256: string
  metadata: PlanMetadata
  spec: LessonPlanBody
  blocks: PlanBlock[]
  block_count: number
  lineage: {
    approved_blueprint_id?: string | null
    blueprint_revision?: number | null
    context_snapshot_hash?: string | null
    prompt_ref?: { prompt_id?: string } | null
    evidence_refs?: string[]
    ai_run_id?: string | null
  }
  recent_change_log?: { revision: number; ts?: string; kind?: string; summary?: string }[]
}

export interface PlanPatchOp {
  op: 'set_block' | 'add_block' | 'remove_block' | 'set_field'
  target?: string
  field?: string
  value?: unknown
}

export interface PlanPatchProposalView {
  patch_id: string
  spec_id: string
  status: string
  source: string
  expected_revision: number
  base_revision?: number
  summary?: string
  operations?: PlanPatchOp[]
  affected?: { block_ids?: string[]; whole_plan?: boolean; rationale?: string }
  changed_block_ids?: string[]
  preview_revision?: number
  refs_echo?: Record<string, unknown>
  provider?: { model?: string }
}

export interface PlanApplyResult {
  spec_id: string
  revision: number
  applied_patch_id?: string
  affected?: { block_ids?: string[]; whole_plan?: boolean; rationale?: string }
  change_log_entry?: { revision: number; ts?: string; kind?: string; summary?: string }
}

export interface PlanRegenRefs {
  approved_blueprint_id: string
  blueprint_revision: number
  spec_revision: number
  prompt_ref: { prompt_id: string }
  source_refs?: string[]
  evidence_refs?: string[]
}

// ═══════════════════════════════════════════════════════════════════
// 请求内核（与 deckApi 同语义）
// ═══════════════════════════════════════════════════════════════════

function authHeader(): Record<string, string> {
  const token = getAccessToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

function newIdempotencyKey(): string {
  return crypto?.randomUUID?.() ?? `pc-${Date.now()}-${Math.random().toString(36).slice(2)}`
}

interface PlanEnvelope<T> {
  code?: number
  message?: string
  detail?: string
  data?: T
  request_id?: string
  error_key?: string
}

async function planRequest<T>(path: string, init: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(`${LESSON_PLAN_API_BASE}${path}`, {
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
    throw new PrepCenterApiError({ kind: 'network', status: null, code: null, detail: (e as Error)?.message })
  }

  let body: PlanEnvelope<T> | null = null
  try {
    body = (await res.json()) as PlanEnvelope<T>
  } catch {
    body = null
  }

  if (!res.ok || !body || (typeof body.code === 'number' && body.code !== 0)) {
    const status = res.status
    let kind: PrepCenterErrorKind = 'error'
    if (status === 401) kind = 'unauthenticated'
    else if (status === 403) kind = 'forbidden'
    else if (status === 409) kind = 'conflict'
    else if (status === 404) kind = 'not_found'
    else if (status === 429) kind = 'rate_limited'
    throw new PrepCenterApiError({
      kind,
      status,
      code: typeof body?.code === 'number' ? body.code : null,
      detail: body?.message || body?.detail || `lesson-plan api error: ${status}`,
      requestId: body?.request_id ?? res.headers.get('x-request-id') ?? null,
      data: body?.data && typeof body.data === 'object' ? (body.data as Record<string, unknown>) : null,
      errorKey: typeof body?.error_key === 'string' ? body.error_key : null,
    })
  }
  return (body.data ?? (body as unknown)) as T
}

// ═══════════════════════════════════════════════════════════════════
// 端点
// ═══════════════════════════════════════════════════════════════════

/** 读全量教案 spec（body 在返回的 `spec` 字段）。 */
export function fetchLessonPlanSpec(specId: string, signal?: AbortSignal) {
  return planRequest<LessonPlanSpecView>(`/${encodeURIComponent(specId)}`, { method: 'GET', signal })
}

/** 提交块级编辑提案（乐观并发：expected_revision）。 */
export function createLessonPlanPatch(
  specId: string,
  payload: { expected_revision: number; summary: string; source?: 'human' | 'ai' | 'regen'; operations: PlanPatchOp[] },
  signal?: AbortSignal,
) {
  return planRequest<PlanPatchProposalView>(`/${encodeURIComponent(specId)}/patches`, {
    method: 'POST',
    body: JSON.stringify({ idempotency_key: newIdempotencyKey(), source: 'human', ...payload }),
    signal,
  })
}

/** 应用提案：revision+1，返回影响面。 */
export function applyLessonPlanPatch(
  specId: string,
  patchId: string,
  expectedRevision: number,
  signal?: AbortSignal,
) {
  return planRequest<PlanApplyResult>(
    `/${encodeURIComponent(specId)}/patches/${encodeURIComponent(patchId)}/apply`,
    { method: 'POST', body: JSON.stringify({ expected_revision: expectedRevision }), signal },
  )
}

/**
 * AI 局部重生成单个块（真实 LLM，走 `lesson_plan_regen` use_case）。
 * 返回提案（含 preview_revision + changed_block_ids），由调用方决定是否 apply。
 */
export function regenLessonPlanBlock(
  specId: string,
  payload: { expected_revision: number; target: { kind: 'block'; block_id: string }; instruction: string; refs: PlanRegenRefs },
  signal?: AbortSignal,
) {
  return planRequest<PlanPatchProposalView>(`/${encodeURIComponent(specId)}/regen`, {
    method: 'POST',
    body: JSON.stringify(payload),
    signal,
  })
}
