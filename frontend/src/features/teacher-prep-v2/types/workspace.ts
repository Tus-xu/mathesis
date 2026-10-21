/**
 * T22 · teacher-prep-v2 工作区类型。
 *
 * 契约来源（唯一权威）：8100 `app/teacher_prep/conversation/schema.py`
 *   `WorkspaceSnapshot` / `MessageView` / `ActionView` / `JobSummaryView` / `ArtifactSnapshot`。
 *
 * 铁律：
 * - 本文件是 **P00 冻结的 typed 契约**的前端镜像；不得放宽为 `any`，
 *   也不得新增服务端没有的字段（`extra=forbid` 语义在前端同样成立）。
 * - 模型只能返回 **component_type + 类型化 props**，由 allowlisted catalog 渲染；
 *   任意 HTML/CSS/JS 在类型层即不可表达（无 `html` / `style` / `script` 字段）。
 */

/** allowlisted 组件类型 —— 必须与 8100 `catalog.COMPONENT_CATALOG` 逐一对齐。 */
export const ALLOWED_COMPONENT_TYPES = [
  'user_text',
  'assistant_text',
  'context_card',
  'blueprint_card',
  'evidence_rail',
  'job_status_card',
  'selection_card',
  'basic_mode_card',
  'diagnostic_card',
  'error_card',
  'direct_chunk_locator',
  'diagnostic_proposal',
  'class_learning_profile',
  'blueprint_revision',
  'run_context_disclosure',
] as const

export type ComponentType = (typeof ALLOWED_COMPONENT_TYPES)[number]

/** 组件引用：type 受 allowlist 约束，props 由各自 schema 校验（前端只读，不校验形状）。 */
export interface ComponentRef<T extends ComponentType = ComponentType> {
  type: T
  props: Record<string, unknown>
}

export type MessageRole = 'user' | 'assistant' | 'system'

/** timeline 单条（服务端权威投影）。 */
export interface MessageView {
  message_id: string
  seq: number
  role: MessageRole
  kind: string
  component: ComponentRef | null
  text: string | null
  intent: Record<string, unknown> | null
  context_snapshot_id: string | null
  blueprint_id: string | null
  run_status: string | null
  error_label: string | null
  error_code: number | null
  editable: boolean
  created_at: string
}

/** living artifact：上下文 + 蓝图 + 学前诊断 + LessonPlanSpec（L-221-2）。 */
export interface ArtifactSnapshot {
  context: Record<string, unknown> | null
  blueprint: Record<string, unknown> | null
  diagnostic: Record<string, unknown> | null
  lesson_plan?: Record<string, unknown> | null
  teaching_deck?: Record<string, unknown> | null
}

/** available action：action 枚举 + target + i18n copy_key（无硬编码业务文案）。 */
export interface ActionView {
  action: string
  target: string | null
  copy_key: string
  disabled: boolean
}

/** job summary：DB 权威作业状态投影（不做假进度）。 */
export interface JobSummaryView {
  job_id: string
  job_type: string
  status: string
  attempt: number
  max_attempts: number
  updated_at: string | null
  last_error: string | null
}

/** 会话投影（`session_dto`）。业务字段一律来自服务端，此处只声明结构需要的键。 */
export interface SessionView {
  id: string
  status: string
  revision: number
  last_event_seq: number
  [k: string]: unknown
}

/** GET / POST 会话的完整工作区投影（Conversation-first，无 Stepper/Wizard）。 */
export interface WorkspaceSnapshot {
  session: SessionView
  timeline: MessageView[]
  artifact: ArtifactSnapshot
  evidence: Array<Record<string, unknown>>
  available_actions: ActionView[]
  job_summary: JobSummaryView[]
  basic_mode: boolean
}

/** SSE 帧（`wrapped` 形态，见 8100 `app/core/sse.py`）。 */
export interface StreamEnvelope {
  seq: number
  event_id?: string
  event_type?: string
  payload?: Record<string, unknown>
  created_at?: string
}

/** 连接/呈现相位 —— 与视觉状态一一对应（PARTIAL 不得显示完成色）。 */
export type WorkspacePhase =
  | 'loading'
  | 'empty'
  | 'ready'
  | 'partial'
  | 'reconnecting'
  | 'forbidden'
  | 'conflict'
  | 'stale'
  | 'error'

/** 错误分类（由 HTTP 状态 + 业务 code 归一化）。 */
export type ErrorKind =
  | 'unauthenticated'
  | 'forbidden'
  | 'conflict'
  | 'not_found'
  | 'gone'
  | 'rate_limited'
  | 'error'
  | 'network'

export interface NormalizedError {
  kind: ErrorKind
  status: number | null
  code: number | null
  /** 稳定 UI 文案走 copy key；业务文案由服务端下发。 */
  copyKey: string
  detail?: string
  /**
   * 服务端 `x-request-id` / 信封 `request_id`（WP-K：错误页要显示**真实** request id，
   * 过去错误页把 HTTP 状态码当成 request_id 渲染出 "request_id: 404" —— 那是假的）。
   */
  requestId?: string | null
}
