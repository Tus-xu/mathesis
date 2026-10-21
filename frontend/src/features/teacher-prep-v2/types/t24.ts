/**
 * T24 · 结构化编辑 / 局部重生成 / 逐页 QA / 真实导出 —— **前端契约类型**。
 *
 * 唯一事实来源：`docs/compose/spec/t24-artifact-edit-export.md`（冻结契约 S2.3–S2.5）。
 *
 * 铁律：
 * - 本文件**只**放类型 + 防御性归一化（server payload → typed view）。不做业务推导、
 *   不补齐缺失字段、不用默认值伪造服务端事实（缺失一律 `null`）。
 * - 归一化函数是纯函数，因此"服务端形状变了"可以在单测里被立刻抓住。
 */

// ── 身份 ──────────────────────────────────────────────────────────────────

/** artifact 的两类 spec（deck 落 `ppt_v2_deck_specs`，lesson plan 落 `tp_lesson_plan_specs`）。 */
export type SpecKind = 'teaching_deck' | 'lesson_plan'

/** 局部重生成的粒度：deck 是页（slide），lesson plan 是块（block）。 */
export type RegenTargetKind = 'slide' | 'block'

// ── patch / proposal ──────────────────────────────────────────────────────

/**
 * 单个 patch 操作。
 *
 * - lesson plan：`{op:"set_block", target:"<block_id>", field, value}`（契约 S2.4 明示）。
 * - deck：`{op:"set_slide", target:"<slide_id>", field:"content"|"notes", value}`（与 8100 领域契约一致）。
 * - `before` 是**客户端**草稿携带的旧值，服务端据此做 stale 校验（不是服务端事实）。
 */
export interface PatchOperation {
  op: string
  target: string
  field: string
  value: unknown
  before?: unknown
}

export interface AffectedSet {
  slideIds: string[]
  blockIds: string[]
  count: number | null
}

export type ProposalStatus = 'proposed' | 'applied' | 'rejected' | 'superseded' | 'failed'

export interface PatchProposal {
  patchId: string
  status: ProposalStatus
  baseRevision: number | null
  expectedRevision: number | null
  affected: AffectedSet | null
  operations: PatchOperation[]
  summary: string | null
}

// ── 修订历史 ──────────────────────────────────────────────────────────────

export interface RevisionListItem {
  revision: number
  kind: string
  summary: string | null
  actorId: string | null
  createdAt: string | null
  affectedSlideIds: string[]
  specSha256: string | null
}

export interface RevisionsView {
  currentRevision: number | null
  revisions: RevisionListItem[]
}

export interface RevertResult {
  revision: number
  revertedTo: number | null
  affected: AffectedSet | null
}

// ── 409 冲突（contract S2.4 载荷）─────────────────────────────────────────

export interface ConflictField {
  target: string
  field: string
  /** 提交时（base）的值。 */
  before: string
  /** 服务端当前的值。 */
  after: string
}

export interface ConflictOperation {
  index: number
  status: 'conflict' | 'applied' | 'unknown'
  message: string | null
}

/** 服务端 409 `data` 归一化后的形状。 */
export interface RevisionConflict {
  kind: 'revision_conflict'
  expectedRevision: number
  currentRevision: number
  serverSpecSha256: string | null
  summaryZh: string | null
  fields: ConflictField[]
  slideIds: string[]
  operations: ConflictOperation[]
}

// ── 逐页 QA（contract S2.5）───────────────────────────────────────────────

export type QaDefectSeverity = 'p0' | 'p1'
export type QaPageStatus = 'pass' | 'fail' | 'unknown'

export interface QaDefect {
  kind: string
  severity: QaDefectSeverity
  message: string
  shapeIds: string[]
}

export interface QaMetrics {
  inkPct: number | null
  cv: number | null
  shapeCount: number | null
  hasPicture: boolean | null
}

export interface QaPageReport {
  pageNo: number
  status: QaPageStatus
  defects: QaDefect[]
  metrics: QaMetrics
  attempts: number | null
}

export interface QaReport {
  deckId: string | null
  specRevision: number | null
  artifactId: string | null
  checks: string[]
  pages: QaPageReport[]
  /** 以下四项**由服务端 summary 提供**；缺失时前端不猜（保持空/0）。 */
  failedPages: number[]
  p0Count: number
  p1Count: number
  byKind: Record<string, number>
}

export interface QaRetryResult {
  pageNo: number
  attempts: number | null
  status: QaPageStatus
  defects: QaDefect[]
  metrics: QaMetrics
  /** 契约要求服务端证明"只重跑了这一页"；false 视为契约违反（保留原值）。 */
  othersUnchanged: boolean
}

// ── 导出 / artifact / 下载票据 ────────────────────────────────────────────

export type ExportStatus = 'pending' | 'running' | 'done' | 'failed' | 'cancelled'

export interface ExportEvent {
  seq: number | null
  type: string
  ts: string | null
  detail: string | null
}

export interface ExportJob {
  jobId: string
  format: string
  status: ExportStatus
  taskId: string | null
  artifactId: string | null
  error: string | null
  progressStage: string | null
  progressPageNo: number | null
  progressPageCount: number | null
  events: ExportEvent[]
}

export interface ArtifactRecord {
  artifactId: string
  version: number | null
  kind: string
  mediaType: string | null
  sizeBytes: number | null
  contentSha256: string | null
  createdAt: string | null
  createdByRunId: string | null
  specKind: string | null
  specId: string | null
  specRevision: number | null
  sourceLineage: Record<string, unknown> | null
  qaStatus: Record<string, unknown> | null
  ticketRequired: boolean
}

export interface DownloadTicket {
  ticket: string
  /** 服务端给的绝对/相对 URL；服务端没给才由客户端按契约拼 `/downloads/{ticket}`。 */
  url: string | null
  expiresAt: string | null
  singleUse: boolean
}

// ── Spec 视图（deck / lesson plan 统一投影）──────────────────────────────

export interface DeckSlideSummary {
  id: string
  index: number
  title: string
  layoutHint: string | null
  qaStatus: QaPageStatus
}

export interface LessonBlockSummary {
  id: string
  kind: string | null
  title: string | null
  sourceLocator: string | null
}

export interface SpecView {
  kind: SpecKind
  /** 服务端权威标识：deck → `deck_id`；lesson plan → `spec_id`。 */
  remoteId: string
  specId: string | null
  revision: number
  schemaVersion: string | null
  specSha256: string | null
  metadata: Record<string, unknown> | null
  spec: Record<string, unknown> | null
  slideCount: number | null
  slides: DeckSlideSummary[]
  blocks: LessonBlockSummary[]
  lineage: Record<string, unknown> | null
  recentChangeLog: Array<Record<string, unknown>>
}

// ── 归一化工具（纯函数，可单测）──────────────────────────────────────────

export function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null
}

export function asString(value: unknown): string | null {
  return typeof value === 'string' && value.length > 0 ? value : null
}

export function asNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

export function asBoolean(value: unknown): boolean | null {
  return typeof value === 'boolean' ? value : null
}

export function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : []
}

export function asStringArray(value: unknown): string[] {
  return asArray(value).filter((v): v is string => typeof v === 'string')
}

function asPageStatus(value: unknown): QaPageStatus {
  return value === 'pass' || value === 'fail' || value === 'unknown' ? value : 'unknown'
}

function asAffected(raw: unknown): AffectedSet | null {
  const rec = asRecord(raw)
  if (!rec) return null
  return {
    slideIds: asStringArray(rec.slide_ids ?? rec.slideIds),
    blockIds: asStringArray(rec.block_ids ?? rec.blockIds),
    count: asNumber(rec.count),
  }
}

function asDefects(raw: unknown): QaDefect[] {
  return asArray(raw).flatMap((item) => {
    const rec = asRecord(item)
    if (!rec) return []
    const kind = asString(rec.kind)
    const message = asString(rec.message)
    if (!kind || !message) return []
    const severity: QaDefectSeverity = rec.severity === 'p1' ? 'p1' : 'p0'
    return [{ kind, severity, message, shapeIds: asStringArray(rec.shape_ids) }]
  })
}

function asMetrics(raw: unknown): QaMetrics {
  const rec = asRecord(raw) ?? {}
  return {
    inkPct: asNumber(rec.ink_pct),
    cv: asNumber(rec.cv),
    shapeCount: asNumber(rec.shape_count),
    hasPicture: asBoolean(rec.has_picture),
  }
}

/** `GET /decks/{id}/qa` → typed。缺字段一律 null/空，不伪造。 */
export function normalizeQaReport(raw: unknown): QaReport | null {
  const rec = asRecord(raw)
  if (!rec) return null
  const summary = asRecord(rec.summary) ?? {}
  const byKindRaw = asRecord(summary.by_kind) ?? {}
  const byKind: Record<string, number> = {}
  for (const [key, value] of Object.entries(byKindRaw)) {
    const n = asNumber(value)
    if (n !== null) byKind[key] = n
  }
  return {
    deckId: asString(rec.deck_id),
    specRevision: asNumber(rec.spec_revision),
    artifactId: asString(rec.artifact_id),
    checks: asStringArray(rec.checks),
    pages: asArray(rec.pages).flatMap((item) => {
      const page = asRecord(item)
      const pageNo = page ? asNumber(page.page_no) : null
      if (!page || pageNo === null) return []
      return [{
        pageNo,
        status: asPageStatus(page.status),
        defects: asDefects(page.defects),
        metrics: asMetrics(page.metrics),
        attempts: asNumber(page.attempts),
      }]
    }),
    failedPages: asArray(summary.failed_pages)
      .map((v) => asNumber(v))
      .filter((v): v is number => v !== null),
    p0Count: asNumber(summary.p0_count) ?? 0,
    p1Count: asNumber(summary.p1_count) ?? 0,
    byKind,
  }
}

/** `POST /decks/{id}/qa/retry` → typed。 */
export function normalizeQaRetry(raw: unknown): QaRetryResult | null {
  const rec = asRecord(raw)
  if (!rec) return null
  const pageNo = asNumber(rec.page_no)
  if (pageNo === null) return null
  return {
    pageNo,
    attempts: asNumber(rec.attempts),
    status: asPageStatus(rec.status),
    defects: asDefects(rec.defects),
    metrics: asMetrics(rec.metrics),
    othersUnchanged: asBoolean(rec.others_unchanged) === true,
  }
}

/**
 * 单页重试后**只**替换该页；summary 若服务端未重算则由前端从 pages 重算
 * （纯派生，不是"发明新事实"：它只是同一组页面事实的计数）。
 */
export function mergeQaRetry(report: QaReport, retry: QaRetryResult): QaReport {
  const pages = report.pages.map((page) =>
    page.pageNo === retry.pageNo
      ? { ...page, status: retry.status, defects: retry.defects, metrics: retry.metrics, attempts: retry.attempts ?? page.attempts }
      : page,
  )
  return recomputeQaSummary({ ...report, pages })
}

/** 从 pages 重算 summary（页集合不变时 summary 是它的纯函数）。 */
export function recomputeQaSummary(report: QaReport): QaReport {
  const failedPages = report.pages.filter((p) => p.status === 'fail').map((p) => p.pageNo)
  let p0Count = 0
  let p1Count = 0
  const byKind: Record<string, number> = {}
  for (const page of report.pages) {
    for (const defect of page.defects) {
      if (defect.severity === 'p1') p1Count += 1
      else p0Count += 1
      byKind[defect.kind] = (byKind[defect.kind] ?? 0) + 1
    }
  }
  return { ...report, failedPages, p0Count, p1Count, byKind }
}

/** 409 `data` → typed conflict；shape 不对返回 null（调用方保留原始错误）。 */
export function normalizeRevisionConflict(raw: unknown): RevisionConflict | null {
  const rec = asRecord(raw)
  if (!rec) return null
  const expected = asNumber(rec.expected_revision)
  const current = asNumber(rec.current_revision)
  if (expected === null || current === null) return null
  const diff = asRecord(rec.diff) ?? {}
  return {
    kind: 'revision_conflict',
    expectedRevision: expected,
    currentRevision: current,
    serverSpecSha256: asString(rec.server_spec_sha256),
    summaryZh: asString(diff.summary_zh),
    fields: asArray(diff.fields).flatMap((item) => {
      const field = asRecord(item)
      if (!field) return []
      const target = asString(field.target)
      const name = asString(field.field)
      if (!target || !name) return []
      return [{
        target,
        field: name,
        before: typeof field.base_value === 'string' ? field.base_value : '',
        after: typeof field.server_value === 'string' ? field.server_value : '',
      }]
    }),
    slideIds: asStringArray(diff.slide_ids),
    operations: asArray(rec.operations).flatMap((item, fallbackIndex) => {
      const op = asRecord(item)
      if (!op) return []
      const index = asNumber(op.index) ?? fallbackIndex
      const status = op.status === 'conflict' || op.status === 'applied' ? op.status : 'unknown'
      return [{ index, status, message: asString(op.message) }]
    }),
  }
}

/** `GET /exports/{job_id}` → typed。 */
export function normalizeExportJob(raw: unknown, fallbackFormat?: string | null): ExportJob | null {
  const rec = asRecord(raw)
  if (!rec) return null
  const jobId = asString(rec.job_id)
  if (!jobId) return null
  const progress = asRecord(rec.progress) ?? {}
  const status = rec.status
  const normalized: ExportStatus =
    status === 'pending' || status === 'running' || status === 'done' || status === 'failed' || status === 'cancelled'
      ? status
      : 'pending'
  return {
    jobId,
    format: asString(rec.format) ?? fallbackFormat ?? '',
    status: normalized,
    taskId: asString(rec.task_id),
    artifactId: asString(rec.artifact_id),
    error: asString(rec.error),
    progressStage: asString(progress.stage),
    progressPageNo: asNumber(progress.page_no),
    progressPageCount: asNumber(progress.page_count),
    events: asArray(rec.events).flatMap((item) => {
      const event = asRecord(item)
      if (!event) return []
      return [{
        seq: asNumber(event.seq),
        type: asString(event.type) ?? 'unknown',
        ts: asString(event.ts),
        detail: asString(event.detail),
      }]
    }),
  }
}

/** `POST /{artifact_id}/tickets` → typed。 */
export function normalizeTicket(raw: unknown): DownloadTicket | null {
  const rec = asRecord(raw)
  if (!rec) return null
  const ticket = asString(rec.ticket)
  if (!ticket) return null
  return {
    ticket,
    url: asString(rec.url),
    expiresAt: asString(rec.expires_at),
    singleUse: asBoolean(rec.single_use) !== false,
  }
}

/** `GET /{artifact_id}` → typed（INV-8 血缘齐全的读取入口）。 */
export function normalizeArtifactRecord(raw: unknown): ArtifactRecord | null {
  const rec = asRecord(raw)
  if (!rec) return null
  const artifactId = asString(rec.artifact_id)
  if (!artifactId) return null
  const download = asRecord(rec.download)
  return {
    artifactId,
    version: asNumber(rec.version),
    kind: asString(rec.kind) ?? 'unknown',
    mediaType: asString(rec.media_type),
    sizeBytes: asNumber(rec.size_bytes),
    contentSha256: asString(rec.content_sha256),
    createdAt: asString(rec.created_at),
    createdByRunId: asString(rec.created_by_run_id),
    specKind: asString(rec.spec_kind),
    specId: asString(rec.spec_id),
    specRevision: asNumber(rec.spec_revision),
    sourceLineage: asRecord(rec.source_lineage),
    qaStatus: asRecord(rec.qa_status),
    ticketRequired: !download || asBoolean(download.ticket_required) !== false,
  }
}

/** `POST .../patches` → typed proposal。 */
export function normalizePatchProposal(raw: unknown): PatchProposal | null {
  const rec = asRecord(raw)
  if (!rec) return null
  const patchId = asString(rec.patch_id) ?? asString(rec.id)
  if (!patchId) return null
  const status = rec.status
  return {
    patchId,
    status:
      status === 'proposed' || status === 'applied' || status === 'rejected' || status === 'superseded' || status === 'failed'
        ? status
        : 'proposed',
    baseRevision: asNumber(rec.base_revision),
    expectedRevision: asNumber(rec.expected_revision),
    affected: asAffected(rec.affected),
    operations: normalizeOperations(rec.operations),
    summary: asString(rec.summary),
  }
}

/** operations 归一化：丢掉没有 target/field 的项（无效操作不冒充有效）。 */
export function normalizeOperations(raw: unknown): PatchOperation[] {
  return asArray(raw).flatMap((item) => {
    const rec = asRecord(item)
    if (!rec) return []
    const op = asString(rec.op)
    const target = asString(rec.target)
    const field = asString(rec.field)
    if (!op || !target || !field) return []
    const out: PatchOperation = { op, target, field, value: rec.value }
    if ('before' in rec) out.before = rec.before
    return [out]
  })
}

/** `GET .../revisions` → typed。 */
export function normalizeRevisions(raw: unknown): RevisionsView {
  const rec = asRecord(raw)
  if (!rec) return { currentRevision: null, revisions: [] }
  return {
    currentRevision: asNumber(rec.current_revision),
    revisions: asArray(rec.revisions).flatMap((item) => {
      const rev = asRecord(item)
      const revision = rev ? asNumber(rev.revision) : null
      if (!rev || revision === null) return []
      return [{
        revision,
        kind: asString(rev.kind) ?? 'unknown',
        summary: asString(rev.summary),
        actorId: asString(rev.actor_id),
        createdAt: asString(rev.created_at),
        affectedSlideIds: asStringArray(rev.affected_slide_ids),
        specSha256: asString(rev.spec_sha256),
      }]
    }),
  }
}

/**
 * `GET /decks/{id}` / `GET /lesson-plans/{id}` → 统一 `SpecView`。
 * `remoteId` 取服务端回给的主键（deck_id / spec_id），**不**信任调用方传入的值。
 */
export function normalizeSpecView(raw: unknown, kind: SpecKind, fallbackRemoteId: string): SpecView | null {
  const rec = asRecord(raw)
  if (!rec) return null
  const remoteId =
    (kind === 'teaching_deck' ? asString(rec.deck_id) : asString(rec.spec_id)) ??
    asString(rec.deck_id) ?? asString(rec.spec_id) ?? fallbackRemoteId
  const revision = asNumber(rec.revision)
  if (revision === null) return null
  return {
    kind,
    remoteId,
    specId: asString(rec.spec_id),
    revision,
    schemaVersion: asString(rec.schema_version),
    specSha256: asString(rec.spec_sha256),
    metadata: asRecord(rec.metadata),
    spec: asRecord(rec.spec),
    slideCount: asNumber(rec.slide_count),
    slides: asArray(rec.slides).flatMap((item, index) => {
      const slide = asRecord(item)
      if (!slide) return []
      const id = asString(slide.id)
      if (!id) return []
      return [{
        id,
        index: asNumber(slide.index) ?? index,
        title: asString(slide.title) ?? '',
        layoutHint: asString(slide.layout_hint),
        qaStatus: asPageStatus(slide.qa_status),
      }]
    }),
    blocks: asArray(rec.blocks).flatMap((item) => {
      const block = asRecord(item)
      if (!block) return []
      const id = asString(block.id)
      if (!id) return []
      return [{
        id,
        kind: asString(block.kind),
        title: asString(block.title),
        sourceLocator: asString(block.source_locator),
      }]
    }),
    lineage: asRecord(rec.lineage),
    recentChangeLog: asArray(rec.recent_change_log)
      .map((entry) => asRecord(entry))
      .filter((entry): entry is Record<string, unknown> => entry !== null),
  }
}
