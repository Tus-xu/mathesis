/**
 * T24 · 把「编辑 store」接到 **ArtifactFocusCanvas 两栏工作区**（T22 living-artifact 形态）。
 *
 * 职责边界：
 * - 本 composable **只做适配**：从 artifact 身份推出 spec kind / remoteId，把 store 的
 *   服务端事实与动作整理成组件需要的 view-model；不做任何业务推导。
 * - 所有服务端事实（revision / proposal / QA / 导出 / 票据 / lineage）都转发 store，
 *   组件层**零**本地业务缓存。
 * - 组件卸载或 artifact 变化 → `detach()`：停止导出轮询与票据到期定时器。
 */
import { computed, onScopeDispose, ref, watch, type Ref } from 'vue'

import { copyFor, copyFormat } from '@/features/teacher-prep-v2/i18n/copy'
import { useArtifactEditingStore } from '@/features/teacher-prep-v2/stores/artifactEditing'
import type { ConflictState, ExportEntry } from '@/features/teacher-prep-v2/stores/artifactEditing'
import type {
  DeckSlideSummary,
  LessonBlockSummary,
  PatchOperation,
  QaReport,
  RevisionListItem,
  SpecKind,
  SpecView,
} from '@/features/teacher-prep-v2/types/t24'
import type { ActiveArtifact } from '@/features/teacher-prep-v2/projection/types'
import type { ExportCapability } from '@/features/teacher-prep-v2/artifacts/shared/ExportPanel.vue'
import type { LineageData } from '@/features/teacher-prep-v2/artifacts/shared/LineagePanel.vue'

/** 契约里每类 spec 的导出格式（不是"能力猜测"，是冻结契约 S2.4 的请求枚举）。 */
const FORMATS_BY_KIND: Record<SpecKind, string[]> = {
  teaching_deck: ['pptx', 'pdf'],
  lesson_plan: ['docx'],
}

/** 编辑区需要的一页（服务端 `slides[]` 摘要 + `spec` 里的可编辑内容）。 */
export interface DeckEditorSlide {
  id: string
  title: string
  content: string
  notes: string
  qaStatus: 'pass' | 'fail' | 'unknown' | null
}

/** 编辑区需要的一个块。 */
export interface LessonEditorBlock {
  id: string
  title: string
  kind: string
  content: string
  sourceLocator: string | null
}

function asRecords(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value) ? value.filter((v): v is Record<string, unknown> => Boolean(v) && typeof v === 'object') : []
}

function readText(source: Record<string, unknown> | undefined, keys: string[]): string {
  if (!source) return ''
  for (const key of keys) {
    const value = source[key]
    if (typeof value === 'string') return value
  }
  return ''
}

/**
 * 服务端事实 → 编辑区页列表。
 *
 * `GET /decks/{id}` 给的是 `slides[]`（id/title/layout_hint/qa_status）**外加**完整 `spec`；
 * 元素正文只在 `spec` 里。这里按 id（回落到 index）把两者对齐 —— 不猜形状：
 * 找不到正文就是空串（诚实空白，而不是编造内容）。
 */
export function deckEditorSlides(view: SpecView | null): DeckEditorSlide[] {
  if (!view) return []
  const specSlides = asRecords(view.spec?.slides)
  const byId = new Map<string, Record<string, unknown>>()
  specSlides.forEach((item, index) => {
    const id = typeof item.id === 'string' ? item.id : `#${index}`
    byId.set(id, item)
  })
  return view.slides.map((slide, index) => {
    const spec = byId.get(slide.id) ?? specSlides[index]
    return {
      id: slide.id,
      title: slide.title,
      content: readText(spec, ['content', 'text', 'body']),
      notes: readText(spec, ['notes', 'speaker_note', 'speaker_notes', 'script']),
      qaStatus: slide.qaStatus,
    }
  })
}

/** 服务端事实 → 编辑区块列表（块正文来自 `spec.blocks` / `spec.body.blocks`）。 */
export function lessonEditorBlocks(view: SpecView | null): LessonEditorBlock[] {
  if (!view) return []
  const specBlocks = asRecords(view.spec?.blocks).length
    ? asRecords(view.spec?.blocks)
    : asRecords((view.spec?.body as Record<string, unknown> | undefined)?.blocks)
  const byId = new Map<string, Record<string, unknown>>()
  specBlocks.forEach((item, index) => {
    const id = typeof item.id === 'string' ? item.id : `#${index}`
    byId.set(id, item)
  })
  return view.blocks.map((block, index) => {
    const spec = byId.get(block.id) ?? specBlocks[index]
    return {
      id: block.id,
      title: block.title ?? block.id,
      kind: block.kind ?? 'content',
      content: readText(spec, ['content', 'text']),
      sourceLocator: block.sourceLocator,
    }
  })
}

export interface ArtifactEditingView {
  specKind: SpecKind
  remoteId: string
  revision: number | null
  specSha256: string | null
  metadata?: Record<string, unknown> | null
  slides: DeckSlideSummary[]
  blocks: LessonBlockSummary[]
  /** 编辑区直接消费的页/块（服务端事实 + `spec` 正文对齐后的视图）。 */
  deckSlides: DeckEditorSlide[]
  lessonBlocks: LessonEditorBlock[]
  qa: QaReport | null
  qaByPage: Record<number, string>
  failedPages: number[]
  revisions: RevisionListItem[]
  conflict: ConflictState | null
  exportEntries: ExportEntry[]
  exportCapabilities: ExportCapability[]
  lineage: LineageData
  hasPendingProposal: boolean
  pendingPatchId: string | null
  dirty: boolean
  busy: boolean
  errorKey: string | null
  errorKind: string | null
  errorDetail: string | null
  requestId: string | null
  /** 服务端尚未返回时的首次加载态（不是"能力缺失"）。 */
  loading: boolean
}

export interface ArtifactEditingHandlers {
  propose(operations: PatchOperation[], summary?: string): Promise<unknown>
  applyPending(patchId?: string): Promise<unknown>
  rejectPending(reason?: string): Promise<unknown>
  regen(payload: { kind: 'slide' | 'block'; targetId: string; instruction: string }): Promise<unknown>
  retryQaPage(pageNo: number): Promise<unknown>
  revertRevision(revision: number, summary?: string): Promise<unknown>
  requestExport(format: string): Promise<unknown>
  cancelExport(format: string): Promise<unknown>
  /** 短时链接过期后按**格式**重新领取（确定入口，不猜是哪个格式过期）。 */
  refreshDownload(format?: string): Promise<unknown>
  markRedeemFailure(artifactId: string, status: number): void
  reload(): Promise<unknown>
  conflictReload(): Promise<unknown>
  conflictReapply(): Promise<unknown>
  conflictDiscard(): void
  setDraft(operations: PatchOperation[]): void
}

export interface UseArtifactEditingResult {
  editing: Ref<ArtifactEditingView | null>
  handlers: ArtifactEditingHandlers
}

/** artifact 身份 → (kind, remoteId)。所有候选都来自**服务端 body**，最后才回落到 artifact_id。 */
export function resolveArtifactIdentity(
  artifact: Pick<ActiveArtifact, 'artifact_type' | 'artifact_id'> | null,
  body: Record<string, unknown> | null,
): { kind: SpecKind; remoteId: string } | null {
  if (!artifact) return null
  const str = (v: unknown) => (typeof v === 'string' && v ? v : null)
  if (artifact.artifact_type === 'teaching_deck_spec') {
    const remoteId = str(body?.deck_id) ?? str(body?.spec_id) ?? str(body?.id) ?? artifact.artifact_id
    return remoteId ? { kind: 'teaching_deck', remoteId } : null
  }
  if (artifact.artifact_type === 'lesson_plan_spec') {
    const remoteId = str(body?.spec_id) ?? str(body?.id) ?? str(body?.lesson_plan_id) ?? artifact.artifact_id
    return remoteId ? { kind: 'lesson_plan', remoteId } : null
  }
  return null
}

function exportStateOf(entry: ExportEntry | undefined): ExportCapability['state'] {
  if (!entry || entry.status === 'idle') return 'available'
  return entry.status
}

export function useArtifactEditing(
  artifact: Ref<ActiveArtifact | null>,
  body: Ref<Record<string, unknown> | null>,
  revisionHint: Ref<number | null>,
): UseArtifactEditingResult {
  const store = useArtifactEditingStore()

  const identity = computed(() => resolveArtifactIdentity(artifact.value, body.value))
  const loading = ref(false)

  async function sync() {
    const next = identity.value
    if (!next) {
      store.detach()
      loading.value = false
      return
    }
    if (store.specKind === next.kind && store.remoteId === next.remoteId && store.view) return
    loading.value = true
    try {
      await store.attach({ kind: next.kind, remoteId: next.remoteId, revisionHint: revisionHint.value })
    } finally {
      loading.value = false
    }
  }

  watch([() => identity.value?.kind ?? null, () => identity.value?.remoteId ?? null], () => {
    void sync()
  }, { immediate: true })

  onScopeDispose(() => {
    // 组件卸载：停止导出轮询 / 票据到期定时器，并清空跨 artifact 复用的事实
    store.detach()
  })

  const editing = computed<ArtifactEditingView | null>(() => {
    const id = identity.value
    if (!id) return null
    const lineageRaw = store.view?.lineage ?? null
    const promptRef = lineageRaw?.prompt_ref as Record<string, unknown> | undefined
    const promptRefLabel = promptRef
      ? [typeof promptRef.id === 'string' ? promptRef.id : null, typeof promptRef.version === 'string' ? promptRef.version : null]
          .filter(Boolean)
          .join('@') || null
      : null
    const qaReport = store.qa
    return {
      specKind: id.kind,
      remoteId: store.remoteId ?? id.remoteId,
      revision: store.revision,
      specSha256: store.view?.specSha256 ?? null,
      metadata: store.view?.metadata ?? null,
      slides: store.slides,
      blocks: store.blocks,
      deckSlides: deckEditorSlides(store.view),
      lessonBlocks: lessonEditorBlocks(store.view),
      qa: qaReport,
      qaByPage: store.qaStatusByPageNo,
      failedPages: store.failedPageNos,
      revisions: store.revisions,
      conflict: store.conflict,
      exportEntries: store.exportEntries,
      exportCapabilities: FORMATS_BY_KIND[id.kind].map((format) => {
        const entry = store.exports[format]
        const expired = entry?.ticket?.expired === true || entry?.status === 'expired'
        // 作业 done **且**拿到短时票据才叫「可下载」；done 而无票据只能是「已完成」。
        const state: ExportCapability['state'] = expired
          ? 'expired'
          : entry?.status === 'done'
            ? (entry?.ticket?.url ? 'ready' : 'done')
            : exportStateOf(entry)
        return {
          format,
          label: copyFor(`prep.t24.export.format.${format}`),
          state,
          downloadUrl: expired ? null : entry?.ticket?.url ?? null,
          fileName: null,
          jobId: entry?.jobId ?? null,
          artifactId: entry?.artifactId ?? null,
          error: entry?.error ?? null,
          progressStage: entry?.progressStage ?? null,
          progressPageNo: entry?.progressPageNo ?? null,
          progressPageCount: entry?.progressPageCount ?? null,
        }
      }),
      lineage: {
        blueprintRevision: typeof lineageRaw?.blueprint_revision === 'number' ? lineageRaw.blueprint_revision : null,
        artifactRevision: store.revision,
        sourceLocator: typeof lineageRaw?.source_locator === 'string' ? lineageRaw.source_locator : null,
        runId: typeof lineageRaw?.run_id === 'string' ? lineageRaw.run_id : null,
        hash: store.view?.specSha256 ?? null,
        specRevision: store.revision,
        contentHash: store.view?.specSha256 ?? null,
        createdByRunId: typeof lineageRaw?.created_by_run_id === 'string' ? lineageRaw.created_by_run_id : null,
        promptRef: promptRefLabel,
        sources: [
          ...(Array.isArray(lineageRaw?.evidence_refs) ? lineageRaw.evidence_refs : []),
          ...(Array.isArray(lineageRaw?.source_refs) ? lineageRaw.source_refs : []),
        ].filter((v): v is string => typeof v === 'string'),
        qaStatus: qaReport
          ? copyFormat('prep.t24.lineage.qa_value', `${qaReport.p0Count}/${qaReport.p1Count}`)
          : null,
      },
      hasPendingProposal: store.hasPendingProposal,
      pendingPatchId: store.pendingProposal?.patchId ?? null,
      dirty: store.dirty,
      busy: store.busy || loading.value,
      errorKey: store.localErrorKey ?? store.error?.copyKey ?? null,
      errorKind: store.error?.kind ?? null,
      errorDetail: store.error?.detail ?? null,
      requestId: store.error?.requestId ?? null,
      loading: loading.value,
    }
  })

  const handlers: ArtifactEditingHandlers = {
    propose: (operations, summary) => store.proposePatch(operations, { summary, source: 'human' }),
    applyPending: (patchId) => store.applyPatch(patchId),
    rejectPending: (reason) => store.rejectPatch(reason),
    regen: (payload) => store.regenTarget(payload),
    retryQaPage: (pageNo) => store.retryQaPage(pageNo),
    revertRevision: (revision, summary) => store.revertRevision(revision, summary),
    requestExport: (format) => store.startExport(format),
    cancelExport: (format) => store.cancelExport(format),
    refreshDownload: (format) =>
      format ? store.refreshDownloadForFormat(format) : store.refreshDownload(),
    markRedeemFailure: (artifactId, status) => store.markRedeemFailure(artifactId, status),
    reload: () => store.reload(),
    conflictReload: () => store.reloadAfterConflict(),
    conflictReapply: () => store.reapply(),
    conflictDiscard: () => store.discard(),
    setDraft: (operations) => store.setDraftOperations(operations),
  }

  return { editing, handlers }
}
