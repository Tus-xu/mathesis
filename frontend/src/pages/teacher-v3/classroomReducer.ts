/**
 * Classroom 双端状态投影 reducer（G7：前端只是 projection）。
 * 状态一律来自服务端 snapshot 与 SSE 事件载荷——活动、名单、统计均为**整体替换**，
 * 严禁 `studentsAnswered++` 类本地推算；未知事件容忍跳过；seq 回退/重复幂等跳过。
 * 事件语义：02-ARCHITECTURE §12.4（SSE 首事件 snapshot，后续 durable event*；
 * 断线重连后服务端重发 snapshot 整体重置 + Last-Event-ID 补拉）。
 * 纯函数可单测；M2-A 由 ClassroomView / 学生 H5 接线，M1 只交付 + 测试（IFC-002）。
 */
import type { V3ClassroomActivity, V3ClassroomEvent, V3ClassroomQuestionBrief, V3ClassroomSession, V3ClassroomSnapshot } from '@/types/teacherV3'

/** 教师端投影状态 */
export interface TeacherClassroomState {
  session: V3ClassroomSession
  questions: Record<string, V3ClassroomQuestionBrief>
  seq: number
  summary: { stats: Record<string, unknown>; insight?: string } | null
}

/** 学生端投影状态（学生视角：revealed 前 answer 不落本地；我的作答只认服务端回执） */
export interface StudentClassroomState {
  sessionId: string
  participantId: string
  sessionStatus: V3ClassroomSession['status']
  joinCode: string
  className: string
  activities: V3ClassroomActivity[]
  questions: Record<string, V3ClassroomQuestionBrief>
  /** 已被服务端接受的作答活动 id（submitResponse 幂等回执 / response_submitted 事件载荷） */
  myAnsweredActivityIds: string[]
  seq: number
  summary: { stats: Record<string, unknown>; insight?: string } | null
}

function indexQuestions(list?: V3ClassroomQuestionBrief[]): Record<string, V3ClassroomQuestionBrief> {
  const out: Record<string, V3ClassroomQuestionBrief> = {}
  for (const q of list || []) out[q.question_id] = q
  return out
}

function upsertActivity(list: V3ClassroomActivity[], activity: V3ClassroomActivity): V3ClassroomActivity[] {
  const idx = list.findIndex((a) => a.activity_id === activity.activity_id)
  if (idx === -1) return [...list, activity].sort((a, b) => a.ord - b.ord)
  const next = [...list]
  next[idx] = activity
  return next
}

function patchActivity(list: V3ClassroomActivity[], activityId: string, patch: Partial<V3ClassroomActivity>): V3ClassroomActivity[] {
  const idx = list.findIndex((a) => a.activity_id === activityId)
  if (idx === -1) return list
  const next = [...list]
  next[idx] = { ...next[idx], ...patch }
  return next
}

/** SSE 消息统一形状：v3Sse onEvent 的 (event, data) */
export interface V3ClassroomSseMessage {
  event: string
  data: any
}

function stale(state: { seq: number }, msg: V3ClassroomSseMessage): boolean {
  // durable 事件载荷带 seq；重复投递/乱序回放直接忽略（幂等）
  const seq = typeof msg.data?.seq === 'number' ? msg.data.seq : null
  return seq !== null && seq <= state.seq
}

function bumpSeq(state: { seq: number }, msg: V3ClassroomSseMessage): number {
  const seq = typeof msg.data?.seq === 'number' ? msg.data.seq : null
  return seq !== null ? Math.max(state.seq, seq) : state.seq
}

/* ==================== 教师端 ==================== */

export function teacherStateFromSnapshot(snap: V3ClassroomSnapshot): TeacherClassroomState {
  return {
    session: snap.session,
    questions: indexQuestions(snap.questions),
    seq: snap.seq,
    summary: snap.summary ?? null,
  }
}

function applyDurableTeacher(state: TeacherClassroomState, ev: V3ClassroomEvent): TeacherClassroomState {
  const p = ev.payload || {}
  switch (ev.event_type) {
    case 'session_opened':
      return { ...state, session: { ...state.session, ...p.session, status: p.session?.status ?? 'open' }, seq: Math.max(state.seq, ev.seq) }
    case 'participant_joined': {
      const pt = p.participant
      if (!pt || state.session.participants.some((x) => x.participant_id === pt.participant_id)) {
        return { ...state, seq: Math.max(state.seq, ev.seq) }
      }
      return { ...state, session: { ...state.session, participants: [...state.session.participants, pt] }, seq: Math.max(state.seq, ev.seq) }
    }
    case 'activity_pushed':
      return p.activity
        ? {
            ...state,
            session: { ...state.session, activities: upsertActivity(state.session.activities, p.activity) },
            questions: p.question ? { ...state.questions, [p.question.question_id]: p.question } : state.questions,
            seq: Math.max(state.seq, ev.seq),
          }
        : state
    case 'response_submitted':
      // 统计只来自服务端聚合载荷（answered/distribution/correct_rate），不本地累计
      return p.activity
        ? { ...state, session: { ...state.session, activities: upsertActivity(state.session.activities, p.activity) }, seq: Math.max(state.seq, ev.seq) }
        : p.activity_id && p.stats
          ? { ...state, session: { ...state.session, activities: patchActivity(state.session.activities, p.activity_id, { stats: p.stats }) }, seq: Math.max(state.seq, ev.seq) }
          : { ...state, seq: Math.max(state.seq, ev.seq) }
    case 'activity_locked':
    case 'activity_revealed':
      return {
        ...state,
        session: { ...state.session, activities: p.activity ? upsertActivity(state.session.activities, p.activity) : state.session.activities },
        questions: p.question ? { ...state.questions, [p.question.question_id]: p.question } : state.questions,
        seq: Math.max(state.seq, ev.seq),
      }
    case 'ai_generation_updated':
      return p.activity_id
        ? { ...state, session: { ...state.session, activities: patchActivity(state.session.activities, p.activity_id, { ai_generation: p.ai_generation }) }, seq: Math.max(state.seq, ev.seq) }
        : state
    case 'session_ended':
      return { ...state, session: { ...state.session, status: 'ended', ended_at: p.ended_at }, seq: Math.max(state.seq, ev.seq) }
    case 'summary_ready':
      return { ...state, summary: p.summary ?? null, seq: Math.max(state.seq, ev.seq) }
    default:
      return state // 未知事件类型容忍跳过（协议前向兼容）
  }
}

export function applyTeacherEvent(state: TeacherClassroomState, msg: V3ClassroomSseMessage): TeacherClassroomState {
  // 重连恢复协议（§12.4）：通道重发 snapshot → 整体重置
  if (msg.event === 'snapshot') {
    const snap = msg.data as V3ClassroomSnapshot
    return snap?.session ? teacherStateFromSnapshot(snap) : state
  }
  // durable 事件统一包装为 {event:'event', data:V3ClassroomEvent}
  if (msg.event === 'event') {
    const ev = msg.data as V3ClassroomEvent
    if (!ev || typeof ev.seq !== 'number') return state
    if (ev.seq <= state.seq) return state
    return applyDurableTeacher(state, ev)
  }
  // live 通道（PubSub 投影，允许丢失；载荷不带 seq 不做幂等）
  if (msg.event === 'presence' && typeof msg.data?.online_count === 'number') {
    return { ...state, session: { ...state.session, online_count: msg.data.online_count } }
  }
  return state
}

/* ==================== 学生端（H5，学生视角投影） ==================== */

export function studentStateFromSnapshot(snap: V3ClassroomSnapshot, participantId: string): StudentClassroomState {
  return {
    sessionId: snap.session.session_id,
    participantId,
    sessionStatus: snap.session.status,
    joinCode: snap.session.join_code,
    className: snap.session.class_name || '',
    activities: snap.session.activities,
    questions: indexQuestions(snap.questions),
    myAnsweredActivityIds: (snap.session.activities || [])
      .filter((a) => a.stats?.my_submitted)
      .map((a) => a.activity_id),
    seq: snap.seq,
    summary: snap.summary ?? null,
  }
}

function applyDurableStudent(state: StudentClassroomState, ev: V3ClassroomEvent): StudentClassroomState {
  const p = ev.payload || {}
  switch (ev.event_type) {
    case 'session_opened':
      return { ...state, sessionStatus: p.session?.status ?? 'open', seq: ev.seq }
    case 'activity_pushed':
      return p.activity
        ? {
            ...state,
            activities: upsertActivity(state.activities, p.activity),
            questions: p.question ? { ...state.questions, [p.question.question_id]: p.question } : state.questions,
            seq: ev.seq,
          }
        : state
    case 'response_submitted': {
      const mine = p.participant_id === state.participantId
      const base = p.activity
        ? { ...state, activities: upsertActivity(state.activities, p.activity) }
        : p.activity_id && p.stats
          ? { ...state, activities: patchActivity(state.activities, p.activity_id, { stats: p.stats }) }
          : state
      return mine && !base.myAnsweredActivityIds.includes(p.activity_id)
        ? { ...base, myAnsweredActivityIds: [...base.myAnsweredActivityIds, p.activity_id], seq: ev.seq }
        : { ...base, seq: ev.seq }
    }
    case 'activity_locked':
    case 'activity_revealed':
      return {
        ...state,
        activities: p.activity ? upsertActivity(state.activities, p.activity) : state.activities,
        questions: p.question ? { ...state.questions, [p.question.question_id]: p.question } : state.questions,
        seq: ev.seq,
      }
    case 'ai_generation_updated':
      return p.activity_id
        ? { ...state, activities: patchActivity(state.activities, p.activity_id, { ai_generation: p.ai_generation }), seq: ev.seq }
        : state
    case 'session_ended':
      return { ...state, sessionStatus: 'ended', seq: ev.seq }
    case 'summary_ready':
      return { ...state, summary: p.summary ?? null, seq: ev.seq }
    default:
      return state
  }
}

export function applyStudentEvent(state: StudentClassroomState, msg: V3ClassroomSseMessage): StudentClassroomState {
  if (msg.event === 'snapshot') {
    const snap = msg.data as V3ClassroomSnapshot
    return snap?.session ? studentStateFromSnapshot(snap, state.participantId) : state
  }
  if (msg.event === 'event') {
    const ev = msg.data as V3ClassroomEvent
    if (!ev || typeof ev.seq !== 'number') return state
    if (ev.seq <= state.seq) return state
    return applyDurableStudent(state, ev)
  }
  return state
}
