/**
 * 备课中心「导出闭环」REST 客户端（R5：备课链路的终点）。
 *
 * 端点（8100 `/api/teacher-v3/artifacts/**`，ppt_v2 artifacts 权威面）：
 * - POST /decks/{deck_id}/exports            → 受理课件导出（format: pptx|pdf）
 * - POST /lesson-plans/{spec_id}/exports     → 受理教案导出（format: markdown|pdf）
 * - GET  /exports/{job_id}                   → 导出作业视图（status/progress/artifact_id/error）
 * - POST /{artifact_id}/tickets              → 签发一次性下载票据
 * - GET  /downloads/{ticket}                 → 文件字节（二进制，附 Content-Disposition）
 *
 * 铁律：
 * - 与 deckApi/prepCenterApi 同一错误语义（code!==0 或非 2xx → PrepCenterApiError；
 *   detail 只取 message/detail 文案，绝不把整个对象塞进错误）。
 * - downloads 返回二进制，不能复用 JSON helper（独立实现，解析 Content-Disposition 文件名）。
 * - Bearer token 复用主应用 `@/api/authSession`；本文件不提供任何 mock 分支。
 */
import { getAccessToken } from '@/api/authSession'
import { PrepCenterApiError, type PrepCenterErrorKind } from './prepCenterApi'

export const ARTIFACTS_API_BASE = '/api/teacher-v3/artifacts'

export type ExportStatus = 'pending' | 'running' | 'done' | 'failed' | 'cancelled'
export type DeckExportFormat = 'pptx' | 'pdf'
export type LessonPlanExportFormat = 'markdown' | 'pdf'
export type ExportFormat = DeckExportFormat | LessonPlanExportFormat

/** 导出作业视图（GET /exports/{job_id}；宽松保留未知字段）。 */
export interface ExportJobView {
  job_id: string
  /** 'deck' | 'lesson_plan'（服务端语义，宽松字符串）。 */
  type?: string
  format: string
  status: ExportStatus
  progress?: { stage?: string | null; [key: string]: unknown } | null
  artifact_id?: string | null
  pptx_artifact_id?: string | null
  pdf_artifact_id?: string | null
  qa_status?: string | null
  error?: string | null
  /** durable 事件流（作业自己发的权威事实；真实阶段名在 detail.stage）。 */
  events?: { seq?: number; type?: string; ts?: string | null; detail?: Record<string, unknown> | null }[]
}

/** 导出受理结果（POST .../exports）。 */
export interface ExportStartResult {
  job_id: string
  task_id?: string
  format: string
  status: ExportStatus
}

function authHeader(): Record<string, string> {
  const token = getAccessToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

function kindOfStatus(status: number): PrepCenterErrorKind {
  if (status === 401) return 'unauthenticated'
  if (status === 403) return 'forbidden'
  if (status === 409) return 'conflict'
  if (status === 404) return 'not_found'
  if (status === 429) return 'rate_limited'
  return 'error'
}

/** JSON 端点共用：信封 `{code,message,data}`；code!==0 或非 2xx 一律抛 PrepCenterApiError。 */
async function artifactRequest<T>(path: string, init: RequestInit & { signal?: AbortSignal }): Promise<T> {
  let res: Response
  try {
    res = await fetch(`${ARTIFACTS_API_BASE}${path}`, {
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
  const body = (await res.json().catch(() => null)) as
    | { code?: number; message?: string; data?: T; request_id?: string; detail?: string }
    | null
  if (!res.ok || !body || (typeof body.code === 'number' && body.code !== 0)) {
    throw new PrepCenterApiError({
      kind: kindOfStatus(res.status),
      status: res.status,
      code: typeof body?.code === 'number' ? body.code : null,
      detail: body?.message || body?.detail || `artifact api error: ${res.status}`,
      requestId: body?.request_id ?? null,
    })
  }
  return (body.data ?? (body as unknown)) as T
}

// ── 端点 ─────────────────────────────────────────────────────────────────────

/** 受理课件导出（pptx|pdf）；expected_revision 为乐观并发基线（过期 → 409 conflict）。 */
export function startDeckExport(
  deckId: string,
  format: DeckExportFormat,
  expectedRevision: number,
  signal?: AbortSignal,
) {
  return artifactRequest<ExportStartResult>(`/decks/${encodeURIComponent(deckId)}/exports`, {
    method: 'POST',
    body: JSON.stringify({ format, expected_revision: expectedRevision }),
    signal,
  })
}

/** 受理教案导出（markdown|pdf）。 */
export function startLessonPlanExport(
  specId: string,
  format: LessonPlanExportFormat,
  expectedRevision: number,
  signal?: AbortSignal,
) {
  return artifactRequest<ExportStartResult>(`/lesson-plans/${encodeURIComponent(specId)}/exports`, {
    method: 'POST',
    body: JSON.stringify({ format, expected_revision: expectedRevision }),
    signal,
  })
}

/** 导出作业视图（轮询通道）。 */
export function getExportJob(jobId: string, signal?: AbortSignal) {
  return artifactRequest<ExportJobView>(`/exports/${encodeURIComponent(jobId)}`, { method: 'GET', signal })
}

// ── 导出卡「页数·大小」真实数据源（R3）────────────────────────────────────
/** 真实导出产物摘要：size_bytes = 实际文件字节；从未导出则对应 kind 缺席（前端诚实省略）。 */
export interface ArtifactSummary {
  artifact_id: string
  kind: string
  size_bytes: number
  created_at: string | null
  file_name: string | null
}

export interface DeckArtifactsView {
  deck_id: string
  pages: number
  artifacts: Partial<Record<string, ArtifactSummary>>
}

export interface PlanArtifactsView {
  spec_id: string
  blocks: number
  artifacts: Partial<Record<string, ArtifactSummary>>
}

/** deck 页数 + 各格式最近真实产物（大小）——S6 导出卡唯一数据源。 */
export function getDeckArtifacts(deckId: string, signal?: AbortSignal) {
  return artifactRequest<DeckArtifactsView>(`/decks/${encodeURIComponent(deckId)}/artifacts`, {
    method: 'GET',
    signal,
  })
}

/** 教案块数 + 各格式最近真实产物（大小）——S6 教案导出卡唯一数据源。 */
export function getPlanArtifacts(specId: string, signal?: AbortSignal) {
  return artifactRequest<PlanArtifactsView>(`/lesson-plans/${encodeURIComponent(specId)}/artifacts`, {
    method: 'GET',
    signal,
  })
}

/** 字节数 → 展示大小（真实换算；仅在拿到真实 size_bytes 后调用）。 */
export function formatBytes(n: number): string {
  if (!Number.isFinite(n) || n <= 0) return ''
  if (n < 1024) return `${n} B`
  const kb = n / 1024
  if (kb < 1024) return `${kb.toFixed(1)} KB`
  const mb = kb / 1024
  if (mb < 1024) return `${mb.toFixed(1)} MB`
  return `${(mb / 1024).toFixed(2)} GB`
}

/** 签发一次性下载票据（默认 600s）。 */
export function issueTicket(artifactId: string, ttlSeconds = 600, signal?: AbortSignal) {
  return artifactRequest<{ ticket: string }>(`/${encodeURIComponent(artifactId)}/tickets`, {
    method: 'POST',
    body: JSON.stringify({ ttl_seconds: ttlSeconds }),
    signal,
  })
}

/**
 * 从 Content-Disposition 解析文件名：
 * 优先 RFC 5987 `filename*=UTF-8''%E5%AF%BC...`，回退 `filename="..."` / `filename=...`。
 * 解析不出时返回 null（调用方给合理默认）。
 */
function parseFilename(disposition: string | null): string | null {
  if (!disposition) return null
  const star = /filename\*\s*=\s*([^;]+)/i.exec(disposition)
  if (star) {
    let raw = star[1].trim().replace(/^["']|["']$/g, '')
    const m = /^[^']*'[^']*'(.*)$/.exec(raw) // charset'lang'value
    if (m) raw = m[1]
    try {
      const decoded = decodeURIComponent(raw)
      if (decoded) return decoded
    } catch {
      if (raw) return raw
    }
  }
  const plain = /filename\s*=\s*("([^"]*)"|([^;]+))/i.exec(disposition)
  if (plain) {
    const v = (plain[2] ?? plain[3] ?? '').trim()
    if (v) return v
  }
  return null
}

/** 下载票据换取文件字节（二进制端点，独立于 JSON helper）。 */
export async function downloadByTicket(ticket: string, signal?: AbortSignal): Promise<{ blob: Blob; filename: string }> {
  let res: Response
  try {
    res = await fetch(`${ARTIFACTS_API_BASE}/downloads/${encodeURIComponent(ticket)}`, {
      method: 'GET',
      headers: { ...authHeader() },
      signal,
    })
  } catch (e) {
    if ((e as Error)?.name === 'AbortError') throw e
    throw new PrepCenterApiError({ kind: 'network', status: null, code: null, detail: (e as Error)?.message })
  }
  if (!res.ok) {
    let code: number | null = null
    let detail: string | undefined
    let requestId: string | null = res.headers.get('x-request-id')
    try {
      const body = (await res.json()) as { code?: number; message?: string; detail?: string; request_id?: string }
      code = typeof body?.code === 'number' ? body.code : null
      detail = body?.message || body?.detail
      requestId = requestId ?? (body?.request_id ?? null)
    } catch {
      /* 非 JSON 错误体：仅用状态判定 */
    }
    throw new PrepCenterApiError({
      kind: kindOfStatus(res.status),
      status: res.status,
      code,
      detail: detail || `download failed: ${res.status}`,
      requestId,
    })
  }
  const blob = await res.blob()
  const filename = parseFilename(res.headers.get('content-disposition')) || 'export.bin'
  return { blob, filename }
}

// ── 编排：导出 → 轮询 → 签票 → 下载 ──────────────────────────────────────────

export interface RunExportOptions {
  kind: 'deck' | 'lesson_plan'
  /** deckId 或 spec_id。 */
  id: string
  format: ExportFormat
  /** 乐观并发基线（必须为最新 revision，否则后端 409）。 */
  expectedRevision: number
  signal?: AbortSignal
  /** 真实阶段回调（文案取自 job.progress.stage/status，非前端假进度）。 */
  onStage?: (label: string) => void
  /** 轮询间隔（默认 1500ms）。 */
  pollIntervalMs?: number
  /** 轮询总上限（默认 5 分钟）。 */
  timeoutMs?: number
}

export interface RunExportResult {
  blob: Blob
  filename: string
  artifactId: string
  jobId: string
}

/**
 * 内部阶段名 → 教师可读文案（内部英文枚举禁止上屏，见 VISUAL-SPEC / 反泄漏纪律）。
 * 阶段名来自作业 durable 事件 `detail.stage`（export_artifact_v2 真实发出）。
 */
const STAGE_COPY: Record<string, string> = {
  ingest: '正在准备教材素材',
  ingested: '素材已就绪',
  generate: '正在渲染课件页面',
  reused_engine_result: '复用已渲染页面',
  rendered: '页面渲染完成',
  qa: '正在做视觉质检',
  render: '正在排版文件',
  artifact: '正在登记导出文件',
}

/** 取最近一条 stage 事件的真实阶段名（无则 null）。 */
function latestStageName(job: Pick<ExportJobView, 'events'>): string | null {
  const evs = job.events || []
  for (let i = evs.length - 1; i >= 0; i--) {
    const e = evs[i]
    const stage = e?.type === 'stage' ? e.detail?.stage : null
    if (typeof stage === 'string' && stage) return stage
  }
  return null
}

/** 作业状态 + 真实阶段 → 教师可读文案（禁止英文状态/阶段名泄漏）。 */
export function exportStageLabel(job: Pick<ExportJobView, 'status' | 'progress' | 'events'>): string {
  if (job.status === 'done') return '导出完成'
  if (job.status === 'failed') return '导出失败'
  if (job.status === 'cancelled') return '导出已取消'
  if (job.status === 'pending') return '排队等待导出'
  const stage = latestStageName(job)
  return (stage && STAGE_COPY[stage]) || '正在导出…'
}

function abortError(): Error {
  const e = new Error('导出已取消')
  e.name = 'AbortError'
  return e
}

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(abortError())
      return
    }
    const onAbort = () => {
      clearTimeout(timer)
      reject(abortError())
    }
    const timer = setTimeout(() => {
      signal?.removeEventListener('abort', onAbort)
      resolve()
    }, ms)
    signal?.addEventListener('abort', onAbort, { once: true })
  })
}

/** 从作业视图挑选可下载产物 id（按目标格式优先）。 */
function pickArtifactId(job: ExportJobView, format: ExportFormat): string | null {
  if (format === 'pptx' && job.pptx_artifact_id) return job.pptx_artifact_id
  if (format === 'pdf' && job.pdf_artifact_id) return job.pdf_artifact_id
  return job.artifact_id ?? job.pptx_artifact_id ?? job.pdf_artifact_id ?? null
}

/**
 * 导出闭环编排：受理 → 轮询至终态（间隔 1.5s / 上限 5 分钟）→ 签票 → 下载字节。
 * 取消能力：传入 AbortSignal 即中止后续请求与等待（抛 AbortError）。
 * 失败诚实报错：job.error 或超时文案（不伪造成功）。
 */
export async function runExport(opts: RunExportOptions): Promise<RunExportResult> {
  const { kind, id, format, expectedRevision, signal, onStage } = opts
  const start =
    kind === 'deck'
      ? await startDeckExport(id, format as DeckExportFormat, expectedRevision, signal)
      : await startLessonPlanExport(id, format as LessonPlanExportFormat, expectedRevision, signal)
  const jobId = start.job_id
  onStage?.(exportStageLabel({ status: start.status, progress: null }))

  const interval = opts.pollIntervalMs ?? 1500
  const deadline = Date.now() + (opts.timeoutMs ?? 300_000)

  let job: ExportJobView | null = null
  for (;;) {
    if (signal?.aborted) throw abortError()
    job = await getExportJob(jobId, signal)
    onStage?.(exportStageLabel(job))
    if (job.status === 'done') break
    if (job.status === 'failed' || job.status === 'cancelled') {
      throw new PrepCenterApiError({
        kind: 'error',
        status: null,
        code: null,
        detail: job.error || (job.status === 'cancelled' ? '导出已取消' : '导出失败，请重试'),
      })
    }
    if (Date.now() >= deadline) {
      throw new PrepCenterApiError({ kind: 'error', status: null, code: null, detail: '导出超时，请稍后重试' })
    }
    await sleep(interval, signal)
  }

  const artifactId = pickArtifactId(job, format)
  if (!artifactId) {
    throw new PrepCenterApiError({ kind: 'error', status: null, code: null, detail: '导出完成但未返回产物，无法下载' })
  }
  const { ticket } = await issueTicket(artifactId, 600, signal)
  const { blob, filename } = await downloadByTicket(ticket, signal)
  return { blob, filename, artifactId, jobId }
}