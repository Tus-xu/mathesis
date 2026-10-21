/**
 * T22.1 · UI projection 类型。
 *
 * 铁律：
 * - 本文件是**纯类型**，不含任何 DOM / Vue 依赖，可在 node 环境直接单测。
 * - `sessionPhase` 等**业务状态只能由服务端事实派生**，前端不得用字符串猜阶段
 *   （UX-CONTRACT §2）。本模块是唯一派生入口。
 * - `surfaceMode` / `activeOverlay` 属 **UI 偏好**，允许内存态；但**除草稿与折叠
 *   偏好外不得持久化**（UX-CONTRACT §2 末段）。
 */

/** 页面级模式。仅两种：沉浸式对话 / 教师主动打开的双区画布。 */
export type SurfaceMode = 'conversation' | 'artifact_focus'

export const SESSION_PHASES = [
  'empty',
  'clarifying',
  'context_ready',
  'proposal_pending',
  'waiting_confirmation',
  'generating',
  'artifact_ready',
  'revising',
  'completed',
  'degraded',
  'forbidden',
  'not_found',
] as const

export type SessionPhase = (typeof SESSION_PHASES)[number]

export const STREAM_PHASES = [
  'connecting',
  'live',
  'reconnecting',
  'stale',
  'closed',
  'error',
] as const

export type StreamPhase = (typeof STREAM_PHASES)[number]

/** 按需互斥面板：同一时刻只允许一个非 none。 */
export const ACTIVE_OVERLAYS = ['none', 'evidence', 'version_history', 'qa', 'export'] as const
export type ActiveOverlay = (typeof ACTIVE_OVERLAYS)[number]

/**
 * artifact 类型。**只列服务端当前真实存在或已列入契约的四种**；
 * 后两种 8100 尚无端点 → UI 必须 disabled + `data-coming-from-contract`。
 */
export const ARTIFACT_TYPES = [
  'teaching_blueprint',
  'diagnostic_task',
  'lesson_plan_spec',
  'teaching_deck_spec',
] as const
export type ArtifactType = (typeof ARTIFACT_TYPES)[number]

/** 8100 当前**确有 HTTP 端点、可真实编辑**的 artifact 类型白名单。 */
export const EDITABLE_ARTIFACT_TYPES: readonly ArtifactType[] = [
  'teaching_blueprint',
  'diagnostic_task',
]

/**
 * 可进入聚焦画布的类型白名单（2026-09-14 备课中心简化后）。
 *
 * 教师明确要求：备课本不再提供「大纲预览」与「教案预览」两个中间环节，
 * AI 生成结束后直达 PPT 编辑工作台（`/teacher-v3/slides?mode=vnext`）。
 * 因此：
 *   - `teaching_blueprint`（= 大纲）不再可聚焦 → 会话内也不再渲染其正文与确认按钮；
 *   - `lesson_plan_spec`（= 教案）不再可聚焦 → 该 spec 仍由服务端投影并保留 lineage，
 *     只是不再有可达的预览界面；
 *   - `teaching_deck_spec`（= 课件）由 `SlidesView` 工作台承载，聚焦画布只作兜底；
 *   - `diagnostic_task` 属学前诊断链路，不在本次简化范围内，保持不变。
 */
export const FOCUSABLE_ARTIFACT_TYPES: readonly ArtifactType[] = [
  'diagnostic_task',
  'teaching_deck_spec',
]

export interface ActiveArtifact {
  artifact_id: string
  artifact_type: ArtifactType
  revision: number
  title: string
  status: string | null
  updated_at: string | null
}

/** 持久 PendingAction 摘要（服务端权威；前端只展示，不驱动状态机）。 */
export interface PendingActionSummary {
  pending_action_id: string
  action: string | null
  created_at: string | null
  expires_at: string | null
}

/** 服务端事实输入（只从 snapshot / HTTP 归一化结果来）。 */
export interface ProjectionFacts {
  loading: boolean
  /** 归一化后的错误；null 表示无错误。 */
  errorKind:
    | 'unauthenticated'
    | 'forbidden'
    | 'conflict'
    | 'not_found'
    | 'gone'
    | 'rate_limited'
    | 'error'
    | 'network'
    | null
  /** 服务端下发的 `session`，无则 null。 */
  sessionStatus: string | null
  sessionRevision: number | null
  pendingActionId: string | null
  contextSnapshotId: string | null
  /** `artifact.blueprint.status`（8100 state_machines: proposal/confirmed/rejected）。 */
  blueprintStatus: string | null
  blueprintId: string | null
  blueprintRevision: number | null
  blueprintTitle: string | null
  /** `artifact.diagnostic.status`（proposal/draft/published/skipped）。 */
  diagnosticStatus: string | null
  diagnosticId: string | null
  /** `artifact.lesson_plan`（L-221-2 specs 投影）。 */
  lessonPlanId?: string | null
  lessonPlanRevision?: number | null
  /** `artifact.teaching_deck`（R21/8200 TeachingDeckSpec 投影）。 */
  teachingDeckId?: string | null
  teachingDeckRevision?: number | null
  /** job_summary 中是否仍有 queued/running。 */
  hasRunningJob: boolean
  hasFailedJob: boolean
  /** 8100 `basic_mode`：能力降级但会话仍可用。 */
  basicMode: boolean
  /** timeline 中是否至少有一条 assistant 消息。 */
  hasAssistantMessage: boolean
  timelineLength: number
}

/** UI 偏好输入（内存态 / 仅草稿与折叠可落 localStorage）。 */
export interface ProjectionUi {
  /** 教师是否主动打开了画布。只有它为 true 才允许 artifact_focus。 */
  focusRequested: boolean
  activeOverlay: ActiveOverlay
  /** 被 dock 关闭后不再显示（本次会话内有效）。 */
  dockDismissed: boolean
}

export interface UiProjection {
  surfaceMode: SurfaceMode
  sessionPhase: SessionPhase
  streamPhase: StreamPhase
  activeOverlay: ActiveOverlay
  activeArtifact: ActiveArtifact | null
  pendingAction: PendingActionSummary | null
  /** 是否允许出现「查看并修改」入口（UX-CONTRACT C）。 */
  canOpenFocus: boolean
  /** 是否显示「本会话产物」dock（小型、可关闭）。 */
  showDock: boolean
  /** 是否有真实 artifact 存在（决定 canvas 能否渲染）。 */
  hasArtifact: boolean
  /** 消息时间线是否应渲染（错误聚焦态下为 false）。 */
  showConversation: boolean
  /** 是否需要渲染聚焦式单状态页（401/403/404/409/不可用）。 */
  focusedError:
    | 'unauthenticated'
    | 'forbidden'
    | 'not_found'
    | 'conflict'
    | 'gone'
    | 'unavailable'
    | null
}
