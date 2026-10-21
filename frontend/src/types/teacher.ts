/**
 * M3 教师端统一类型（API v2.1 契约为准，详见 D:\M2开发\M3教师端\M3_API接口文档_v2.1.md）
 * 仅供后续 teacher 模块复用，禁止把 Provider/模型/密钥/工作流名落到此处。
 */

/** 同时用于前端静态消费 */
export const TEACHER_ERROR_CODES = {
  VALIDATION: 40001,
  UNAUTHORIZED: 40100,
  ROLE_DENIED: 40301,
  CLASS_SCOPE_DENIED: 40302,
  NOT_FOUND: 40400,
  VERSION_CONFLICT: 40901,
  DUPLICATE_REQUEST: 40902,
  CONFIRMATION_REQUIRED: 42210,
  CAPABILITY_DEGRADED: 50310,
  CAPABILITY_UNAVAILABLE: 50311,
} as const

export type StableErrorCode =
  | 40001
  | 40100
  | 40301
  | 40302
  | 40400
  | 40901
  | 40902
  | 42210
  | 50310
  | 50311

export interface ApiEnvelope<T> {
  code: number
  message: string
  data: T
  request_id?: string
}

export interface ApiErrorBody {
  code: number
  message: string
  data?: Record<string, unknown> | null
  request_id?: string
}

export type TeacherScene =
  | 'teacher.today'
  | 'teacher.prep'
  | 'teacher.assessment'
  | 'teacher.grading'
  | 'teacher.classroom'
  | 'teacher.class.insights'
  | 'teacher.resources'

export type ArtifactStatus = 'draft' | 'confirmed' | 'published' | 'archived'
export type ArtifactType =
  | 'lesson_plan'
  | 'slides'
  | 'quiz_set'
  | 'grading_suggestion'
  | 'explanation'
  | 'course'
  | 'document'

export interface TeacherArtifact {
  artifact_id: string
  artifact_type: ArtifactType
  scene: TeacherScene
  class_id: string | null
  owner_id: string
  status: ArtifactStatus
  version: number
  content: Record<string, unknown>
  source_refs: SourceRef[]
  warnings: string[]
  degraded: boolean
  /** 后端对产物生成/校验的结构化元数据；题集包含请求与可用题量。 */
  validation?: ArtifactValidation
  engine?: string
  confirmed_by?: string | null
  confirmed_at?: string | null
  created_at: string
  updated_at: string
}

export interface SourceRef {
  kind: string
  ref: string
  title?: string
  page?: number | string
  snippet?: string
}

export type TaskStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled'

export interface TeacherTask {
  task_id: string
  capability: string
  status: TaskStatus
  progress: number
  stage?: string
  artifact_id: string | null
  error_code: StableErrorCode | null
  created_at?: string
  updated_at?: string
}

export interface ActionableInsight {
  insight_id: string
  kind: string
  summary: string
  evidence: string
  data_window: { from: string; to: string }
  recommended_actions: string[]
  confidence?: number
  applied?: boolean
  /** 错题聚类附带的知识点（供「布置变式练习」蓝图预填；可选附加字段） */
  kp_code?: string
}

export interface TeacherTodayData {
  next_lesson: {
    class_id: string
    topic: string
    class_name?: string
    starts_at: string
    /** 备课完成度（0-100），供"任务优先"首页展示 */
    prep_completion?: number
    /** 本节课还缺的材料/环节，供首页生成待办 */
    missing_items?: string[]
    /** 上课时长（分钟），供倒计时与节奏提示 */
    duration_minutes?: number
    /** 下节课标识（GP-2 洞察深链），mock 未提供时省略 */
    lesson_id?: string
  } | null
  grading_queue: { count: number; action: string }
  deadlines: Deadline[]
  actionable_insights: ActionableInsight[]
  degraded: boolean
}

export interface Deadline {
  id: string
  kind: string
  title: string
  due_at: string
}

export interface LessonPlan {
  topic: string
  objectives: string[]
  sections: LessonPlanSection[]
  materials: string[]
  assignment: string
}

export interface LessonPlanSection {
  title: string
  duration_minutes?: number
  activities: string[]
}

/** RD-1（modules/lesson-artifact.md）：教案产物结构化环节。全部字段 additive/optional；
 *  旧 payload（仅 description）必须继续可渲染，禁止迁移破坏。 */
export type LessonSegmentKind = 'import' | 'concept' | 'example' | 'practice' | 'check' | 'summary' | 'intervention'
export type LessonSegmentSource = 'template' | 'adapted' | 'ai_suggested' | 'teacher_edited'

export interface LessonMaterialRef {
  resource_id: string
  name: string
  usage?: string
}

/** 对应真实教案「环节 ×(教师活动|学生活动|预计分钟|设计意图|教学评价)」环节表 */
export interface LessonSegment {
  id: string
  title: string
  duration_min?: number
  kind: LessonSegmentKind
  /** 本环节学习目标 */
  learning_objective?: string
  /** 教师活动（讲授/提问/演示） */
  teacher_action?: string
  /** 学生活动（独立作答/讨论/演板） */
  student_action?: string
  /** 核心问题（一句话） */
  core_question?: string
  /** 主内容（富文本，含 LaTeX；description 旧字段映射于此，读旧写新） */
  content: string
  /** 素材引用（可跳资源库） */
  materials?: LessonMaterialRef[]
  /** 检查理解方式 */
  assessment_check?: string
  /** 依据的 insight_id（支撑「AI 有依据」） */
  linked_insights?: string[]
  source?: LessonSegmentSource
  /** AI 批量建议不得触碰（同 PPT 锁页语义） */
  locked?: boolean
}

export interface LessonSegmentContent {
  topic?: string
  objectives?: string[]
  segments: LessonSegment[]
  materials?: string[]
  assignment?: string
}

export interface QuizQuestion {
  item_no: number
  q_type: 'choice' | 'blank' | 'text' | 'solution'
  difficulty: 'easy' | 'medium' | 'hard'
  kp_code?: string
  kp_name?: string
  question_text: string
  options?: string[] | Record<string, string> | null
  answer?: string | null
  answer_analysis?: string | null
  /** 真题来源（question_bank.source，如「2023新课标I卷」）；本地兜底题缺省 */
  source?: string
  /** 题库溯源引用，如 qb:<id> */
  source_ref?: string
  hash?: string
  /** Compatibility with legacy solution-shaped quiz payloads. */
  solution?: string | null
  analysis?: string | null
}

/** 教师备课生成请求；输入由备课页显式采集，不能由页面默认课题隐式补齐。 */
export interface LessonAdaptRequest {
  class_id: string
  topic: string
  requirements: string
  duration_minutes: number
}

export interface LessonTimelineItem {
  phase?: string
  minutes?: number
  activities?: string[]
}

export interface QuizSet {
  knowledge_points: string[]
  count: number
  difficulty: Record<string, number>
  items: QuizQuestion[]
  duplicated: number
  insufficient: boolean
}

export interface GradingSuggestion {
  suggestion_id: string
  submission_item_id: string
  student_label: string
  original_answer: string
  scoring_standard: string
  suggestion_score: number | null
  confidence: number
  evidence: string
  review_needed: boolean
  teacher_final_score: number | null
  teacher_feedback: string | null
  decision: 'draft' | 'accepted' | 'overridden'
  /** 建议记录版本（确认时乐观锁校验） */
  version?: number
  /** 幂等重放标记（后端 batch/confirm 重放时为 true） */
  replayed?: boolean
}

export type AssignmentStatus = 'draft' | 'published' | 'closed' | 'archived'

export interface Assignment {
  assignment_id: string
  client_assignment_id?: string
  class_id: string
  title: string
  quiz_set?: QuizSet
  status: AssignmentStatus
  version?: number
  created_at: string
  published_at?: string | null
}

export interface ClassroomModeState {
  enabled: boolean
  class_id: string
  lesson_id?: string | null
  ttl_seconds: number
  updated_at: string
  degraded: boolean
}

export interface VideoInsight {
  class_id?: string
  lesson_id?: string | null
  /** Defined only when an actual classroom/video event source supplies it. */
  participation?: Record<string, unknown>
  timeline_events?: unknown[]
  actionable_recommendations?: unknown[]
  reason?: string
  /** Legacy mock fields remain optional while the formal endpoint uses the fields above. */
  aggregate_engagement?: number | null
  segments?: VideoSegment[]
  actions?: ActionableInsight[]
  degraded: boolean
}

export interface VideoSegment {
  time: number
  event: string
  summary?: string
}
/** 课堂会话（§14 调研版：Session 语义，对标 SchoolAI Mission Control / 希沃授课助手） */
export interface ClassroomSessionSegment {
  title: string
  kind?: string
  duration_min?: number
}

export interface ClassroomSessionQuestion {
  question_id: string
  prompt: string
  options: string[]
  correct_index: number
  focus: string
  submitted: number
  /** 各选项人数，顺序与 options 对齐 */
  distribution: number[]
  correct_rate: number
  main_wrong_option: string
  ai_reminder: string
  pattern_similar: boolean
  variant: string
}

export interface ClassroomSessionState {
  class_id: string
  session_id: string | null
  topic: string
  room: string
  started_at: string
  status: 'idle' | 'active' | 'ended'
  connected_total: number
  current_segment: ClassroomSessionSegment | null
  last_question: ClassroomSessionQuestion | null
  degraded: boolean
}

/** 批后讲评建议（调研版「批完→最值得讲的 3 题」） */
export interface GradingReviewTypicalAnswer {
  user_id: string
  submission_item_id: string | null
  file_id: string | null
  answer_text: string
  attachment_count: number
}

export interface GradingReviewQuestion {
  item_no: number
  question_text: string
  kp_code: string | null
  wrong_count: number
  graded_count: number
  correct_ratio: number
  typical_answers: GradingReviewTypicalAnswer[]
  actions: { action: string; label: string }[]
}

export interface GradingReviewInsights {
  assignment_id: string | null
  title: string
  review_rate: number
  top_questions: GradingReviewQuestion[]
}


export interface TeacherResource {
  resource_id: string
  name: string
  resource_kind?: 'uploaded_file' | 'external_reference'
  external_url?: string | null
  provider?: string | null
  attribution?: string | null
  intended_use?: string | null
  file_type: string
  size_bytes: number
  status: 'uploading' | 'preprocessing' | 'ready' | 'understand' | 'failed' | 'cancelled'
  task_id?: string | null
  error?: string | null
  pages?: { page: number; text?: string; snippet?: string }[]
  slices?: { slice_id: string; text: string }[]
  summary?: string
  published?: boolean
  degraded?: boolean
  warnings?: string[]
  /** Extracted from a teacher-owned source. Never becomes a bank row before approval. */
  question_candidates?: ResourceQuestionCandidate[]
  download_url?: string
  created_at: string
}

export interface ResourceQuestionCandidate {
  candidate_id: string
  stem: string
  q_type: string
  answer?: string
  knowledge_points?: string[]
  analysis?: string
  review_status: 'pending_review' | 'approved'
}

/** Butler 场景输入：前端只提交业务上下文，不提交策略/工具/workflow 字段 */
export interface ButlerSceneInput {
  scene: TeacherScene
  classId?: string
  artifactId?: string
  userMessage: string
  clientRequestId: string
}
/** 批改队列项与详情 */
export interface GradingQueueItem {
  submission_item_id: string
  student_label: string
  status: 'unprocessed' | 'low_confidence' | 'confirmed'
  confidence: number
  suggestion_score: number | null
  teacher_final_score: number | null
}

export interface GradingDetail extends GradingQueueItem {
  original_answer: string
  file_id?: string | null
  scoring_standard: string
  assignment_title?: string | null
  /** Persisted quiz evidence returned by the teacher grading detail endpoint. */
  question_text?: string | null
  question_type?: string | null
  options?: Record<string, string> | string[] | null
  standard_answer?: string | null
  answer_analysis?: string | null
  suggestion: GradingSuggestion | null
}

export interface ArtifactValidation extends Record<string, unknown> {
  question_count?: number
  dedup?: boolean
  bank_count?: number
  requested_count?: number
  available_count?: number
}

export interface BatchConfirmResult {
  results: { submission_item_id: string; ok: boolean; error?: number }[]
  failed: number
}

export interface UploadTicket {
  resource_id: string
  task_id?: string
  upload_url?: string
  status: 'uploading' | 'preprocessing' | 'ready' | 'failed'
}
