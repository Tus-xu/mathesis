/**
 * T22.1 · snapshot/event → UI projection（**纯函数**，零 Vue / 零 DOM）。
 *
 * 设计意图：把"服务端事实 → 界面形态"的推导**集中到一个可单测的纯函数**，
 * 组件里不再出现 `if (phase === ...)` 的业务判断（UX-CONTRACT §2）。
 *
 * 优先级（不可颠倒）：
 *   1. 加载中 → degraded/loading 语义（不显示空三栏）
 *   2. 权威错误（401/403/404/409/不可用）→ 聚焦式单状态页
 *   3. 服务端领域事实 → sessionPhase
 *   4. UI 偏好 → surfaceMode / activeOverlay
 */

import type {
  ActiveArtifact,
  ArtifactType,
  PendingActionSummary,
  ProjectionFacts,
  ProjectionUi,
  SessionPhase,
  StreamPhase,
  UiProjection,
} from './types'
import { EDITABLE_ARTIFACT_TYPES, FOCUSABLE_ARTIFACT_TYPES } from './types'
import { canSwitchSurface, FOCUS_ALLOWED_PHASES } from './transitions'

/** 由错误 kind 决定是否需要聚焦式单状态页（同一时刻只有一个主错误呈现）。 */
function focusedErrorOf(
  kind: ProjectionFacts['errorKind'],
): UiProjection['focusedError'] {
  switch (kind) {
    case 'unauthenticated':
      return 'unauthenticated'
    case 'forbidden':
      return 'forbidden'
    case 'not_found':
      return 'not_found'
    case 'gone':
      return 'gone'
    case 'conflict':
      return 'conflict'
    case 'error':
    case 'network':
    case 'rate_limited':
      return 'unavailable'
    default:
      return null
  }
}

/**
 * 从服务端事实派生 `activeArtifact`。
 *
 * 2026-09-14 备课中心简化：**只暴露课件**（外加学前诊断）。
 * 「大纲」（teaching_blueprint）与「教案」（lesson_plan_spec）仍是服务端事实
 * （里程碑与 lineage 照常使用 facts 里的字段），但不再成为可打开的产物，
 * 因此不会再把教师引向已删除的大纲/教案预览界面。
 */
export function deriveActiveArtifact(f: ProjectionFacts): ActiveArtifact | null {
  if (f.teachingDeckId) {
    return {
      artifact_id: f.teachingDeckId,
      artifact_type: 'teaching_deck_spec',
      revision: f.teachingDeckRevision ?? 0,
      title: f.blueprintTitle ?? '',
      status: 'available',
      updated_at: null,
    }
  }
  if (f.diagnosticId && (f.diagnosticStatus === 'draft' || f.diagnosticStatus === 'published')) {
    return {
      artifact_id: f.diagnosticId,
      artifact_type: 'diagnostic_task',
      revision: 0,
      title: '',
      status: f.diagnosticStatus,
      updated_at: null,
    }
  }
  return null
}

/** 该 artifact 类型是否真的可编辑（8100 有端点）。 */
export function isArtifactEditable(type: ArtifactType): boolean {
  return EDITABLE_ARTIFACT_TYPES.includes(type)
}

export function isArtifactFocusable(type: ArtifactType): boolean {
  return FOCUSABLE_ARTIFACT_TYPES.includes(type)
}

/** 纯函数：服务端事实 → sessionPhase。 */
export function deriveSessionPhase(f: ProjectionFacts): SessionPhase {
  // 1) 权威错误最高优先（403 绝不允许出现"已就绪"）
  const fe = focusedErrorOf(f.errorKind)
  if (fe === 'forbidden') return 'forbidden'
  if (fe === 'not_found') return 'not_found'

  // 2) 领域事实推导
  if (f.pendingActionId) return 'waiting_confirmation'
  if (f.hasRunningJob) return 'generating'

  const bp = f.blueprintStatus
  const dg = f.diagnosticStatus
  // T24 specs are real, restorable artifacts even when their editor/export
  // mutation endpoint is unavailable. The focus adapter must still be openable.
  if (f.lessonPlanId || f.teachingDeckId) return 'artifact_ready'
  if (bp === 'confirmed' || dg === 'published') return 'artifact_ready'
  if (dg === 'draft') return 'artifact_ready'
  if (bp === 'proposal' || dg === 'proposal') return 'proposal_pending'
  if (f.contextSnapshotId) return 'context_ready'
  if (f.hasAssistantMessage) return 'clarifying'

  // 3) 无事实 → 空态（诚实空态，不填假数据）
  if (f.loading) return 'empty'
  return 'empty'
}

/** 纯函数：stream 事实 → streamPhase。 */
export function deriveStreamPhase(input: {
  loading: boolean
  connected: boolean
  reconnecting: boolean
  gapCount: number
  errorKind: ProjectionFacts['errorKind']
}): StreamPhase {
  if (input.errorKind && input.errorKind !== 'conflict') return 'error'
  if (input.reconnecting) return 'reconnecting'
  if (input.gapCount > 0) return 'stale'
  if (input.loading) return 'connecting'
  if (input.connected) return 'live'
  return 'closed'
}

/**
 * 主投影函数。
 * @param facts   服务端事实（snapshot + HTTP 归一化）
 * @param ui      UI 偏好（内存；仅草稿/折叠可持久化）
 * @param stream  stream 事实
 */
export function projectWorkspace(
  facts: ProjectionFacts,
  ui: ProjectionUi,
  stream: { connected: boolean; reconnecting: boolean; gapCount: number },
): UiProjection {
  const focusedError = focusedErrorOf(facts.errorKind)
  const sessionPhase = deriveSessionPhase(facts)
  const streamPhase = deriveStreamPhase({
    loading: facts.loading,
    connected: stream.connected,
    reconnecting: stream.reconnecting,
    gapCount: stream.gapCount,
    errorKind: facts.errorKind,
  })

  const activeArtifact = deriveActiveArtifact(facts)
  const hasArtifact = activeArtifact !== null

  // 「查看并修改」：必须真实 artifact + phase 允许 + 类型可编辑
  const canOpenFocus =
    hasArtifact &&
    activeArtifact !== null &&
    isArtifactFocusable(activeArtifact.artifact_type) &&
    FOCUS_ALLOWED_PHASES.includes(sessionPhase)

  // surfaceMode：**只有教师主动请求** 且 合法才进入 focus
  const wantsFocus = ui.focusRequested && canOpenFocus
  const surfaceMode: UiProjection['surfaceMode'] = canSwitchSurface(
    'conversation',
    wantsFocus ? 'artifact_focus' : 'conversation',
    { byTeacher: ui.focusRequested, phase: sessionPhase },
  )
    ? wantsFocus
      ? 'artifact_focus'
      : 'conversation'
    : 'conversation'

  // 聚焦式错误态下：不渲染对话舞台、不渲染 dock、不渲染 canvas
  const showConversation = focusedError === null
  const showDock =
    showConversation && hasArtifact && !ui.dockDismissed && surfaceMode === 'conversation'

  const pendingAction: PendingActionSummary | null = facts.pendingActionId
    ? {
        pending_action_id: facts.pendingActionId,
        action: null,
        created_at: null,
        expires_at: null,
      }
    : null

  return {
    surfaceMode,
    sessionPhase,
    streamPhase,
    // 错误聚焦态下强制关闭所有辅助面板（避免重复主错误）
    activeOverlay: focusedError === null ? ui.activeOverlay : 'none',
    activeArtifact,
    pendingAction,
    canOpenFocus,
    showDock,
    hasArtifact,
    showConversation,
    focusedError,
  }
}

/**
 * 从 `WorkspaceSnapshot` 抽取 projection 所需事实（**唯一**抽取入口）。
 * 放在这里而不是组件里，是为了让"哪个字段决定哪个状态"可单测、可审计。
 */
export function factsFromSnapshot(input: {
  snapshot: import('@/features/teacher-prep-v2/types/workspace').WorkspaceSnapshot | null
  loading: boolean
  errorKind: ProjectionFacts['errorKind']
}): ProjectionFacts {
  const s = input.snapshot
  const session = s?.session ?? null
  const bp = (s?.artifact?.blueprint ?? null) as Record<string, unknown> | null
  const dg = (s?.artifact?.diagnostic ?? null) as Record<string, unknown> | null
  const lp = (s?.artifact?.lesson_plan ?? null) as Record<string, unknown> | null
  const jobs = s?.job_summary ?? []

  const str = (v: unknown): string | null => (typeof v === 'string' && v ? v : null)
  const num = (v: unknown): number | null => (typeof v === 'number' ? v : null)
  const jobStatus = (value: unknown): string => String(value ?? '').trim().toUpperCase()

  return {
    loading: input.loading,
    errorKind: input.errorKind,
    sessionStatus: str(session?.status),
    sessionRevision: num(session?.revision),
    pendingActionId: str(session?.pending_action_id),
    contextSnapshotId: str(session?.context_snapshot_id),
    blueprintStatus: str(bp?.status),
    blueprintId: str(bp?.blueprint_id),
    blueprintRevision: num(bp?.revision),
    blueprintTitle: str(bp?.title),
    diagnosticStatus: str(dg?.status),
    diagnosticId: str(dg?.diagnostic_id ?? dg?.id),
    lessonPlanId: str(lp?.spec_id ?? lp?.id),
    lessonPlanRevision: num(lp?.revision),
    teachingDeckId: str((s?.artifact?.teaching_deck as Record<string, unknown> | null)?.spec_id ?? (s?.artifact?.teaching_deck as Record<string, unknown> | null)?.id),
    teachingDeckRevision: num((s?.artifact?.teaching_deck as Record<string, unknown> | null)?.revision),
    hasRunningJob: jobs.some((j) => ['PENDING', 'QUEUED', 'CLAIMED', 'RUNNING'].includes(jobStatus(j.status))),
    hasFailedJob: jobs.some((j) => ['FAILED', 'DLQ', 'BASIC_MODE'].includes(jobStatus(j.status))),
    basicMode: Boolean(s?.basic_mode),
    hasAssistantMessage: (s?.timeline ?? []).some((m) => m.role === 'assistant'),
    timelineLength: (s?.timeline ?? []).length,
  }
}
