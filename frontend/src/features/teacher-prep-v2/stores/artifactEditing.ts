/**
 * T24 · 产物编辑 store —— **服务端事实 / 客户端草稿 / 冲突恢复 / 导出作业** 四层边界。
 *
 * ┌─ SERVER FACTS（权威，全部来自 8100 响应）──────────────────────────────┐
 * │ spec 视图（revision/spec_sha256/slides/blocks/lineage）、revision 列表、  │
 * │ proposal、逐页 QA、导出作业、artifact、下载票据。                       │
 * │ 本 store **绝不**自行推导 revision、**绝不**本地数组冒充版本历史。      │
 * └───────────────────────────────────────────────────────────────────────┘
 * ┌─ CLIENT DRAFT（可丢弃，仅内存）────────────────────────────────────────┐
 * │ 未提交的 `draftOperations`；被 409 拒绝的 `rejectedOperations`。        │
 * │ **不落 localStorage**：服务端事实与可提交意图都不允许被本地缓存冒充。  │
 * └───────────────────────────────────────────────────────────────────────┘
 * ┌─ CONFLICT（INV-1 的客户端一半）────────────────────────────────────────┐
 * │ `conflict` 保存服务端给的 {expected/current/diff/operations}；          │
 * │ 冲突未解决前 `apply` 直接拒绝 —— **禁止 last-write-wins 静默覆盖**。     │
 * │ 三个明确出口：reload() / reapply() / discard()。                        │
 * └───────────────────────────────────────────────────────────────────────┘
 * ┌─ EXPORT JOBS（真实状态机，无伪造进度）─────────────────────────────────┐
 * │ 创建 → 轮询 `GET /exports/{id}` → done → 领取**短时**下载票据；          │
 * │ 票据按 `expires_at` 自然过期（或 redeem 410/403）→ state='expired'。     │
 * └───────────────────────────────────────────────────────────────────────┘
 *
 * 铁律（与后端契约 S2.3 对齐）：
 * - INV-1：每个写入口都带 `expected_revision`；409 在客户端表现为
 *   "写请求被拒绝且**没有任何本地乐观更新**"。
 * - INV-2：patch/regen 的局部性由服务端保证，客户端**不做**乐观局部替换，
 *   `apply` 成功后一律回拉服务端视图。
 * - INV-5：撤销 = `revert` 生成**新** revision，然后回拉历史；不做本地数组撤销。
 */
import { computed, ref, shallowRef } from 'vue'
import { defineStore } from 'pinia'

import {
  PrepApiError,
  applyDeckPatch,
  applyLessonPlanPatch,
  cancelExportJob,
  createDownloadTicket,
  downloadUrlOf,
  getDeck,
  getDeckQa,
  getExportJob,
  getLessonPlan,
  listDeckRevisions,
  listLessonPlanRevisions,
  normalizePrepError,
  proposeDeckPatch,
  proposeLessonPlanPatch,
  regenDeckTarget,
  regenLessonPlanTarget,
  rejectDeckPatch,
  rejectLessonPlanPatch,
  requestDeckExport,
  requestLessonPlanExport,
  retryPageQa,
  revertDeckRevision,
  revertLessonPlanRevision,
} from '@/features/teacher-prep-v2/api/prepApi'
import {
  mergeQaRetry,
  normalizeExportJob,
  normalizeOperations,
  normalizePatchProposal,
  normalizeQaReport,
  normalizeQaRetry,
  normalizeRevisionConflict,
  normalizeRevisions,
  normalizeSpecView,
  normalizeTicket,
} from '@/features/teacher-prep-v2/types/t24'
import type {
  AffectedSet,
  DownloadTicket,
  ExportJob,
  ExportStatus,
  PatchOperation,
  PatchProposal,
  QaReport,
  RegenTargetKind,
  RevisionConflict,
  RevisionListItem,
  SpecKind,
  SpecView,
} from '@/features/teacher-prep-v2/types/t24'
import type { NormalizedError } from '@/features/teacher-prep-v2/types/workspace'

/** proposal 的可选元信息（summary 进 revision 记录；source 标记 human/ai/regen）。 */
export interface ProposeOptions {
  summary?: string
  source?: 'human' | 'ai' | 'regen'
}

/** 导出作业轮询间隔（真实 interval 轮询；组件卸载/取消时停）。 */
export const DEFAULT_EXPORT_POLL_MS = 1500

/** 冲突状态（服务端 409 载荷 + 一个客户端可观测事实：是否已重新拉过服务端）。 */
export interface ConflictState extends RevisionConflict {
  /** `reload()` 之后才为 true —— `reapply()` 依赖它保证"先拿新 revision 再重提"。 */
  reloadedAfterConflict: boolean
  /** 冲突发生时用户提交的版本（历史事实，不随 reload 改变）。 */
  submittedRevision: number
}

export interface DownloadTicketState {
  url: string | null
  expiresAt: string | null
  singleUse: boolean
  expired: boolean
  /** redeemer 反馈的 410/403（浏览器导航无法观测，需 API 侧回传）。 */
  redeemFailure: number | null
}

/** 导出面板需要的**真实**状态：idle / pending / running / done / failed / cancelled / expired。 */
export interface ExportEntry {
  format: string
  jobId: string | null
  status: 'idle' | ExportStatus | 'expired'
  artifactId: string | null
  error: string | null
  progressStage: string | null
  progressPageNo: number | null
  progressPageCount: number | null
  events: ExportJob['events']
  ticket: DownloadTicketState | null
}

export interface RegenRecord {
  patchId: string
  targetKind: RegenTargetKind
  targetId: string
  changedIds: string[]
  providerModel: string | null
  providerRunId: string | null
}

/** 操作名：与 8100 领域 patch 契约一致。 */
export const OP_SET_BLOCK = 'set_block'
export const OP_SET_SLIDE = 'set_slide'

/**
 * 本地入参校验（无效入参必须在**发出请求之前**被拒绝，且不得改动任何状态）。
 * 返回 null = 通过；返回错误码 = 拒绝。
 */
export function validatePatchOperations(operations: unknown): string | null {
  if (!Array.isArray(operations) || operations.length === 0) return 'prep.t24.error.patch_empty'
  for (const op of operations) {
    if (!op || typeof op !== 'object') return 'prep.t24.error.patch_op_invalid'
    const rec = op as Record<string, unknown>
    if (typeof rec.op !== 'string' || !rec.op) return 'prep.t24.error.patch_op_invalid'
    if (typeof rec.target !== 'string' || !rec.target) return 'prep.t24.error.patch_op_invalid'
    if (typeof rec.field !== 'string' || !rec.field) return 'prep.t24.error.patch_op_invalid'
    if (!('value' in rec)) return 'prep.t24.error.patch_op_invalid'
  }
  return null
}

/**
 * 从**服务端 lineage** 组装 regen refs（INV-3：只引用 blueprint，不改 blueprint）。
 * 服务端没给的一律为 null —— 不猜 id、不编 revision。
 */
export function buildRegenRefs(lineage: Record<string, unknown> | null): Record<string, unknown> {
  const l = lineage ?? {}
  const str = (v: unknown) => (typeof v === 'string' && v ? v : null)
  const num = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) ? v : null)
  return {
    approved_blueprint_id: str(l.approved_blueprint_id) ?? str(l.blueprint_id),
    blueprint_revision: num(l.blueprint_revision) ?? num(l.approved_blueprint_revision),
    spec_revision: num(l.spec_revision) ?? num(l.revision),
    prompt_ref: (l.prompt_ref && typeof l.prompt_ref === 'object' ? l.prompt_ref : null),
    context_manifest_id: str(l.context_manifest_id),
    evidence_refs: Array.isArray(l.evidence_refs) ? l.evidence_refs : [],
    source_refs: Array.isArray(l.source_refs) ? l.source_refs : [],
  }
}

/** 票据是否已过期（无 expires_at 视为未过期：服务端没给到期时间就不臆断）。 */
export function isTicketExpired(ticket: DownloadTicketState | null, now: number = Date.now()): boolean {
  if (!ticket) return false
  if (ticket.expired) return true
  if (!ticket.expiresAt) return false
  const at = Date.parse(ticket.expiresAt)
  if (Number.isNaN(at)) return false
  return now >= at
}

function idempotencyKey(scope: string): string {
  return `fe-t24-${scope}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`
}

function ticketStateFrom(ticket: DownloadTicket): DownloadTicketState {
  return {
    url: downloadUrlOf(ticket),
    expiresAt: ticket.expiresAt,
    singleUse: ticket.singleUse,
    expired: false,
    redeemFailure: null,
  }
}

function emptyEntry(format: string): ExportEntry {
  return {
    format,
    jobId: null,
    status: 'idle',
    artifactId: null,
    error: null,
    progressStage: null,
    progressPageNo: null,
    progressPageCount: null,
    events: [],
    ticket: null,
  }
}

export const useArtifactEditingStore = defineStore('prepArtifactEditingV24', () => {
  // ── SERVER FACTS ────────────────────────────────────────────────────────
  const specKind = ref<SpecKind | null>(null)
  const remoteId = ref<string | null>(null)
  const view = shallowRef<SpecView | null>(null)
  const revisions = ref<RevisionListItem[]>([])
  const currentRevision = ref<number | null>(null)
  const pendingProposal = shallowRef<PatchProposal | null>(null)
  const qa = shallowRef<QaReport | null>(null)
  const exports = ref<Record<string, ExportEntry>>({})
  const lastRegen = shallowRef<RegenRecord | null>(null)
  const lastApplied = shallowRef<{ revision: number; patchId: string; affected: AffectedSet | null } | null>(null)

  // ── CLIENT DRAFT / CONFLICT ─────────────────────────────────────────────
  const draftOperations = ref<PatchOperation[]>([])
  const rejectedOperations = ref<PatchOperation[]>([])
  const conflict = shallowRef<ConflictState | null>(null)

  // ── 元状态 ──────────────────────────────────────────────────────────────
  const busy = ref(false)
  const error = ref<NormalizedError | null>(null)
  const localErrorKey = ref<string | null>(null)
  const pollIntervalMs = ref(DEFAULT_EXPORT_POLL_MS)

  let pollTimers = new Map<string, ReturnType<typeof setTimeout>>()
  const ticketFormats = new Map<string, string>()
  let ticketTimers = new Map<string, ReturnType<typeof setTimeout>>()

  // ── 派生（全部来自服务端事实）──────────────────────────────────────────
  const revision = computed(() => currentRevision.value)
  const slides = computed(() => view.value?.slides ?? [])
  const blocks = computed(() => view.value?.blocks ?? [])
  const hasPendingProposal = computed(() => pendingProposal.value !== null)
  const dirty = computed(() => draftOperations.value.length > 0)
  /** 冲突未解决前禁止 apply（INV-1 客户端一半）。 */
  const canApply = computed(
    () => pendingProposal.value !== null && conflict.value === null && !busy.value,
  )
  const exportEntries = computed(() => Object.values(exports.value))

  /** 每页 QA 状态（服务端 `qa.pages` 优先，回落到 deck 视图的 `slides[].qa_status`）。 */
  const qaStatusByPageNo = computed<Record<number, string>>(() => {
    const out: Record<number, string> = {}
    for (const page of qa.value?.pages ?? []) out[page.pageNo] = page.status
    return out
  })
  const failedPageNos = computed(() => qa.value?.failedPages ?? [])

  // ── 错误处理 ────────────────────────────────────────────────────────────
  function normalizeError(e: unknown): NormalizedError {
    return e instanceof PrepApiError
      ? e.info
      : normalizePrepError({ status: null, detail: e instanceof Error ? e.message : String(e) })
  }

  /** 409：只记冲突，**不触碰**任何 server fact / 本地乐观值。 */
  function captureConflict(e: PrepApiError, submittedRevision: number, fallbackOps: PatchOperation[]): boolean {
    const parsed = normalizeRevisionConflict(e.conflictPayload)
    if (!parsed) {
      error.value = e.info
      return false
    }
    conflict.value = {
      ...parsed,
      submittedRevision,
      reloadedAfterConflict: false,
    }
    // 被拒绝的操作是**客户端提交意图**（不是服务端事实）：服务端在 409 时无副作用，
    // 因此重试所需的 operations 只能由客户端自己的草稿还原。
    rejectedOperations.value = fallbackOps.length ? fallbackOps : rejectedOperations.value
    draftOperations.value = [...rejectedOperations.value]
    error.value = null
    localErrorKey.value = null
    return true
  }

  function handleError(e: unknown): void {
    error.value = normalizeError(e)
  }

  // ── 读 ─────────────────────────────────────────────────────────────────
  async function loadRevisions() {
    if (!specKind.value || !remoteId.value) return
    const raw =
      specKind.value === 'teaching_deck'
        ? await listDeckRevisions(remoteId.value)
        : await listLessonPlanRevisions(remoteId.value)
    const parsed = normalizeRevisions(raw)
    revisions.value = parsed.revisions
    // revision 以服务端为准（快照里的 current_revision 是同一事实的另一处表述）
    if (parsed.currentRevision !== null && view.value === null) {
      currentRevision.value = parsed.currentRevision
    }
  }

  async function loadQa() {
    if (specKind.value !== 'teaching_deck' || !remoteId.value) return
    const raw = await getDeckQa(remoteId.value)
    qa.value = normalizeQaReport(raw)
  }

  /** 回拉权威视图 + 历史（reload / conflict-reload / apply 后对账共用同一条通道）。 */
  async function reload(): Promise<SpecView | null> {
    if (!specKind.value || !remoteId.value) return null
    const kind = specKind.value
    const id = remoteId.value
    busy.value = true
    try {
      const raw = kind === 'teaching_deck' ? await getDeck(id) : await getLessonPlan(id)
      const parsed = normalizeSpecView(raw, kind, id)
      view.value = parsed
      if (parsed) currentRevision.value = parsed.revision
      await loadRevisions()
      if (kind === 'teaching_deck') await loadQa()
      error.value = null
      // 冲突横幅保留（用户还需要显式决定 reapply/discard），但标记"已看过新 revision"。
      if (conflict.value) {
        conflict.value = {
          ...conflict.value,
          reloadedAfterConflict: true,
          currentRevision: currentRevision.value ?? conflict.value.currentRevision,
        }
      }
      return parsed
    } catch (e) {
      handleError(e)
      return null
    } finally {
      busy.value = false
    }
  }

  /** 绑定一个 artifact；身份变化时清空**全部**既有事实（不跨 artifact 复用）。 */
  async function attach(input: {
    kind: SpecKind
    remoteId: string
    /** 快照里的 revision：仅用于在服务端返回前显示"正在加载 vN"，不作为写入口的 expected。 */
    revisionHint?: number | null
  }) {
    stopAllPolling()
    specKind.value = input.kind
    remoteId.value = input.remoteId
    view.value = null
    revisions.value = []
    currentRevision.value = null
    pendingProposal.value = null
    qa.value = null
    exports.value = {}
    draftOperations.value = []
    rejectedOperations.value = []
    conflict.value = null
    lastRegen.value = null
    lastApplied.value = null
    error.value = null
    localErrorKey.value = null
    if (typeof input.revisionHint === 'number') currentRevision.value = input.revisionHint
    await reload()
  }

  function detach() {
    stopAllPolling()
    specKind.value = null
    remoteId.value = null
    view.value = null
    revisions.value = []
    currentRevision.value = null
    pendingProposal.value = null
    qa.value = null
    exports.value = {}
    draftOperations.value = []
    rejectedOperations.value = []
    conflict.value = null
    lastRegen.value = null
    lastApplied.value = null
    error.value = null
    localErrorKey.value = null
  }

  // ── 草稿 ───────────────────────────────────────────────────────────────
  function setDraftOperations(operations: PatchOperation[]) {
    draftOperations.value = normalizeOperations(operations)
  }
  function clearDraft() {
    draftOperations.value = []
  }

  // ── proposal 生命周期（INV-4：先提案后落库）───────────────────────────
  async function proposePatch(
    operations: PatchOperation[],
    opts: ProposeOptions = {},
  ): Promise<PatchProposal | null> {
    // 先校验**原始**入参：形状不对要报"内容不完整"，而不是被归一化静默丢弃后
    // 报"没有可提交的修改"（诊断信息必须准确）。
    const invalid = validatePatchOperations(operations)
    if (invalid) {
      // 无效入参：不发请求、不改状态（除错误提示）
      localErrorKey.value = invalid
      return null
    }
    const ops = normalizeOperations(operations)
    if (!specKind.value || !remoteId.value) {
      localErrorKey.value = 'prep.t24.error.no_artifact'
      return null
    }
    const revisionAtSubmit = currentRevision.value
    if (revisionAtSubmit === null) {
      localErrorKey.value = 'prep.t24.error.no_revision'
      return null
    }
    const body = {
      expected_revision: revisionAtSubmit,
      summary: opts.summary ?? '',
      source: opts.source ?? 'human',
      idempotency_key: idempotencyKey('patch'),
      operations: ops,
    }
    busy.value = true
    try {
      const raw =
        specKind.value === 'teaching_deck'
          ? await proposeDeckPatch(remoteId.value, body)
          : await proposeLessonPlanPatch(remoteId.value, body)
      const proposal = normalizePatchProposal(raw)
      if (!proposal) {
        localErrorKey.value = 'prep.t24.error.proposal_unparsable'
        return null
      }
      pendingProposal.value = proposal.operations.length ? proposal : { ...proposal, operations: ops }
      draftOperations.value = []
      error.value = null
      localErrorKey.value = null
      return pendingProposal.value
    } catch (e) {
      if (e instanceof PrepApiError && e.info.kind === 'conflict') {
        captureConflict(e, revisionAtSubmit, ops)
        return null
      }
      handleError(e)
      return null
    } finally {
      busy.value = false
    }
  }

  /**
   * 应用提案。
   * **禁止 last-write-wins**：存在未解决冲突时直接拒绝（不发请求、不覆盖服务端）。
   */
  async function applyPatch(patchIdOverride?: string): Promise<{ revision: number } | null> {
    if (conflict.value) {
      localErrorKey.value = 'prep.t24.error.conflict_unresolved'
      return null
    }
    const patchId = patchIdOverride ?? pendingProposal.value?.patchId
    if (!patchId || !specKind.value || !remoteId.value || currentRevision.value === null) {
      localErrorKey.value = 'prep.t24.error.no_proposal'
      return null
    }
    const body = { expected_revision: currentRevision.value }
    busy.value = true
    try {
      const raw =
        specKind.value === 'teaching_deck'
          ? await applyDeckPatch(remoteId.value, patchId, body)
          : await applyLessonPlanPatch(remoteId.value, patchId, body)
      const rec = raw as Record<string, unknown>
      const nextRevision = typeof rec.revision === 'number' ? rec.revision : null
      if (nextRevision === null) {
        localErrorKey.value = 'prep.t24.error.apply_unparsable'
        return null
      }
      currentRevision.value = nextRevision
      lastApplied.value = {
        revision: nextRevision,
        patchId,
        affected: normalizeAffected(rec.affected),
      }
      pendingProposal.value = null
      draftOperations.value = []
      rejectedOperations.value = []
      // INV-2：局部性由服务端保证；客户端不猜内容，一律回拉权威视图。
      await reload()
      return { revision: nextRevision }
    } catch (e) {
      if (e instanceof PrepApiError && e.info.kind === 'conflict') {
        captureConflict(e, body.expected_revision, pendingProposal.value?.operations ?? [])
        return null
      }
      handleError(e)
      return null
    } finally {
      busy.value = false
    }
  }

  async function rejectPatch(reason?: string, patchIdOverride?: string): Promise<boolean> {
    const patchId = patchIdOverride ?? pendingProposal.value?.patchId
    if (!patchId || !specKind.value || !remoteId.value) {
      localErrorKey.value = 'prep.t24.error.no_proposal'
      return false
    }
    busy.value = true
    try {
      if (specKind.value === 'teaching_deck') {
        await rejectDeckPatch(remoteId.value, patchId, { reason: reason ?? null })
      } else {
        await rejectLessonPlanPatch(remoteId.value, patchId, { reason: reason ?? null })
      }
      pendingProposal.value = null
      draftOperations.value = []
      return true
    } catch (e) {
      handleError(e)
      return false
    } finally {
      busy.value = false
    }
  }

  // ── 冲突三动作 ─────────────────────────────────────────────────────────
  /** 动作 1：重新加载服务端事实（不重提、不丢弃）。 */
  async function reloadAfterConflict() {
    return reload()
  }

  /**
   * 动作 2：把被拒绝的操作按**重启后的服务端 revision** 重提一遍。
   * 若还没 reload 过，先 reload —— "冲突后必须重新拉服务端 revision 再允许重提"。
   */
  async function reapply() {
    if (!conflict.value) return null
    const ops = rejectedOperations.value.length
      ? [...rejectedOperations.value]
      : pendingProposal.value?.operations ?? []
    if (!conflict.value.reloadedAfterConflict) await reload()
    if (currentRevision.value === null) return null
    if (!ops.length) {
      localErrorKey.value = 'prep.t24.error.patch_empty'
      return null
    }
    const proposal = await proposePatch(ops, { source: 'human' })
    // 重提成功 → 冲突已消化；仍冲突 → captureConflict 已刷新 conflict。
    if (proposal) {
      conflict.value = null
      rejectedOperations.value = []
    }
    return proposal
  }

  /** 动作 3：放弃本地草稿与冲突上下文（**不发**任何写请求）。 */
  function discard() {
    conflict.value = null
    draftOperations.value = []
    rejectedOperations.value = []
    pendingProposal.value = null
    localErrorKey.value = null
  }

  // ── 局部重生成（INV-2 / INV-3）────────────────────────────────────────
  async function regenTarget(input: {
    kind: RegenTargetKind
    targetId: string
    instruction: string
    refs?: Record<string, unknown>
  }): Promise<RegenRecord | null> {
    if (!specKind.value || !remoteId.value || currentRevision.value === null) {
      localErrorKey.value = 'prep.t24.error.no_artifact'
      return null
    }
    if (!input.instruction.trim()) {
      localErrorKey.value = 'prep.t24.error.regen_instruction_empty'
      return null
    }
    const revisionAtSubmit = currentRevision.value
    const refs = input.refs ?? buildRegenRefs(view.value?.lineage ?? null)
    busy.value = true
    try {
      let raw: Record<string, unknown>
      if (specKind.value === 'teaching_deck') {
        if (input.kind !== 'slide') {
          localErrorKey.value = 'prep.t24.error.regen_target_kind'
          return null
        }
        raw = await regenDeckTarget(remoteId.value, {
          expected_revision: revisionAtSubmit,
          target: { kind: 'slide', slide_id: input.targetId },
          instruction: input.instruction,
          refs,
        })
      } else {
        if (input.kind !== 'block') {
          localErrorKey.value = 'prep.t24.error.regen_target_kind'
          return null
        }
        raw = await regenLessonPlanTarget(remoteId.value, {
          expected_revision: revisionAtSubmit,
          target: { kind: 'block', block_id: input.targetId },
          instruction: input.instruction,
          refs,
        })
      }
      const patchId = typeof raw.patch_id === 'string' ? raw.patch_id : null
      if (!patchId) {
        localErrorKey.value = 'prep.t24.error.proposal_unparsable'
        return null
      }
      const proposal = normalizePatchProposal(raw.proposal) ?? {
        patchId,
        status: 'proposed' as const,
        baseRevision: revisionAtSubmit,
        expectedRevision: revisionAtSubmit,
        affected: null,
        operations: [],
        summary: null,
      }
      pendingProposal.value = proposal
      const changedIds = Array.isArray(raw.changed_slide_ids)
        ? (raw.changed_slide_ids.filter((v) => typeof v === 'string') as string[])
        : Array.isArray(raw.changed_block_ids)
          ? (raw.changed_block_ids.filter((v) => typeof v === 'string') as string[])
          : []
      const provider = (raw.provider && typeof raw.provider === 'object' ? raw.provider : {}) as Record<string, unknown>
      lastRegen.value = {
        patchId,
        targetKind: input.kind,
        targetId: input.targetId,
        changedIds,
        providerModel: typeof provider.model === 'string' ? provider.model : null,
        providerRunId: typeof provider.run_id === 'string' ? provider.run_id : null,
      }
      // **关键**：不乐观改动 view —— 重生成的产物落库由 apply 决定（INV-4）。
      error.value = null
      localErrorKey.value = null
      return lastRegen.value
    } catch (e) {
      if (e instanceof PrepApiError && e.info.kind === 'conflict') {
        captureConflict(e, revisionAtSubmit, [])
        return null
      }
      handleError(e)
      return null
    } finally {
      busy.value = false
    }
  }

  // ── 撤销 / 版本历史（INV-5：不经本地数组）─────────────────────────────
  async function revertRevision(targetRevision: number, summary?: string): Promise<number | null> {
    if (!specKind.value || !remoteId.value || currentRevision.value === null) return null
    const body = { expected_revision: currentRevision.value, summary: summary ?? null }
    busy.value = true
    try {
      const raw =
        specKind.value === 'teaching_deck'
          ? await revertDeckRevision(remoteId.value, targetRevision, body)
          : await revertLessonPlanRevision(remoteId.value, targetRevision, body)
      const rec = raw as Record<string, unknown>
      const next = typeof rec.revision === 'number' ? rec.revision : null
      if (next === null) {
        localErrorKey.value = 'prep.t24.error.revert_unparsable'
        return null
      }
      currentRevision.value = next
      pendingProposal.value = null
      // 撤销不改历史：回拉 revision 列表（新 revision 是**新增**的一条）
      await reload()
      return next
    } catch (e) {
      if (e instanceof PrepApiError && e.info.kind === 'conflict') {
        captureConflict(e, body.expected_revision, [])
        return null
      }
      handleError(e)
      return null
    } finally {
      busy.value = false
    }
  }

  async function refreshRevisions() {
    try {
      await loadRevisions()
    } catch (e) {
      handleError(e)
    }
  }

  // ── 逐页 QA ────────────────────────────────────────────────────────────
  async function refreshQa() {
    try {
      await loadQa()
    } catch (e) {
      handleError(e)
    }
  }

  /** 单页局部重试：**只**替换这一页（其余页由服务端 `others_unchanged` 背书）。 */
  async function retryQaPage(pageNo: number): Promise<boolean> {
    if (specKind.value !== 'teaching_deck' || !remoteId.value || currentRevision.value === null) {
      localErrorKey.value = 'prep.t24.error.no_artifact'
      return false
    }
    busy.value = true
    try {
      const raw = await retryPageQa(remoteId.value, {
        page_no: pageNo,
        expected_revision: currentRevision.value,
      })
      const retry = normalizeQaRetry(raw)
      if (!retry) {
        localErrorKey.value = 'prep.t24.error.qa_unparsable'
        return false
      }
      if (!retry.othersUnchanged) {
        // 服务端没有给出"其它页未变"的保证 → 拒绝局部合并，改为整体回拉（诚实，不猜）。
        await loadQa()
        localErrorKey.value = 'prep.t24.error.qa_not_local'
        return true
      }
      qa.value = qa.value ? mergeQaRetry(qa.value, retry) : null
      error.value = null
      localErrorKey.value = null
      return true
    } catch (e) {
      if (e instanceof PrepApiError && e.info.kind === 'conflict') {
        captureConflict(e, currentRevision.value, [])
        return false
      }
      handleError(e)
      return false
    } finally {
      busy.value = false
    }
  }

  // ── 导出作业（真实状态机）──────────────────────────────────────────────
  function setPollIntervalMs(ms: number) {
    pollIntervalMs.value = Math.max(1, Math.floor(ms))
  }

  function clearPoll(jobId: string) {
    const timer = pollTimers.get(jobId)
    if (timer !== undefined) clearTimeout(timer)
    pollTimers.delete(jobId)
  }

  function stopAllPolling() {
    for (const timer of pollTimers.values()) clearTimeout(timer)
    pollTimers.clear()
    for (const timer of ticketTimers.values()) clearTimeout(timer)
    ticketTimers.clear()
  }

  function ensureEntry(format: string): ExportEntry {
    if (!exports.value[format]) exports.value[format] = emptyEntry(format)
    return exports.value[format]
  }

  function applyExportJob(format: string, job: ExportJob) {
    const entry = exports.value[format] ?? emptyEntry(format)
    exports.value[format] = {
      ...entry,
      format,
      jobId: job.jobId,
      status: job.status,
      artifactId: job.artifactId ?? entry.artifactId,
      error: job.error,
      progressStage: job.progressStage,
      progressPageNo: job.progressPageNo,
      progressPageCount: job.progressPageCount,
      events: job.events,
    }
  }

  /**
   * artifact → 导出格式。
   *
   * 导出流程会在 job 变成 done 时登记映射；但"先有 artifact 再补票据"的路径
   * （例如只恢复了一个 job 条目、或票据需要重新领取）也必须能定位到条目 ——
   * 因此先按记录查，再按 `exports` 里已登记的 `artifactId` **反查**。
   * 查不到就明确返回 null（不猜格式）。
   */
  function formatForArtifact(artifactId: string): string | null {
    const mapped = ticketFormats.get(artifactId)
    if (mapped && exports.value[mapped]) return mapped
    for (const [format, entry] of Object.entries(exports.value)) {
      if (entry.artifactId === artifactId) return format
    }
    return null
  }

  /** 领取短时票据（INV-7）：带 `expires_at`，到期即本地置 expired。 */
  async function requestTicket(artifactId: string, ttlSeconds?: number | null): Promise<DownloadTicketState | null> {
    try {
      const raw = await createDownloadTicket(artifactId, ttlSeconds ?? null)
      const ticket = normalizeTicket(raw)
      if (!ticket) {
        localErrorKey.value = 'prep.t24.error.ticket_unparsable'
        return null
      }
      const state = ticketStateFrom(ticket)
      const format = formatForArtifact(artifactId)
      if (format) {
        const entry = ensureEntry(format)
        exports.value[format] = {
          ...entry,
          artifactId,
          ticket: state,
          status: entry.status === 'expired' ? 'done' : entry.status,
        }
      }
      armTicketExpiry(artifactId, ticket.expiresAt)
      return state
    } catch (e) {
      handleError(e)
      return null
    }
  }

  /** 到期由 `expires_at` 决定（不猜、不轮询、不用定时器伪造进度）。 */
  function armTicketExpiry(artifactId: string, expiresAt: string | null) {
    const existing = ticketTimers.get(artifactId)
    if (existing !== undefined) clearTimeout(existing)
    ticketTimers.delete(artifactId)
    if (!expiresAt) return
    const at = Date.parse(expiresAt)
    if (Number.isNaN(at)) return
    const delay = Math.max(0, Math.min(at - Date.now(), 2_147_000_000))
    const timer = setTimeout(() => {
      ticketTimers.delete(artifactId)
      markTicketExpired(artifactId)
    }, delay)
    ticketTimers.set(artifactId, timer)
  }

  function markTicketExpired(artifactId: string) {
    const format = formatForArtifact(artifactId)
    if (!format) return
    const entry = exports.value[format]
    if (!entry?.ticket) return
    exports.value[format] = {
      ...entry,
      status: 'expired',
      ticket: { ...entry.ticket, expired: true },
    }
  }

  /** redeemer 反馈的 410（过期）/ 403（撤权）：浏览器导航观测不到，需 API 侧回传。 */
  function markRedeemFailure(artifactId: string, status: number) {
    const format = formatForArtifact(artifactId)
    if (!format) return
    const entry = exports.value[format]
    if (!entry?.ticket) return
    exports.value[format] = {
      ...entry,
      status: 'expired',
      ticket: { ...entry.ticket, expired: true, redeemFailure: status },
    }
  }

  /** 链接过期后的"重新获取"：重新走 `POST /{artifact_id}/tickets`（旧票据服务端一次性）。 */
  async function refreshDownload(artifactId?: string, ttlSeconds?: number | null) {
    const target = artifactId ?? Object.values(exports.value).find((e) => e.status === 'expired')?.artifactId
    if (!target) return null
    return requestTicket(target, ttlSeconds)
  }

  /**
   * 按格式"重新获取"（UI 的确定入口）：从该格式自己的条目取 artifact，
   * 避免在多个格式同时过期时挑错条目。
   */
  async function refreshDownloadForFormat(format: string, ttlSeconds?: number | null) {
    const artifactId = exports.value[format]?.artifactId
    if (!artifactId) return null
    return requestTicket(artifactId, ttlSeconds)
  }

  async function startExport(format: string): Promise<ExportEntry | null> {
    if (!specKind.value || !remoteId.value || currentRevision.value === null) {
      localErrorKey.value = 'prep.t24.error.no_artifact'
      return null
    }
    busy.value = true
    try {
      const revisionAtSubmit = currentRevision.value
      const raw =
        specKind.value === 'teaching_deck'
          ? await requestDeckExport(remoteId.value, {
              format: format === 'pdf' ? 'pdf' : 'pptx',
              expected_revision: revisionAtSubmit,
              idempotency_key: idempotencyKey('export'),
            })
          : await requestLessonPlanExport(remoteId.value, {
              format: 'docx',
              expected_revision: revisionAtSubmit,
              idempotency_key: idempotencyKey('export'),
            })
      const rec = raw as Record<string, unknown>
      const jobId = typeof rec.job_id === 'string' ? rec.job_id : null
      if (!jobId) {
        localErrorKey.value = 'prep.t24.error.export_unparsable'
        return null
      }
      const status = rec.status === 'running' ? 'running' : rec.status === 'done' ? 'done' : 'pending'
      exports.value[format] = {
        ...emptyEntry(format),
        jobId,
        status,
        error: null,
      }
      const entry = exports.value[format]
      if (status === 'pending' || status === 'running') schedulePoll(jobId, format)
      error.value = null
      localErrorKey.value = null
      return entry
    } catch (e) {
      if (e instanceof PrepApiError && e.info.kind === 'conflict') {
        captureConflict(e, currentRevision.value, [])
        return null
      }
      handleError(e)
      return null
    } finally {
      busy.value = false
    }
  }

  function schedulePoll(jobId: string, format: string) {
    if (pollTimers.has(jobId)) return
    const timer = setTimeout(() => {
      pollTimers.delete(jobId)
      void pollOnce(jobId, format)
    }, pollIntervalMs.value)
    pollTimers.set(jobId, timer)
  }

  async function pollOnce(jobId: string, format: string) {
    const entry = exports.value[format]
    if (!entry || entry.jobId !== jobId) return
    if (entry.status === 'cancelled' || entry.status === 'expired') return
    try {
      const raw = await getExportJob(jobId)
      const job = normalizeExportJob(raw, format)
      if (!job) return
      applyExportJob(format, job)
      if (job.status === 'pending' || job.status === 'running') {
        schedulePoll(jobId, format)
        return
      }
      if (job.status === 'done' && job.artifactId) {
        ticketFormats.set(job.artifactId, format)
        await requestTicket(job.artifactId)
      }
    } catch (e) {
      handleError(e)
      schedulePoll(jobId, format)
    }
  }

  async function cancelExport(format: string): Promise<boolean> {
    const entry = exports.value[format]
    if (!entry?.jobId) return false
    const jobId = entry.jobId
    clearPoll(jobId)
    try {
      await cancelExportJob(jobId)
      exports.value[format] = { ...entry, status: 'cancelled', error: null }
      return true
    } catch (e) {
      handleError(e)
      return false
    }
  }

  function stopExportPolling(jobId?: string) {
    if (jobId) clearPoll(jobId)
    else for (const id of [...pollTimers.keys()]) clearPoll(id)
  }

  return {
    // identity
    specKind, remoteId,
    // server facts
    view, revisions, currentRevision, revision, pendingProposal, qa, exports,
    slides, blocks, lastRegen, lastApplied,
    // client draft / conflict
    draftOperations, rejectedOperations, conflict,
    // meta
    busy, error, localErrorKey, pollIntervalMs,
    // derived
    hasPendingProposal, dirty, canApply, exportEntries, qaStatusByPageNo, failedPageNos,
    // actions
    attach, detach, reload, reloadAfterConflict,
    setDraftOperations, clearDraft,
    proposePatch, applyPatch, rejectPatch,
    reapply, discard,
    regenTarget,
    revertRevision, refreshRevisions, refreshQa, retryQaPage,
    startExport, cancelExport, stopExportPolling, setPollIntervalMs,
    requestTicket, refreshDownload, refreshDownloadForFormat, markRedeemFailure, markTicketExpired,
  }
})

/** `affected` 归一化（apply/revert 响应里的局部性声明）。 */
function normalizeAffected(raw: unknown): AffectedSet | null {
  if (!raw || typeof raw !== 'object') return null
  const rec = raw as Record<string, unknown>
  const strings = (v: unknown) => (Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : [])
  return {
    slideIds: strings(rec.slide_ids),
    blockIds: strings(rec.block_ids),
    count: typeof rec.count === 'number' ? rec.count : null,
  }
}
