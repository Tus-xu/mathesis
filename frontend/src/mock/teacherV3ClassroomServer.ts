/**
 * Classroom mock 域（M2-A · IFC-002）——教师/学生双端行为之镜（02-ARCHITECTURE §12）
 * 契约：三层状态机（session/activity/ai_generation）；responses 幂等 UNIQUE(activity_id,participant_id,attempt_no)；
 * SSE 双通道：snapshot 首事件 → durable event*（Last-Event-ID 重放补拉，§7.1/§12.4）；学生流为学生视角投影。
 * 门禁：教师端点要 mock 教师角色；join 无 JWT；学生端点用课堂作用域 token（§10）。
 * 本模块为 additive 新增：不改 teacherV3Server.ts 任何既有字段/事件/语义。
 */
import { V3_QUIZ_QUESTIONS } from './teacherV3Data'
import type { V3ClassroomActivity, V3ClassroomParticipant, V3ClassroomQuestionBrief, V3ClassroomSession, V3ClassroomSnapshot } from '@/types/teacherV3'

/* ---------- 响应壳与 SSE 基建（与 teacherV3Server 同构） ---------- */
function readBody(req: any): Promise<any> {
  return new Promise((resolve) => {
    let buf = ''
    req.on('data', (c: any) => { buf += c })
    req.on('end', () => { try { resolve(buf ? JSON.parse(buf) : {}) } catch { resolve({}) } })
  })
}
function ok(res: any, data: unknown) {
  if (res.headersSent || res.writableEnded) return
  res.setHeader('Content-Type', 'application/json')
  res.end(JSON.stringify({ code: 0, message: 'ok', data }))
}
function fail(res: any, status: number, code: number, message: string) {
  if (res.headersSent || res.writableEnded) return
  res.statusCode = status
  res.setHeader('Content-Type', 'application/json')
  res.end(JSON.stringify({ code, message, data: null }))
}
function sendSse(res: any, event: string, data: unknown) {
  if (res.writableEnded || res.destroyed) return
  res.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)
}
const isTeacher = (req: any) => (req.headers?.authorization || '').includes('mock-token-teacher-preview')

let _id = 9000
const uid = (p: string) => `${p}-${++_id}`
const nowIso = () => new Date().toISOString()

/* ---------- 内存态 ---------- */
interface DurableEvent { seq: number; event_id: string; event_type: string; payload: Record<string, any>; created_at: string }
interface Room {
  session: V3ClassroomSession
  questions: Record<string, V3ClassroomQuestionBrief>
  events: DurableEvent[]
  seq: number
  summary: { stats: Record<string, unknown>; insight?: string } | null
  responses: Map<string, { answer: unknown; image_key?: string; attempt_no: number; submitted_at: string }> // key=activity_id|participant_id|attempt_no
  tokens: Map<string, string> // token → participant_id
  subscribers: Set<(event: string, data: unknown) => void>
}
const rooms = new Map<string, Room>()

function snapshotOf(room: Room): V3ClassroomSnapshot {
  return { session: JSON.parse(JSON.stringify(room.session)), questions: Object.values(room.questions), seq: room.seq, summary: room.summary }
}

/** 学生视角投影（§12.5）：revealed 前无答案无分布；my_submitted 标注本人作答 */
function projectForStudent(room: Room, participantId: string): V3ClassroomSnapshot {
  const snap = snapshotOf(room)
  snap.session.activities = snap.session.activities.map((a) => {
    const revealed = a.status === 'revealed' || a.status === 'completed'
    const mine = room.responses.has(`${a.activity_id}|${participantId}|1`)
    return {
      ...a,
      stats: revealed
        ? { ...(a.stats || { answered: 0 }), my_submitted: mine }
        : { answered: a.stats?.answered ?? 0, my_submitted: mine },
    }
  })
  snap.questions = (snap.questions || []).map((q) => (revealedIn(snap, q.question_id) ? q : { question_id: q.question_id, stem_latex: q.stem_latex, options: q.options }))
  return snap
}
function revealedIn(snap: V3ClassroomSnapshot, questionId: string): boolean {
  const act = snap.session.activities.find((a) => a.question_id === questionId)
  return !!act && (act.status === 'revealed' || act.status === 'completed')
}

/** durable 事件：seq 唯一、广播所有订阅者（学生订阅者拿到的是**投影后**事件——见 attachStream） */
function emit(room: Room, event_type: string, payload: Record<string, any>): DurableEvent {
  const ev: DurableEvent = { seq: ++room.seq, event_id: `ev-${room.seq}`, event_type, payload, created_at: nowIso() }
  room.events.push(ev)
  for (const sub of room.subscribers) sub('event', ev)
  return ev
}

/* ---------- 题面 ---------- */
function briefOf(questionId: string): V3ClassroomQuestionBrief | null {
  const q = (V3_QUIZ_QUESTIONS as any[]).find((x) => x.id === questionId)
  if (!q) return null
  return { question_id: q.id, stem_latex: q.stem_latex, options: q.options, answer: q.answer, analysis: q.analysis }
}

/** 学生视角 live 事件投影（G7 铁律：公布前答案/分布不出服务端）：
 * - 推题/锁定：activity.stats 剥分布与正确率、补 my_submitted；question 剥 answer/analysis（推题即带题面，学生端无需二次请求）
 * - 作答：分布剥离；my_submitted 标注接收者本人
 * - 公布：答案与分布放开（my_submitted 仍按本人标注） */
function projectEventForStudent(room: Room, participantId: string, ev: DurableEvent): DurableEvent {
  const mine = (activityId: string) => room.responses.has(`${activityId}|${participantId}|1`)
  const stripStats = (a: any, revealed: boolean) => {
    if (!a) return a
    const stats: any = revealed ? { ...(a.stats || {}) } : { answered: a.stats?.answered ?? 0 }
    stats.my_submitted = mine(a.activity_id)
    if (!revealed) { delete stats.distribution; delete stats.correct_rate }
    return { ...a, stats }
  }
  const stripAnswer = (q: any) => (q ? { question_id: q.question_id, stem_latex: q.stem_latex, options: q.options } : q)
  const p: any = { ...ev.payload }
  if (ev.event_type === 'activity_pushed') {
    p.activity = stripStats(p.activity, false)
    p.question = stripAnswer(p.question)
  } else if (ev.event_type === 'response_submitted') {
    p.activity = stripStats(p.activity, false)
    if (p.stats) { p.stats = { answered: p.stats.answered ?? 0, my_submitted: p.participant_id === participantId } }
  } else if (ev.event_type === 'activity_locked') {
    p.activity = stripStats(p.activity, false)
    p.question = stripAnswer(p.question)
  } else if (ev.event_type === 'activity_revealed') {
    p.activity = stripStats(p.activity, true)
    // question 原样下发（答案随公布放开）
  }
  return { ...ev, payload: p }
}

/* ---------- SSE 订阅 ---------- */
function attachStream(res: any, room: Room, projection: (room: Room) => V3ClassroomSnapshot, req: any, student?: { participantId: string }) {
  res.writeHead(200, {
    'Content-Type': 'text/event-stream; charset=utf-8',
    'Cache-Control': 'no-cache',
    Connection: 'keep-alive',
    'X-Accel-Buffering': 'no',
  })
  res.write('retry: 3000\n\n')
  // 恢复协议（§12.4）：先 snapshot（权威重置）→ after(Last-Event-ID) 重放 → 续 live；
  // 学生流：快照与事件都走学生视角投影
  const project = (ev: DurableEvent) => (student ? projectEventForStudent(room, student.participantId, ev) : ev)
  const lastId = Number(req.headers?.['last-event-id'] || 0)
  sendSse(res, 'snapshot', projection(room))
  for (const ev of room.events) if (ev.seq > lastId) sendSse(res, 'event', project(ev))
  const sub = (event: string, data: unknown) => {
    const wrapped = data as DurableEvent
    sendSse(res, event, event === 'event' ? project(wrapped) : data)
  }
  room.subscribers.add(sub)
  const heartbeat = setInterval(() => { if (!res.writableEnded && !res.destroyed) res.write(': ping\n\n') }, 15000)
  res.on('close', () => { clearInterval(heartbeat); room.subscribers.delete(sub) })
}

/* ---------- 路由 ---------- */
export async function handleTeacherV3ClassroomApi(req: any, res: any): Promise<boolean> {
  const url = String(req.url || '')
  if (!url.startsWith('/teacher-v3/classroom')) return false
  const method = String(req.method || 'GET').toUpperCase()
  const path = url.split('?')[0]
  const body = method === 'GET' || method === 'DELETE' ? {} : await readBody(req)

  /* ---- join（无 JWT，§10）与 REST ---- */
  if (method === 'POST' && path === '/teacher-v3/classroom/join') {
    const { join_code, student_name } = body
    const room = [...rooms.values()].find((r) => r.session.join_code === String(join_code) && r.session.status === 'open')
    if (!room) return fail(res, 404, 40401, '课堂码无效或课堂已结束'), true
    if (!String(student_name || '').trim()) return fail(res, 400, 40002, 'student_name_required'), true
    const participant: V3ClassroomParticipant = { participant_id: uid('pt'), student_name: String(student_name).trim(), joined_at: nowIso() }
    room.session.participants.push(participant)
    const token = `mock-room-${participant.participant_id}`
    room.tokens.set(token, participant.participant_id)
    emit(room, 'participant_joined', { participant })
    return ok(res, { session_id: room.session.session_id, participant_id: participant.participant_id, student_name: participant.student_name, token, session_name: room.session.class_name }), true
  }

  /* ---- 学生端点（课堂作用域 token，须在教师门禁之前） ---- */
  const studentToken = String(req.headers?.authorization || '').replace(/^Bearer\s+/i, '')
  const mStudentStream = path.match(/^\/teacher-v3\/classroom\/sessions\/([^/]+)\/student-stream$/)
  if (method === 'GET' && mStudentStream) {
    const room = rooms.get(mStudentStream[1])
    if (!room) return fail(res, 404, 40401, 'session_not_found'), true
    const pid = room.tokens.get(studentToken)
    if (!pid) return fail(res, 401, 40101, 'classroom_token_required'), true
    attachStream(res, room, (r) => projectForStudent(r, pid), req, { participantId: pid })
    return true
  }
  const mResponse = path.match(/^\/teacher-v3\/classroom\/sessions\/([^/]+)\/responses$/)
  if (method === 'POST' && mResponse) {
    const room = rooms.get(mResponse[1])
    if (!room) return fail(res, 404, 40401, 'session_not_found'), true
    const participantId = room.tokens.get(studentToken)
    if (!participantId) return fail(res, 401, 40101, 'classroom_token_required'), true
    const act = room.session.activities.find((a) => a.activity_id === String(body.activity_id))
    if (!act) return fail(res, 404, 40401, 'activity_not_found'), true
    if (act.status !== 'collecting') return fail(res, 409, 40901, 'state_invalid：该活动已停止收答'), true
    // 幂等核心约束：UNIQUE(activity_id, participant_id, attempt_no)——重复提交返回首次结果（§14）
    const attemptNo = Number(body.attempt_no || 1)
    const key = `${act.activity_id}|${participantId}|${attemptNo}`
    if (room.responses.has(key)) return ok(res, { ok: true, duplicate: true, activity: act }), true
    room.responses.set(key, { answer: body.answer, image_key: body.image_key, attempt_no: attemptNo, submitted_at: nowIso() })
    // 服务端权威聚合（G7）：前端不做任何本地统计
    const dist = (act.stats?.distribution || {}) as Record<string, number>
    const answerKey = String(body.answer ?? '')
    dist[answerKey] = (dist[answerKey] || 0) + 1
    act.stats = { ...(act.stats || {}), answered: (act.stats?.answered || 0) + 1, distribution: dist }
    emit(room, 'response_submitted', { activity_id: act.activity_id, participant_id: participantId, stats: act.stats, activity: act })
    return ok(res, { ok: true, activity: act }), true
  }

  /* ---- 以下教师端点：教师角色门禁（与既有 mock 一致的 role_denied 语义） ---- */
  if (!isTeacher(req)) { fail(res, 403, 40301, 'role_denied'); return true }

  if (method === 'POST' && path === '/teacher-v3/classroom/sessions') {
    const sessionId = uid('cs')
    let joinCode = ''
    do { joinCode = String(Math.floor(100000 + Math.random() * 900000)) } while ([...rooms.values()].some((r) => r.session.join_code === joinCode))
    const session: V3ClassroomSession = {
      session_id: sessionId,
      class_id: String(body.class_id || 'c2-03'),
      class_name: String(body.class_name || '高二(3)班'),
      join_code: joinCode,
      topic: String(body.topic || ''),
      status: 'open',
      started_at: nowIso(),
      participants: [],
      activities: [],
      online_count: 0,
    }
    const room: Room = { session, questions: {}, events: [], seq: 0, summary: null, responses: new Map(), tokens: new Map(), subscribers: new Set() }
    rooms.set(sessionId, room)
    emit(room, 'session_opened', { session })
    return ok(res, session), true
  }

  const mSnap = path.match(/^\/teacher-v3\/classroom\/sessions\/([^/]+)\/snapshot$/)
  const mStream = path.match(/^\/teacher-v3\/classroom\/sessions\/([^/]+)\/stream$/)
  const mEnd = path.match(/^\/teacher-v3\/classroom\/sessions\/([^/]+)\/end$/)
  const mCall = path.match(/^\/teacher-v3\/classroom\/sessions\/([^/]+)\/call-random$/)
  const mPush = path.match(/^\/teacher-v3\/classroom\/sessions\/([^/]+)\/activities$/)
  const mAct = path.match(/^\/teacher-v3\/classroom\/sessions\/([^/]+)\/activities\/([^/]+)\/(lock|reveal|variation)$/)

  if (method === 'GET' && mSnap) {
    const room = rooms.get(mSnap[1])
    if (!room) return fail(res, 404, 40401, 'session_not_found'), true
    return ok(res, snapshotOf(room)), true
  }
  if (method === 'GET' && mStream) {
    const room = rooms.get(mStream[1])
    if (!room) return fail(res, 404, 40401, 'session_not_found'), true
    attachStream(res, room, snapshotOf, req)
    return true
  }
  if (method === 'POST' && mEnd) {
    const room = rooms.get(mEnd[1])
    if (!room) return fail(res, 404, 40401, 'session_not_found'), true
    if (room.session.status !== 'open') return fail(res, 409, 40901, 'state_invalid：课堂不在 open 态'), true
    room.session.status = 'ended'
    room.session.ended_at = nowIso()
    // 结课生成 classroom_summaries（§4.6）：真实来自本节课作答聚合
    const acts = room.session.activities
    const rates = acts.map((a) => a.stats?.correct_rate).filter((x): x is number => typeof x === 'number')
    const asked = room.questions
    const topWrong = acts
      .map((a) => ({ a, wrong: Object.entries(a.stats?.distribution || {}).sort((x, y) => y[1] - x[1])[0] }))
      .filter(({ a, wrong }) => wrong && asked[a.question_id || ''] && wrong[0] !== asked[a.question_id!].answer)
      .map(({ a }) => `易错项 ${Object.entries(a.stats?.distribution || {}).sort((x, y) => y[1] - x[1])[0]?.[0]}`)
    const durationMin = Math.max(1, Math.round(((room.session.ended_at ? Date.parse(room.session.ended_at) : Date.now()) - Date.parse(room.session.started_at || room.session.ended_at || nowIso())) / 60000))
    room.summary = {
      stats: {
        questions: acts.length,
        avg_correct_rate: rates.length ? Math.round(rates.reduce((x, y) => x + y, 0) / rates.length) : null,
        top_wrong: topWrong[0] || null,
        duration_min: durationMin,
        participants: room.session.participants.length,
      },
      insight: acts.length ? '本节课数据来自真实课堂作答，可在学情洞察回流查看趋势。' : '本节课未推送互动题目。',
    }
    emit(room, 'session_ended', { ended_at: room.session.ended_at })
    emit(room, 'summary_ready', { summary: room.summary })
    return ok(res, room.session), true
  }
  if (method === 'POST' && mAct) {
    const room = rooms.get(mAct[1])
    const act = room?.session.activities.find((a) => a.activity_id === mAct![2])
    if (!room || !act) return fail(res, 404, 40401, 'session_or_activity_not_found'), true
    if (mAct[3] === 'lock') {
      if (act.status !== 'collecting') return fail(res, 409, 40901, `state_invalid：collecting→locked 才合法（当前 ${act.status}）`), true
      act.status = 'locked'
      act.locked_at = nowIso()
      // 服务端计算正确率（G7：统计是服务端聚合，不是前端推算）
      const q = act.question_id ? room.questions[act.question_id] : null
      const dist = (act.stats?.distribution || {}) as Record<string, number>
      const answered = act.stats?.answered || 0
      if (q && answered) act.stats = { ...act.stats!, correct_rate: Math.round(((dist[q.answer || ''] || 0) / answered) * 100) }
      emit(room, 'activity_locked', { activity: act })
      return ok(res, act), true
    }
    if (mAct[3] === 'reveal') {
      if (act.status !== 'locked' && act.status !== 'collecting') return fail(res, 409, 40901, `state_invalid：locked→revealed 才合法（当前 ${act.status}）`), true
      act.status = 'revealed'
      const question = act.question_id ? room.questions[act.question_id] : null
      emit(room, 'activity_revealed', { activity: act, question })
      return ok(res, act), true
    }
    // variation：AiGeneration 状态机（§12.2）generating→verifying→awaiting_teacher（真实 LLM 变式 M3 接 B1/B2）
    if (act.ai_generation && ['generating', 'verifying'].includes(act.ai_generation.status)) return fail(res, 409, 40901, 'state_invalid：变式生成中'), true
    const base = briefOf(act.question_id || '')
    act.ai_generation = {
      status: 'awaiting_teacher',
      content: base ? { kind: 'variation', stem_latex: base.stem_latex, options: base.options, note: '基于本题知识点生成的变式草稿（演示数据）' } : { kind: 'variation', note: '变式草稿（演示数据）' },
      verifier_result: { passed: true, method: 'sympy', detail: 'mock：等价性采样通过' },
    }
    emit(room, 'ai_generation_updated', { activity_id: act.activity_id, ai_generation: act.ai_generation })
    return ok(res, act), true
  }
  if (method === 'POST' && mCall) {
    const room = rooms.get(mCall[1])
    if (!room) return fail(res, 404, 40401, 'session_not_found'), true
    if (!room.session.participants.length) return fail(res, 409, 40901, 'no_participants'), true
    const pick = room.session.participants[Math.floor(Math.random() * room.session.participants.length)]
    return ok(res, { participant: pick }), true
  }
  if (method === 'POST' && mPush) {
    // 推互动（§12.3）：question/photo_submit/poll/game；题面进 room.questions，推题即 collecting
    const room = rooms.get(mPush[1])
    if (!room) return fail(res, 404, 40401, 'session_not_found'), true
    if (room.session.status !== 'open') return fail(res, 409, 40901, 'state_invalid：课堂已结束'), true
    const brief = body.question_id ? briefOf(String(body.question_id)) : null
    if (body.question_id && !brief) return fail(res, 404, 40401, 'question_not_found'), true
    const act: V3ClassroomActivity = {
      activity_id: uid('act'),
      kind: body.kind || 'question',
      question_id: body.question_id || undefined,
      config: body.config || {},
      ord: room.session.activities.length + 1,
      status: 'collecting',
      pushed_at: nowIso(),
      stats: { answered: 0, distribution: {} },
    }
    room.session.activities.push(act)
    if (brief) room.questions[brief.question_id] = brief
    // 推题事件带题面：教师得全量；学生投影层剥离 answer/analysis（G7）
    emit(room, 'activity_pushed', { activity: act, question: brief || undefined })
    return ok(res, act), true
  }

  fail(res, 404, 40404, 'not_found')
  return true
}
