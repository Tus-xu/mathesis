/**
 * deck（TeachingDeckSpec）编辑 REST 客户端（R4）。
 *
 * 端点（8100 `/api/teacher-v3/artifacts/decks/{deck_id}`，ppt_v2 权威面）：
 * - POST /patches → 提案；POST /patches/{id}/apply → 应用（revision+1）
 * - POST /regen → AI 单页修改 / 快捷操作（content | note_practice）
 * - POST /pages → AI 添加页（基于锚点页生成真实内容，禁止空白假页）
 * - POST /enrich → 逐页增稠（异步作业；进度经 `artifacts/exports/{job_id}` 查询）
 *
 * 铁律：与 prepCenterApi 同一错误语义（code!==0 抛 PrepCenterApiError）；Bearer 复用主应用令牌。
 */
import { getAccessToken } from '@/api/authSession'
import { PrepCenterApiError, type PrepCenterErrorKind } from './prepCenterApi'

export const DECK_API_BASE = '/api/teacher-v3/artifacts/decks'

function authHeader(): Record<string, string> {
  const token = getAccessToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

async function deckRequest<T>(deckId: string, path: string, init: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(`${DECK_API_BASE}/${encodeURIComponent(deckId)}${path}`, {
      ...init,
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
        ...authHeader(),
        ...(init.headers || {}),
      },
    })
  } catch (e) {
    throw new PrepCenterApiError({ kind: 'network', status: null, code: null, detail: (e as Error)?.message })
  }
  const body = (await res.json().catch(() => null)) as
    | { code?: number; message?: string; data?: T; request_id?: string; error_key?: string; detail?: string }
    | null
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
      detail: body?.message || body?.detail || `deck api error: ${status}`,
      requestId: body?.request_id ?? null,
    })
  }
  return (body.data ?? (body as unknown)) as T
}

// ── 类型（宽松：与 app/ppt_v2/domain/spec.py 对齐，未知字段保留） ─────────────

export interface MathObjectStyle {
  color?: string
  stroke_px?: number
  dash?: 'solid' | 'dashed' | 'dotted' | string
}

export interface MathObjectToggles {
  show_foci?: boolean
  show_axes?: boolean
  show_aux_lines?: boolean
  show_value_labels?: boolean
}

export interface MathObject {
  kind: 'ellipse' | string
  params?: Record<string, number>
  toggles?: MathObjectToggles
  style?: MathObjectStyle
}

/**
 * 语义化版式元素（FROZEN-CONTRACT §A）：**严禁出现坐标**，版式由渲染层从 role 派生。
 * 旧 spec 无 elements（`null`/`undefined`/`[]`）时渲染层退化为 content 排版（向后兼容）。
 */
export type SlideElementRole =
  | 'definition'
  | 'formula'
  | 'knowledge_card'
  | 'figure'
  | 'bullets'
  | 'note'
  | 'steps'
  | 'table'
  | string

export interface SlideElement {
  /** 版式角色（枚举，必填）。 */
  role: SlideElementRole
  /** 正文（≤400 字，可含行内 `$...$` KaTeX）。 */
  text?: string | null
  /** 显示式 LaTeX 源码（不含 `$` 定界符）。 */
  latex?: string | null
  /** 卡片标题（≤60 字，仅 knowledge_card 用）。 */
  title?: string | null
  /** 条目列表（≤10 项，每项 ≤200 字，可含行内 KaTeX；table 每项 `|` 分隔单元格）。 */
  items?: string[]
  /** 图形引用（仅 figure 用）。 */
  figure_ref?: 'math_object' | null
}

export interface DeckSlide {
  id: string
  type: string
  teaching_goal?: string
  title?: string
  content?: string
  /** 语义化版式元素（FROZEN-CONTRACT §A；缺省 → 退化 content 排版）。 */
  elements?: SlideElement[] | null
  source_refs?: string[]
  visual_intent?: string
  layout_hint?: string | null
  speaker_note?: string | null
  revision?: number
  status?: string
  qa_status?: string
  math_object?: MathObject | null
  /**
   * 本页图片资产 id 列表（MATH_RENDER 通道：直线 / 二次函数 / 幂函数图象）。
   *
   * 与 `math_object` 是两条并列的图形通道：`math_object` 由前端确定性渲染（椭圆），
   * `assets` 是服务端已渲染好的 PNG（内容寻址，字节在 MinIO）。编辑器此前只认前者，
   * 导致走资产通道的图形恒为空白 —— 取图走 `fetchDeckAssetUrl`。
   */
  assets?: string[]
}

export interface DeckOutlineEntry {
  id: string
  goal: string
  slide_ids: string[]
}

/** 工作区快照 artifact.teaching_deck 的形状（另带服务端别名 deck_id/revision/updated_at）。 */
export interface DeckArtifact {
  deck_id?: string
  spec_id?: string
  revision?: number
  title?: string
  updated_at?: string | null
  schema_version?: string
  metadata?: { title?: string; grade?: string | null; duration_minutes?: number | null; language?: string }
  outline?: DeckOutlineEntry[]
  slides?: DeckSlide[]
  speaker_notes?: Record<string, string>
  change_log?: { revision: number; ts?: string; kind?: string; summary?: string }[]
  projection?: {
    approved_blueprint_id?: string
    approved_blueprint_revision?: number
    context_snapshot_hash?: string
    run_hash?: string
    body_hash?: string
    projected_at?: string
  }
  [key: string]: unknown
}

export interface PatchOp {
  op: 'set_slide' | 'add_slide' | 'remove_slide' | 'move_slide' | 'set_metadata' | string
  target?: string
  field?: string
  value?: unknown
  before?: unknown
}

export interface DeckPatchProposalView {
  patch_id: string
  deck_id: string
  status: string
  expected_revision: number
  base_revision?: number
  summary?: string
  operations?: PatchOp[]
  changed_slide_ids?: string[]
  applied_revision?: number | null
  refs_echo?: Record<string, unknown>
  provider?: { model?: string; run_id?: string }
}

export interface DeckApplyResult {
  deck_id: string
  revision: number
  applied_patch_id?: string
  affected?: { slide_ids?: string[]; rationale?: string }
}

export interface DeckRefs {
  approved_blueprint_id: string
  blueprint_revision: number
  spec_revision: number
  prompt_ref: { prompt_id: string }
  context_manifest_id: string
}

/**
 * 课件图片资产的短时效访问 URL（R8c：编辑器渲染 `slide.assets` 的唯一取图入口）。
 *
 * 返回 MinIO presign URL（TTL 30min）；失败抛 `PrepCenterApiError`，由调用方显式降级提示
 * （禁止静默空白，遵守 UI Reality Contract）。
 */
export async function fetchDeckAssetUrl(
  deckId: string,
  assetId: string,
  signal?: AbortSignal,
): Promise<{ asset_id: string; url: string; expires_in: number }> {
  return deckRequest<{ asset_id: string; url: string; expires_in: number }>(
    deckId,
    `/assets/${encodeURIComponent(assetId)}/url`,
    { method: 'GET', signal },
  )
}

export function newIdempotencyKey(): string {
  return crypto?.randomUUID?.() ?? `pc-${Date.now()}-${Math.random().toString(36).slice(2)}`
}

// ── 端点 ─────────────────────────────────────────────────────────────────────

/** 提交编辑提案（乐观并发：expected_revision）。 */
export function createDeckPatch(
  deckId: string,
  payload: { expected_revision: number; summary: string; source?: 'human' | 'ai'; operations: PatchOp[] },
  signal?: AbortSignal,
) {
  return deckRequest<DeckPatchProposalView>(deckId, '/patches', {
    method: 'POST',
    body: JSON.stringify({
      idempotency_key: newIdempotencyKey(),
      source: 'human',
      ...payload,
    }),
    signal,
  })
}

/** 应用提案：revision+1，返回影响面。 */
export function applyDeckPatch(deckId: string, patchId: string, expectedRevision: number, signal?: AbortSignal) {
  return deckRequest<DeckApplyResult>(deckId, `/patches/${encodeURIComponent(patchId)}/apply`, {
    method: 'POST',
    body: JSON.stringify({ expected_revision: expectedRevision }),
    signal,
  })
}

/** AI 局部重生成：mode=content 改写本页正文；note_practice 生成随堂练习追加到备注；
 *  derivation 生成编号推导链（前端投影为 steps，可在预览中逐步播放）。 */
export function regenDeckPage(
  deckId: string,
  payload: {
    expected_revision: number
    target: { kind: 'slide'; slide_id: string }
    instruction: string
    refs: DeckRefs
    mode?: 'content' | 'note_practice' | 'derivation'
  },
  signal?: AbortSignal,
) {
  return deckRequest<DeckPatchProposalView>(deckId, '/regen', {
    method: 'POST',
    body: JSON.stringify(payload),
    signal,
  })
}

/** 为本课补齐确定性数学配图（最多 3 张；无候选诚实 no-op）。 */
export function ensureDeckFigures(
  deckId: string,
  payload: { expected_revision: number; refs?: DeckRefs; max_figures?: number },
  signal?: AbortSignal,
) {
  return deckRequest<{
    changed: boolean
    figures_added?: number
    figures?: { kind: string; asset_id: string; target_slide_id: string; target_title: string }[]
    revision?: number
    warnings?: string[]
  }>(deckId, '/figures', {
    method: 'POST',
    body: JSON.stringify(payload),
    signal,
  })
}

/**
 * 逐页增稠：受理异步作业（服务端逐页 LLM 把骨架页补全为板书内容，失败页确定性兜底）。
 * 返回 `{job_id}`；进度经 `GET /artifacts/exports/{job_id}` 查询，完成后
 * `GET /artifacts/decks/{deck_id}`（或工作区快照）即读到新 revision。
 * 无页需要变更时不产生新 revision（诚实 no-op）。
 */
export function enrichDeck(deckId: string, expectedRevision: number, signal?: AbortSignal) {
  return deckRequest<{ job_id: string; task_id?: string; deck_id: string; status?: string }>(deckId, '/enrich', {
    method: 'POST',
    body: JSON.stringify({ expected_revision: expectedRevision }),
    signal,
  })
}

/** AI 添加页：基于锚点页生成真实内容，返回 add_slide+move_slide 提案。 */
export function addDeckPage(
  deckId: string,
  payload: { expected_revision: number; after_slide_id: string; instruction: string; refs: DeckRefs },
  signal?: AbortSignal,
) {
  return deckRequest<DeckPatchProposalView>(deckId, '/pages', {
    method: 'POST',
    body: JSON.stringify(payload),
    signal,
  })
}

// ── PPT 内容/视觉质检（真实 QA 行；供 S7「教学内容已检查」消费）───────────────

/** 课件质检视图（GET /decks/{deck_id}/qa）；无 QA 行 → status='unknown'。 */
export interface DeckQaView {
  deck_id: string
  spec_revision?: number | null
  checks?: string[]
  summary?: { p0_count?: number; p1_count?: number; failed_pages?: number[]; by_kind?: Record<string, number> }
  status?: 'pass' | 'fail' | 'unknown' | string
}

/** 读取课件质检结果（真实数据，禁止前端编造通过状态）。 */
export function getDeckQa(deckId: string, signal?: AbortSignal) {
  return deckRequest<DeckQaView>(deckId, '/qa', { method: 'GET', signal })
}