/**
 * 8000 M3 教师域客户端（tel PLAN §4.3 / D1 直调通道）
 *
 * - baseURL 复用 client.js 的 `/api` 代理与单 token（authHeaders / 401 刷新 / 幂等键），
 *   不新建鉴权协议（AGENTS.md §22）；与 teacherV3.ts（8100 `/api/teacher-v3/*`）并存不合并。
 * - 信封 `{code,message,data}`，code===0 成功；ApiError.code 为业务错误码（40301 role_denied
 *   统一跳提示页，与 teacher/client.ts 同纪律）。
 * - 契约事实源（本会话读码核对）：
 *   classes：services/api/app/domains/classroom/router.py:57-430
 *   teacher M3：services/api/app/domains/teacher/router.py:406-672
 *   schemas：services/api/app/domains/teacher/schemas.py（extra=forbid）
 */

import { ApiError, api } from '@/api/client'
import { redirectTeacherDenied } from './teacher/client'

/* ============ 类型（对齐 8000 序列化字段；仅收窄前端消费面） ============ */

/** GET /api/classes/mine 元素（classroom/router.py:183-240） */
export interface ApiClassItem {
  id: string
  name: string
  grade: string
  myRole: string
  confirmed: boolean
  /** 后端按 scope.py 同口径计算的能力位（旧响应无此字段时前端回退 myRole 判断）。 */
  canTeach?: boolean
  isOwner?: boolean
  inviteCode: string | null
  createdAt: string | null
  /** tel A1：confirmed 学生成员数 */
  students: number
}

/** GET /api/classes/{id}/members 元素（classroom/router.py:258-330） */
export interface ApiMemberItem {
  id: string
  userId: string
  memberRole: string
  confirmed: boolean
  joinVia: string
  nicknameInClass: string
  nickname: string
  joinedAt: string | null
}

/** tel D2/A3：任务类型扩展 */
export type ApiAssignmentType = 'quiz' | 'pre_class' | 'homework'

/** _serialize_assignment（assessment.py:308-319） */
export interface ApiAssignment {
  assignment_id: string
  client_assignment_id: string
  class_id: string
  title: string
  type: ApiAssignmentType
  status: 'draft' | 'published' | 'closed' | 'archived'
  deadline: string | null
  created_at: string | null
  replayed?: boolean
}

/** quiz_set artifact 内的题项（assessment.py:_item_from_row） */
export interface ApiQuizSetItem {
  item_no: number
  q_type: 'choice' | 'blank' | 'solution'
  question_text: string
  options: Record<string, string> | null
  answer: string
  analysis: string | null
  kp_code: string | null
  difficulty: string
  max_score?: number | null
}

/** _serialize_artifact（artifacts.py:46-67） */
export interface ApiQuizSetArtifact {
  artifact_id: string
  artifact_type: string
  scene: string
  class_id: string | null
  owner_id: string
  status: 'draft' | 'confirmed' | 'published' | 'archived'
  version: number
  content: {
    knowledge_points?: string[]
    count?: number
    items?: ApiQuizSetItem[]
    insufficient?: boolean
    question_type_distribution?: Record<string, number>
  }
  source_refs: string[]
  validation: Record<string, unknown>
  warnings: string[]
  degraded: boolean
  engine: string
  confirmed_by: string | null
  confirmed_at: string | null
  created_at: string | null
  updated_at: string | null
}

/** 批改队列元素（grading.py:_workspace_queue_state；tel F3 起含 auto_graded=系统自动批阅） */
export interface ApiGradingQueueItem {
  submission_item_id: string
  anonymous_label: string
  state: 'ungraded' | 'review' | 'auto_graded' | 'confirmed'
  manual_review?: boolean
}

/** GET /teacher/grading/queue 行（_serialize_queue_item：unprocessed|low_confidence|confirmed；
 *  tel F3：选择/判断题系统自动判分 → suggestion_score 直接下发系统分快照） */
export interface ApiGradingQueueRow {
  submission_item_id: string
  student_label: string
  status: 'unprocessed' | 'low_confidence' | 'confirmed'
  confidence: number
  suggestion_score: number | null
  teacher_final_score: number | null
}

/** grading_workspace 投影（grading.py:626-889） */
export interface ApiGradingWorkspace {
  context: {
    class: { class_id: string; label: string | null } | null
    assignment: { assignment_id: string; title: string } | null
    question: {
      item_no: number
      question_text: string | null
      q_type: string | null
      options: Record<string, string> | null
      image?: string[]
      max_score: number | null
    } | null
    filters: { status: string }
    progress: { total: number; confirmed: number; remaining: number }
  }
  available_context: {
    assignments: { assignment_id: string; title: string }[]
    questions: { item_no: number; label: string; question_text: string | null }[]
  }
  queue: ApiGradingQueueItem[]
  selected: {
    submission_item_id: string
    work: { original_answer: string; file_id: string | null; attachments?: { file_id?: string }[] }
    scoring: {
      max_score: number | null
      rubric_status: string
      rubric_items: { id: string; criterion: string; points: number; evidence_hint: string }[]
      standard_answer: string | null
      answer_analysis: string | null
      fallback_standard: string
    }
    suggestion: {
      suggestion_id: string | null
      version: number
      proposed_score: number | null
      review_needed: boolean
      evidence: { kind: string; text: string }[]
    }
    confirmed_decision: { final_score: number | null; feedback: string | null; decision: string | null } | null
  } | null
  navigation: { previous_id: string | null; next_ungraded_id: string | null }
}

/** grading/{item} 详情（grading.py:535-624，对齐 GradingSuggestion 契约） */
export interface ApiGradingDetail {
  submission_item_id: string
  student_label: string
  status: string
  confidence: number
  suggestion_score: number | null
  teacher_final_score: number | null
  original_answer: string
  file_id: string | null
  attachments: unknown[]
  scoring_standard: string
  assignment_title: string | null
  question_text: string | null
  question_type: string | null
  options: Record<string, string> | null
  standard_answer: string | null
  answer_analysis: string | null
  suggestion: {
    suggestion_id: string
    submission_item_id: string
    student_label: string
    original_answer: string
    file_id: string | null
    attachments: unknown[]
    scoring_standard: string
    suggestion_score: number | null
    confidence: number
    evidence: string
    review_needed: boolean
    teacher_final_score: number | null
    teacher_feedback: string | null
    decision: string
    version: number
  }
}

/** tel A4 roster 学生行（insights.py:418-482） */
export interface ApiRosterStudent {
  user_id: string
  nickname: string
  confirmed: boolean
  submission_count: number
  pending_count: number
  avg_mastery: number | null
  weak_kps: { code: string; name: string; mastery: number }[]
}

/** tel A5 班级学情聚合（insights.py:485-660） */
export interface ApiLearningOverview {
  class_id: string
  window_days: number
  submission_trend: { date: string; submitted: number; total: number }[]
  kp_heat: { code: string; name: string; wrong_rate: number }[]
  pending_review_count: number
  avg_mastery: number | null
  error_clusters: { kp_code: string; label: string; count: number }[]
}

/** 既有 A-insights（insights.py:_serialize） */
export interface ApiActionableInsight {
  insight_id: string
  kind: string
  summary: string
  evidence: string
  data_window: { from: string; to: string }
  recommended_actions: string[]
  confidence: number
  applied: boolean
}

/** 讲评要点（grading.py:grading_review_insights） */
export interface ApiGradingReviewInsights {
  assignment_id: string | null
  title: string
  review_rate: number
  top_questions: {
    item_no?: number
    question_text: string
    kp_code?: string | null
    wrong_count: number
    graded_count: number
    typical?: { submission_item_id?: string; answer?: string; file_id?: string | null }[]
  }[]
}

/** 8100 R19 备课画像重算请求（teacher_prep/router.py:87-97） */
export interface ApiRecalculateProfileBody {
  session_id: string
  class_id: string
  assignment_id: string
  idempotency_key?: string
}

/* ---------- 班级课堂课件（8000 /api/teacher/classroom-coursewares*，Phase D 契约） ---------- */

/** 发布到班级的课件（TeachingArtifact scene=classroom_courseware 投影）。 */
export interface ApiCoursewareItem {
  courseware_id: string
  title: string
  class_id: string
  class_name: string
  teacher_name: string
  page_count: number
  /** draft=准备中（讲稿/练习后台生成）· published=已发布 · archived=已下架 */
  status: string
  /** 已打开学生数（真实 ClassroomSession 计数） */
  opened: number
  students_total: number
  /** 0–100 */
  avg_progress_pct: number
  /** 练习正确率（后端可能给 0–1 比值或 0–100 百分数；展示层统一归一化） */
  practice_accuracy: number | null
  published_at: string | null
  updated_at: string | null
}

/** 发布准备进度（详情页真实准备状态；字段随后端准备任务填充）。 */
export interface ApiCoursewarePreparation {
  done?: number
  total?: number
  stage?: string
  narration_status?: string
  practice_status?: string
  [key: string]: unknown
}

/** GET /teacher/classroom-coursewares/{id} */
export interface ApiCoursewareDetail {
  courseware_id: string
  title: string
  class_id: string
  class_name?: string
  page_count?: number
  status: string
  warnings: string[]
  degraded?: boolean
  preparation?: ApiCoursewarePreparation | null
  published_at?: string | null
  updated_at?: string | null
}

/** 每生真实学情（来自 ClassroomSession.progress / practice_stats）。 */
export interface ApiCoursewareMonitorStudent {
  user_id: string
  name: string
  opened: boolean
  /** 当前页（后端 slide_index 派生；null=未打开） */
  current_page: number | null
  pages_seen: number
  self_check_ok: number
  self_check_again: number
  practice_answered: number
  practice_correct: number
  last_active: string | null
}

/** GET /teacher/classroom-coursewares/{id}/monitor */
export interface ApiCoursewareMonitor {
  students_total: number
  opened: number
  completed: number
  avg_progress_pct: number
  practice_accuracy: number | null
  page_dropoff: { page: number; count: number }[]
  students: ApiCoursewareMonitorStudent[]
}

function guard(e: unknown): never {
  if (e instanceof ApiError && e.code === 40301) redirectTeacherDenied()
  throw e
}

/** 低层请求（client.js api.raw 无泛型签名——此处包一层并解信封；ApiError 原样上抛）。 */
async function raw<T>(method: string, path: string, opts: Record<string, unknown> = {}): Promise<T> {
  const r = (await api.raw(method, path, opts as Parameters<typeof api.raw>[2])) as { data: T }
  return r.data
}

/* ============ 客户端 ============ */

export const api8000 = {
  /* ---------- 班级（8000 /api/classes*，身份与成员唯一权威） ---------- */
  classes: {
    list: () => raw<{ items: ApiClassItem[]; total: number }>('GET', '/classes/mine').catch(guard),
    create: (body: { name: string; grade?: string }) =>
      raw<{ id: string; name: string; inviteCode: string; grade: string }>('POST', '/classes', { body }).catch(guard),
    members: (classId: string) =>
      raw<{ items: ApiMemberItem[]; total: number }>('GET', `/classes/${classId}/members`).catch(guard),
    /** 教师确认/取消确认成员（pending → confirmed 才进任务/学情口径，D3） */
    setMemberConfirmed: (classId: string, userId: string, confirmed: boolean) =>
      raw<{ confirmed: boolean }>('PATCH', `/classes/${classId}/members/${userId}`, { body: { confirmed } }).catch(guard),
    resetInviteCode: (classId: string) =>
      raw<{ inviteCode: string }>('POST', `/classes/${classId}/invite-code/reset`).catch(guard),
  },

  /* ---------- 组卷 / 产物（quiz_set draft → 教师显式 confirm → 建 Assignment） ---------- */
  quizzes: {
    /** 四步编排第 1 步：题库供给组卷（题量不足时 degraded=true + warnings，前端如实呈现） */
    generate: (body: {
      class_id: string
      knowledge_points: string[]
      count: number
      question_types: { choice: number; blank: number; text: number }
      difficulty: { easy: number; medium: number; hard: number }
      exclude_hashes?: string[]
    }) => raw<ApiQuizSetArtifact>('POST', '/teacher/quizzes/generate', { body }).catch(guard),
  },
  artifacts: {
    get: (artifactId: string) =>
      raw<ApiQuizSetArtifact>('GET', `/teacher/artifacts/${artifactId}`).catch(guard),
    /** 四步编排第 2 步：教师显式确认题集（无静默；schema ArtifactActionRequest） */
    confirm: async (artifactId: string, clientRequestId: string, idempotencyKey?: string) => {
      // tel E2E 修复：8000 confirm 返回 {artifact, replayed}（artifacts.py:277），前端 publishFlow
      // 期望裸 artifact（.status/.artifact_id）——此处解包对齐（GET /artifacts/{id} 为裸形状）。
      const r = await raw<{ artifact: ApiQuizSetArtifact; replayed?: boolean }>(
        'POST', `/teacher/artifacts/${artifactId}/confirm`,
        { body: { client_request_id: clientRequestId }, idempotencyKey },
      ).catch(guard)
      const art = (r.artifact ?? (r as unknown as ApiQuizSetArtifact)) as ApiQuizSetArtifact
      return Object.assign(art, { replayed: r.replayed })
    },
  },

  /* ---------- 作业（draft → publish；client_assignment_id 为前端稳定幂等键） ---------- */
  assignments: {
    list: (query?: { class_id?: string; status?: string }) =>
      raw<{ assignments: ApiAssignment[] }>('GET', '/teacher/assignments', { query }).catch(guard),
    /** 四步编排第 3 步：创建 draft（artifact 必须 confirmed，否则 422 confirmation_required） */
    create: (body: {
      class_id: string
      title: string
      artifact_id: string
      client_assignment_id: string
      deadline?: string
      type?: ApiAssignmentType
    }) => raw<ApiAssignment>('POST', '/teacher/assignments', { body }).catch(guard),
    /** 四步编排第 4 步：发布 → 学生任务中心可见 */
    publish: (assignmentId: string, clientRequestId: string, idempotencyKey?: string) =>
      raw<ApiAssignment>('POST', `/teacher/assignments/${assignmentId}/publish`, {
        body: { client_request_id: clientRequestId },
        idempotencyKey,
      }).catch(guard),
    close: (assignmentId: string, clientRequestId: string, idempotencyKey?: string) =>
      raw<ApiAssignment>('POST', `/teacher/assignments/${assignmentId}/close`, {
        body: { client_request_id: clientRequestId },
        idempotencyKey,
      }).catch(guard),
  },

  /* ---------- 批改工作台（题聚焦投影 + 原图 + confirm 唯一写分入口） ---------- */
  grading: {
    queue: (classId?: string) =>
      raw<{ queue: ApiGradingQueueRow[] }>('GET', '/teacher/grading/queue', { query: classId ? { class_id: classId } : undefined }).catch(guard),
    workspace: (query: { class_id?: string; assignment_id?: string; item_no?: number; status?: string; submission_item_id?: string }) =>
      raw<ApiGradingWorkspace>('GET', '/teacher/grading/workspace', { query }).catch(guard),
    detail: (submissionItemId: string) =>
      raw<ApiGradingDetail>('GET', `/teacher/grading/${submissionItemId}`).catch(guard),
    /** 学生手写原图：鉴权字节流，不出域；前端 blob 化后本地展示（无公开 URL） */
    fetchFileBlob: (submissionItemId: string) => api.download(`/teacher/grading/${submissionItemId}/file`).catch(guard),
    /** tel D9/A6：教师显式触发 AI 建议分（仅参考；confirm 仍是唯一正式分） */
    suggest: (submissionItemId: string, classId: string, clientRequestId: string) =>
      raw<ApiGradingDetail['suggestion']>('POST', `/teacher/grading/${submissionItemId}/suggest`, {
        body: { class_id: classId, client_request_id: clientRequestId },
      }).catch(guard),
    /** 教师终审确认：唯一正式分入口（suggestion_id + decision + final_score） */
    confirm: (submissionItemId: string, body: {
      suggestion_id: string
      decision: 'accept' | 'override'
      final_score?: number
      teacher_feedback?: string
      version?: number
    }) => raw<ApiGradingDetail['suggestion']>('POST', `/teacher/grading/${submissionItemId}/confirm`, { body }).catch(guard),
    /** 批量确认：逐项独立校验，单项失败不掩盖其他项（router.py:610-650） */
    batchConfirm: (items: {
      submission_item_id: string
      suggestion_id: string
      decision: 'accept' | 'override'
      final_score?: number
      teacher_feedback?: string
      version?: number
    }[]) => raw<{ results: ({ ok: true; submission_item_id: string } | { ok: false; submission_item_id: string; code: number; message: string })[] }>('POST', '/teacher/grading/batch-confirm', { body: { items } }).catch(guard),
    review: (submissionItemId: string, state: 'pending' | 'cleared', note: string, clientRequestId: string) =>
      raw<Record<string, unknown>>('POST', `/teacher/grading/${submissionItemId}/review`, {
        body: { state, note, client_request_id: clientRequestId },
      }).catch(guard),
    /** 批后讲评要点：最近一份已批作业的 Top 题 */
    insights: (classId: string) =>
      raw<ApiGradingReviewInsights>('GET', '/teacher/grading/insights', { query: { class_id: classId } }).catch(guard),
  },

  /* ---------- 班级学情（tel D5/A4/A5/A-既有，纯确定性 SQL 无 LLM） ---------- */
  insights: {
    roster: (classId: string) =>
      raw<{ class_id: string; students: ApiRosterStudent[] }>('GET', `/teacher/classes/${classId}/roster`).catch(guard),
    learningOverview: (classId: string, windowDays?: number) =>
      raw<ApiLearningOverview>('GET', `/teacher/classes/${classId}/learning-overview`, { query: windowDays ? { window_days: windowDays } : undefined }).catch(guard),
    classInsights: (classId: string, actionable = true) =>
      raw<{ insights: ApiActionableInsight[] }>('GET', `/teacher/classes/${classId}/insights`, { query: { actionable } }).catch(guard),
  },

  /* ---------- 题库知识点目录（组卷选题源；student_router.py:1109-1127，题库有题的章节） ---------- */
  kpCatalog: () =>
    raw<{ items: { code: string; name: string; bank_count: number }[] }>('GET', '/student/practice/kp-catalog').catch(guard),

  /* ---------- 班级课堂课件（备课 PPT 发布 → 学生 AI 课堂 → 教师真实学情） ----------
   * 发布动作本身在 8100（orchestration 打包 deck 后调 8000），前端不走本客户端；
   * 本组只读列表/详情/学情 + 软下架，供「课堂 · 双师课堂」消费。 */
  coursewares: {
    list: (query?: { class_id?: string; deck_id?: string }) =>
      raw<{ items: ApiCoursewareItem[] }>('GET', '/teacher/classroom-coursewares', { query }).catch(guard),
    get: (coursewareId: string) =>
      raw<ApiCoursewareDetail>('GET', `/teacher/classroom-coursewares/${encodeURIComponent(coursewareId)}`).catch(guard),
    monitor: (coursewareId: string) =>
      raw<ApiCoursewareMonitor>('GET', `/teacher/classroom-coursewares/${encodeURIComponent(coursewareId)}/monitor`).catch(guard),
    /** 软下架：学生不再看到；已打开学生的历史会话保留（不删数据）。 */
    archive: (coursewareId: string) =>
      raw<{ courseware_id?: string; status?: string }>('POST', `/teacher/classroom-coursewares/${encodeURIComponent(coursewareId)}/archive`).catch(guard),
  },
}

/** 生成稳定幂等键（发布四步编排 client_assignment_id / client_request_id 前端生成） */
export function telKey(prefix: string): string {
  const rand =
    typeof crypto !== 'undefined' && 'randomUUID' in crypto
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
  return `${prefix}-${rand}`
}
