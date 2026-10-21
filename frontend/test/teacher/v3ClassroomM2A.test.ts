// A0 M2-A · Classroom 两端接真验收（L9-basic 前端侧）
//  A. mock classroom 域契约（教师/学生双端，02-ARCHITECTURE §12）：开课→join→推题→作答→锁定→公布→结课
//  B. ClassroomView：服务端权威投影（G7——统计来自事件载荷，禁止本地 ++）
//  C. 学生 H5：join_code+姓名 → 课堂 token → 作答回执
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'

/* ================= A. mock classroom 域 ================= */
import { handleTeacherV3ClassroomApi } from '@/mock/teacherV3ClassroomServer'
import { V3_QUIZ_QUESTIONS } from '@/mock/teacherV3Data'

function toReq(over: { method: string; url: string; headers?: Record<string, string>; body?: unknown }) {
  const req: any = {
    method: over.method,
    url: over.url,
    headers: { authorization: 'Bearer mock-token-teacher-preview', ...(over.headers || {}) },
  }
  // readBody 依赖 data/end 事件：无 body 也必须触发 end，否则挂起
  req.on = (ev: string, cb: (c?: any) => void) => {
    if (ev === 'data' && over.body !== undefined) cb(JSON.stringify(over.body))
    if (ev === 'end') cb()
  }
  return req
}
function captureRes() {
  const res: any = {
    statusCode: 0, headers: {}, writableEnded: false, destroyed: false,
    chunks: [] as string[], body: null as any,
    setHeader(k: string, v: string) { this.headers[k] = v },
    writeHead(code: number, hdrs?: Record<string, string>) { this.statusCode = code; Object.assign(this.headers, hdrs || {}) },
    write(chunk: string) { this.chunks.push(String(chunk)); return true },
    on(_ev: string, _cb: (...a: any[]) => void) { /* SSE close 监听：测试直调无事件循环 */ },
    end(body?: string) {
      if (typeof body === 'string' && body) {
        this.chunks.push(body)
        try { this.body = JSON.parse(body) } catch { this.body = body }
      }
      if (this.statusCode === 0) this.statusCode = 200
      this.writableEnded = true
    },
  }
  return res
}
async function call(method: string, url: string, body?: unknown, headers?: Record<string, string>) {
  const res = captureRes()
  await handleTeacherV3ClassroomApi(toReq({ method, url, body, headers }), res)
  return res
}
function parseSse(res: any): { event: string; data: any }[] {
  const out: { event: string; data: any }[] = []
  for (const block of res.chunks.join('').split('\n\n')) {
    const ev = block.match(/^event: (.+)$/m)?.[1]
    const dt = block.match(/^data: (.+)$/m)?.[1]
    if (ev && dt) out.push({ event: ev, data: JSON.parse(dt) })
  }
  return out
}

const CHOICE = V3_QUIZ_QUESTIONS.find((q: any) => q.q_type === 'choice' && q.options?.length >= 4) as any

async function openRoom() {
  const opened = await call('POST', '/teacher-v3/classroom/sessions', { class_id: 'c2-03', class_name: '高二(3)班', topic: '椭圆习题课' })
  return opened.body.data
}
async function joinRoom(joinCode: string, name: string) {
  const r = await call('POST', '/teacher-v3/classroom/join', { join_code: joinCode, student_name: name })
  return r.body.data
}

describe('mock classroom · 双端契约', () => {
  it('开课 → join_code 6 位 / open；join 无 JWT → 课堂 token 与参与人事件', async () => {
    const session = await openRoom()
    expect(session.join_code).toMatch(/^\d{6}$/)
    expect(session.status).toBe('open')
    expect(session.topic).toBe('椭圆习题课')

    // join 不带教师 token（无账号，§10）
    const noAuth = await call('POST', '/teacher-v3/classroom/join', { join_code: session.join_code, student_name: '王小明' }, { authorization: '' })
    expect(noAuth.statusCode).toBe(200)
    const joined = noAuth.body.data
    expect(joined.token).toBeTruthy()
    expect(joined.participant_id).toBeTruthy()

    // 无效课堂码 / 空姓名
    expect((await call('POST', '/teacher-v3/classroom/join', { join_code: '000000', student_name: 'X' }, { authorization: '' })).statusCode).toBe(404)
    expect((await call('POST', '/teacher-v3/classroom/join', { join_code: session.join_code, student_name: ' ' }, { authorization: '' })).statusCode).toBe(400)
  })

  it('教师端点需要教师角色；学生流需要课堂 token', async () => {
    const res = captureRes()
    await handleTeacherV3ClassroomApi(toReq({ method: 'POST', url: '/teacher-v3/classroom/sessions', body: { class_id: 'c2-03' }, headers: { authorization: 'Bearer student-x' } }), res)
    expect(res.statusCode).toBe(403)
    expect(res.body.code).toBe(40301)

    const session = await openRoom()
    const snap = await call('GET', `/teacher-v3/classroom/sessions/${session.session_id}/student-stream`, undefined, { authorization: 'Bearer bogus' })
    expect(snap.statusCode).toBe(401)
  })

  it('推题 → 作答（服务端聚合）→ 重复提交幂等返回首次结果（§14）', async () => {
    const session = await openRoom()
    const stu = await joinRoom(session.join_code, '王小明')

    const pushed = await call('POST', `/teacher-v3/classroom/sessions/${session.session_id}/activities`, { kind: 'question', question_id: CHOICE.id, config: { duration: 90 } })
    expect(pushed.statusCode).toBe(200)
    const act = pushed.body.data
    expect(act.status).toBe('collecting')
    expect(act.ord).toBe(1)

    // 教师端点用教师 token；学生提交用课堂 token
    const sub = await call('POST', `/teacher-v3/classroom/sessions/${session.session_id}/responses`, { activity_id: act.activity_id, answer: CHOICE.answer }, { authorization: `Bearer ${stu.token}` })
    expect(sub.statusCode).toBe(200)
    expect(sub.body.data.activity.stats.answered).toBe(1)
    expect(sub.body.data.activity.stats.distribution[CHOICE.answer]).toBe(1)

    // 重复提交：UNIQUE(activity_id, participant_id, attempt_no) → 返回首次结果
    const dup = await call('POST', `/teacher-v3/classroom/sessions/${session.session_id}/responses`, { activity_id: act.activity_id, answer: 'B' }, { authorization: `Bearer ${stu.token}` })
    expect(dup.body.data.duplicate).toBe(true)
    expect(dup.body.data.activity.stats.answered).toBe(1)

    // 第二个学生
    const stu2 = await joinRoom(session.join_code, '李小红')
    await call('POST', `/teacher-v3/classroom/sessions/${session.session_id}/responses`, { activity_id: act.activity_id, answer: 'B' }, { authorization: `Bearer ${stu2.token}` })
    const snap = await call('GET', `/teacher-v3/classroom/sessions/${session.session_id}/snapshot`)
    expect(snap.body.data.session.activities[0].stats).toEqual({ answered: 2, distribution: { [CHOICE.answer]: 1, B: 1 } })
  })

  it('状态机：锁定算正确率 → 公布下发答案 → 非法转移 409 业务码', async () => {
    const session = await openRoom()
    const stu = await joinRoom(session.join_code, '王小明')
    const act = (await call('POST', `/teacher-v3/classroom/sessions/${session.session_id}/activities`, { kind: 'question', question_id: CHOICE.id })).body.data
    await call('POST', `/teacher-v3/classroom/sessions/${session.session_id}/responses`, { activity_id: act.activity_id, answer: CHOICE.answer }, { authorization: `Bearer ${stu.token}` })

    // collecting → 直接连 push 两次非法：推新题合法但再 reveal 未锁定→合法（mock 允许 collecting→revealed 依据 §12.2 stopped→revealed；此处先锁定）
    const locked = await call('POST', `/teacher-v3/classroom/sessions/${session.session_id}/activities/${act.activity_id}/lock`)
    expect(locked.body.data.status).toBe('locked')
    expect(locked.body.data.stats.correct_rate).toBe(100) // 服务端按答案聚合

    // 锁定后作答 → 409
    const late = await call('POST', `/teacher-v3/classroom/sessions/${session.session_id}/responses`, { activity_id: act.activity_id, answer: 'A' }, { authorization: `Bearer ${stu.token}` })
    expect(late.statusCode).toBe(409)
    expect(late.body.code).not.toBe(500)

    // 重复锁定 → 409
    expect((await call('POST', `/teacher-v3/classroom/sessions/${session.session_id}/activities/${act.activity_id}/lock`)).statusCode).toBe(409)

    const revealed = await call('POST', `/teacher-v3/classroom/sessions/${session.session_id}/activities/${act.activity_id}/reveal`)
    expect(revealed.body.data.status).toBe('revealed')

    // 锁定后作答 → 409
    // snapshot：教师视角含答案
    const snap = await call('GET', `/teacher-v3/classroom/sessions/${session.session_id}/snapshot`)
    const q = snap.body.data.questions.find((x: any) => x.question_id === CHOICE.id)
    expect(q.answer).toBeTruthy()
  })

  it('教师流 SSE：snapshot 首事件 + durable event*；Last-Event-ID 重放补拉（§12.4）', async () => {
    const session = await openRoom()
    await joinRoom(session.join_code, '王小明')
    const act = (await call('POST', `/teacher-v3/classroom/sessions/${session.session_id}/activities`, { kind: 'question', question_id: CHOICE.id })).body.data

    // 首连：snapshot + 全量事件
    const first = await call('GET', `/teacher-v3/classroom/sessions/${session.session_id}/stream`)
    const evs = parseSse(first)
    expect(evs[0].event).toBe('snapshot')
    expect(evs[0].data.session.session_id).toBe(session.session_id)
    const seqs = evs.filter((e) => e.event === 'event').map((e) => e.data.seq)
    expect(seqs).toEqual([...seqs].sort((a, b) => a - b))
    expect(evs.some((e) => e.event === 'event' && e.data.event_type === 'participant_joined')).toBe(true)
    expect(evs.some((e) => e.event === 'event' && e.data.event_type === 'activity_pushed')).toBe(true)

    // 断线重连：Last-Event-ID=seq[0] → 只重放其后
    const replay = await call('GET', `/teacher-v3/classroom/sessions/${session.session_id}/stream`, undefined, { 'last-event-id': String(seqs[0]) })
    const revs = parseSse(replay)
    const reSeqs = revs.filter((e) => e.event === 'event').map((e) => e.data.seq)
    expect(reSeqs.every((s: number) => s > seqs[0])).toBe(true)
    expect(reSeqs).toContain(act.seq ?? reSeqs[0])
  })

  it('学生流 SSE：学生视角投影——公布前无答案无分布；my_submitted 标注', async () => {
    const session = await openRoom()
    const stu = await joinRoom(session.join_code, '王小明')
    const stu2 = await joinRoom(session.join_code, '李小红')
    const act = (await call('POST', `/teacher-v3/classroom/sessions/${session.session_id}/activities`, { kind: 'question', question_id: CHOICE.id })).body.data
    await call('POST', `/teacher-v3/classroom/sessions/${session.session_id}/responses`, { activity_id: act.activity_id, answer: CHOICE.answer }, { authorization: `Bearer ${stu.token}` })

    const sse = await call('GET', `/teacher-v3/classroom/sessions/${session.session_id}/student-stream`, undefined, { authorization: `Bearer ${stu.token}` })
    const evs = parseSse(sse)
    const snap = evs.find((e) => e.event === 'snapshot')!.data
    const proj = snap.session.activities.find((a: any) => a.activity_id === act.activity_id)
    expect(proj.stats.answered).toBe(1)
    expect(proj.stats.my_submitted).toBe(true) // 王小明本人
    expect(proj.stats.distribution).toBeUndefined() // 公布前无分布
    expect(snap.questions.find((q: any) => q.question_id === CHOICE.id).answer).toBeUndefined() // 公布前无答案

    // 另一名学生视角：my_submitted=false
    const sse2 = await call('GET', `/teacher-v3/classroom/sessions/${session.session_id}/student-stream`, undefined, { authorization: `Bearer ${stu2.token}` })
    const proj2 = parseSse(sse2).find((e) => e.event === 'snapshot')!.data.session.activities.find((a: any) => a.activity_id === act.activity_id)
    expect(proj2.stats.my_submitted).toBe(false)

    // 公布后：答案与分布下发
    await call('POST', `/teacher-v3/classroom/sessions/${session.session_id}/activities/${act.activity_id}/reveal`)
    const sse3 = await call('GET', `/teacher-v3/classroom/sessions/${session.session_id}/student-stream`, undefined, { authorization: `Bearer ${stu.token}` })
    const after = parseSse(sse3).find((e) => e.event === 'snapshot')!.data
    expect(after.questions.find((q: any) => q.question_id === CHOICE.id).answer).toBe(CHOICE.answer)
    expect(after.session.activities.find((a: any) => a.activity_id === act.activity_id).stats.distribution).toBeTruthy()
  })

  it('学生流 live 事件投影：推题即得题面（无答案）、作答事件无分布（G7 防泄漏）', async () => {
    const session = await openRoom()
    const stu = await joinRoom(session.join_code, '王小明')
    // 学生先连接学生流（live 订阅在推题之前建立）
    const sse = await call('GET', `/teacher-v3/classroom/sessions/${session.session_id}/student-stream`, undefined, { authorization: `Bearer ${stu.token}` })
    const act = (await call('POST', `/teacher-v3/classroom/sessions/${session.session_id}/activities`, { kind: 'question', question_id: CHOICE.id })).body.data
    await call('POST', `/teacher-v3/classroom/sessions/${session.session_id}/responses`, { activity_id: act.activity_id, answer: CHOICE.answer }, { authorization: `Bearer ${stu.token}` })

    const evs = parseSse(sse).filter((e) => e.event === 'event')
    const pushed = evs.find((e) => e.data.event_type === 'activity_pushed')!
    expect(pushed.data.payload.question.stem_latex).toBeTruthy() // 题面随推题下发
    expect(pushed.data.payload.question.answer).toBeUndefined() // 答案不下发
    expect(pushed.data.payload.activity.stats.distribution).toBeUndefined()
    expect(pushed.data.payload.activity.stats.my_submitted).toBe(false)
    const submitted = evs.find((e) => e.data.event_type === 'response_submitted')!
    expect(submitted.data.payload.stats.my_submitted).toBe(true) // 接收者本人标注
    expect(submitted.data.payload.stats.distribution).toBeUndefined()

    // 教师流同事件不被投影（拿得到答案与分布）
    const tse = await call('GET', `/teacher-v3/classroom/sessions/${session.session_id}/stream`)
    const tPushed = parseSse(tse).filter((e) => e.event === 'event').find((e) => e.data.event_type === 'activity_pushed')!
    expect(tPushed.data.payload.question.answer).toBe(CHOICE.answer)
  })

  it('结课 → ended + 服务端小结（summary_ready），重开失败', async () => {
    const session = await openRoom()
    await joinRoom(session.join_code, '王小明')
    const act = (await call('POST', `/teacher-v3/classroom/sessions/${session.session_id}/activities`, { kind: 'question', question_id: CHOICE.id })).body.data
    await call('POST', `/teacher-v3/classroom/sessions/${session.session_id}/responses`, { activity_id: act.activity_id, answer: CHOICE.answer }, { authorization: `Bearer ${(await joinRoom(session.join_code, '李小红')).token}` })
    await call('POST', `/teacher-v3/classroom/sessions/${session.session_id}/activities/${act.activity_id}/lock`)

    const ended = await call('POST', `/teacher-v3/classroom/sessions/${session.session_id}/end`)
    expect(ended.body.data.status).toBe('ended')
    const snap = await call('GET', `/teacher-v3/classroom/sessions/${session.session_id}/snapshot`)
    expect(snap.body.data.summary.stats.questions).toBe(1)
    expect(snap.body.data.summary.stats.avg_correct_rate).toBe(100)
    expect(snap.body.data.summary.stats.participants).toBe(2)

    // 已结束后：join 与推题均拒
    expect((await call('POST', '/teacher-v3/classroom/join', { join_code: session.join_code, student_name: '迟到' }, { authorization: '' })).statusCode).toBe(404)
    expect((await call('POST', `/teacher-v3/classroom/sessions/${session.session_id}/activities`, { kind: 'poll' })).statusCode).toBe(409)
  })

  it('随机点名来自真实参与人；变式走 AiGeneration 状态机到 awaiting_teacher', async () => {
    const session = await openRoom()
    const stu = await joinRoom(session.join_code, '王小明')
    const picked = await call('POST', `/teacher-v3/classroom/sessions/${session.session_id}/call-random`)
    expect(picked.body.data.participant.participant_id).toBe(stu.participant_id)

    const act = (await call('POST', `/teacher-v3/classroom/sessions/${session.session_id}/activities`, { kind: 'question', question_id: CHOICE.id })).body.data
    const v = await call('POST', `/teacher-v3/classroom/sessions/${session.session_id}/activities/${act.activity_id}/variation`)
    expect(v.body.data.ai_generation.status).toBe('awaiting_teacher')
    expect(v.body.data.ai_generation.verifier_result.passed).toBe(true)
  })
})

/* ================= B/C. 组件层 ================= */
const CLASS_LIST = [{ class_id: 'c2-03', name: '高二(3)班', students: 44 }]
const QLIST = [CHOICE]

let streamHandler: ((event: string, data: any) => void) | null = null
let studentHandler: ((event: string, data: any) => void) | null = null

vi.mock('@/api/teacherV3', () => ({
  v3Api: {
    catalog: {
      classes: vi.fn(async () => ({ data: { items: CLASS_LIST } })),
      quizQuestions: vi.fn(async () => ({ data: { items: QLIST, total: QLIST.length } })),
    },
    classroom: {
      createSession: vi.fn(async () => ({ data: { session_id: 'cs-1', class_id: 'c2-03', class_name: '高二(3)班', join_code: '538201', topic: '椭圆习题课', status: 'open', participants: [], activities: [] } })),
      stream: vi.fn((_id: string, onEvent: any) => { streamHandler = onEvent; return { abort: vi.fn(), finished: Promise.resolve() } }),
      studentStream: vi.fn((_id: string, _token: string, onEvent: any) => { studentHandler = onEvent; return { abort: vi.fn(), finished: Promise.resolve() } }),
      pushActivity: vi.fn(async () => ({ data: { activity_id: 'act-1', kind: 'question', ord: 1, status: 'collecting', stats: { answered: 0, distribution: {} } } })),
      lockActivity: vi.fn(async () => ({ data: { activity_id: 'act-1', status: 'locked', stats: { answered: 2, correct_rate: 50 } } })),
      revealActivity: vi.fn(async () => ({ data: { activity_id: 'act-1', status: 'revealed' } })),
      endSession: vi.fn(async () => ({ data: { session_id: 'cs-1', status: 'ended' } })),
      snapshot: vi.fn(async () => ({ data: { session: { session_id: 'cs-1', status: 'ended', participants: [], activities: [] }, questions: [], seq: 99, summary: { stats: { questions: 1, avg_correct_rate: 50, top_wrong: '易错项 B', duration_min: 3, participants: 2 }, insight: 'x' } } })),
      callRandom: vi.fn(async () => ({ data: { participant: { participant_id: 'p1', student_name: '王小明', joined_at: 'T' } } })),
      join: vi.fn(async () => ({ data: { session_id: 'cs-1', participant_id: 'p-me', student_name: '王小明', token: 'tok-1', session_name: '高二(3)班' } })),
      submitResponse: vi.fn(async () => ({ data: { ok: true, activity: { activity_id: 'act-1', stats: { answered: 1 } } } })),
    },
    decks: { list: vi.fn(async () => ({ data: { items: [{ id: 'deck-1', title: '椭圆' }] } })), addSlide: vi.fn(async () => ({ data: {} })) },
  },
}))

vi.mock('@/components/mathx/GeoFigure.vue', () => ({ default: { name: 'GeoFigure', template: '<div class="geo-mock"/>' } }))

import ClassroomView from '@/pages/teacher-v3/ClassroomView.vue'
import StudentH5 from '@/pages/classroom/StudentH5.vue'
import { v3Api } from '@/api/teacherV3'

function mountView(comp: typeof ClassroomView | typeof StudentH5) {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/:pathMatch(.*)*', component: { template: '<div/>' } }] })
  return mount(comp, { global: { plugins: [router, createPinia()] } })
}

describe('ClassroomView · 服务端权威投影（G7）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    streamHandler = null
    localStorage.clear()
    sessionStorage.clear()
    vi.stubGlobal('confirm', () => true)
  })

  it('开课 → createSession + 订阅教师流 + 课堂码徽标可见', async () => {
    const w = mountView(ClassroomView)
    await flushPromises()
    await w.find('[data-testid="tv3-open-session"]').trigger('click')
    await flushPromises()
    expect(v3Api.classroom.createSession).toHaveBeenCalledWith({ class_id: 'c2-03', class_name: '高二(3)班', topic: '椭圆及其标准方程 · 习题课' })
    expect(v3Api.classroom.stream).toHaveBeenCalledWith('cs-1', expect.any(Function), undefined, expect.objectContaining({ onRecover: expect.any(Function) }))
    expect(w.find('[data-testid="tv3-join-code"]').text()).toContain('538201')
  })

  it('名单/作答统计全部来自 snapshot 与事件载荷：事件给 5 就显示 5（非本地 ++）', async () => {
    const w = mountView(ClassroomView)
    await flushPromises()
    await w.find('[data-testid="tv3-open-session"]').trigger('click')
    await flushPromises()

    streamHandler!('snapshot', {
      session: {
        session_id: 'cs-1', join_code: '538201', status: 'open', participants: [
          { participant_id: 'p1', student_name: '王小明', joined_at: 'T' },
          { participant_id: 'p2', student_name: '李小红', joined_at: 'T' },
        ],
        activities: [],
      },
      questions: [{ question_id: CHOICE.id, stem_latex: CHOICE.stem_latex, options: CHOICE.options, answer: CHOICE.answer }],
      seq: 2, summary: null,
    })
    await flushPromises()
    expect(w.find('[data-testid="tv3-joined"]').text()).toContain('2/44')

    // 推题事件（服务器权威 activity，answered=3）
    streamHandler!('event', { seq: 3, event_id: 'e3', event_type: 'activity_pushed', payload: { activity: { activity_id: 'act-1', kind: 'question', question_id: CHOICE.id, ord: 1, status: 'collecting', stats: { answered: 3, distribution: { A: 2, B: 1 } } } }, created_at: 'T' })
    await flushPromises()
    expect(w.find('[data-testid="tv3-status-tag"]').text()).toContain('答题中')
    expect(w.find('[data-testid="tv3-live-count"]').text()).toContain('3 /')

    // 再来一个作答事件：服务端说 5 就是 5（本地 ++ 只会算 4）
    streamHandler!('event', { seq: 4, event_id: 'e4', event_type: 'response_submitted', payload: { activity_id: 'act-1', participant_id: 'p2', activity: { activity_id: 'act-1', kind: 'question', question_id: CHOICE.id, ord: 1, status: 'collecting', stats: { answered: 5, distribution: { A: 3, B: 2 } } } }, created_at: 'T' })
    await flushPromises()
    expect(w.find('[data-testid="tv3-live-count"]').text()).toContain('5 /')

    // 锁定事件（服务端发完整 activity）→ 「已停止」→ 公布按钮出现
    streamHandler!('event', { seq: 5, event_id: 'e5', event_type: 'activity_locked', payload: { activity: { activity_id: 'act-1', kind: 'question', question_id: CHOICE.id, status: 'locked', ord: 1, stats: { answered: 5, correct_rate: 60, distribution: { A: 3, B: 2 } } } }, created_at: 'T' })
    await flushPromises()
    expect(w.find('[data-testid="tv3-status-tag"]').text()).toContain('已停止')
    expect(w.find('[data-testid="tv3-reveal-q"]').exists()).toBe(true)
    await w.find('[data-testid="tv3-reveal-q"]').trigger('click')
    await flushPromises()
    expect(v3Api.classroom.revealActivity).toHaveBeenCalledWith('cs-1', 'act-1')
  })

  it('随机点名走服务端', async () => {
    const w = mountView(ClassroomView)
    await flushPromises()
    await w.find('[data-testid="tv3-open-session"]').trigger('click')
    await flushPromises()
    streamHandler!('snapshot', { session: { session_id: 'cs-1', join_code: '538201', status: 'open', participants: [{ participant_id: 'p1', student_name: '王小明', joined_at: 'T' }], activities: [] }, questions: [], seq: 1, summary: null })
    await flushPromises()
    // 点名横幅位于「实时作答分布」卡片的活动区内（模板结构）：先推一题再点名
    streamHandler!('event', { seq: 2, event_id: 'e2', event_type: 'activity_pushed', payload: { activity: { activity_id: 'act-1', kind: 'question', question_id: CHOICE.id, ord: 1, status: 'collecting', stats: { answered: 0, distribution: {} } } }, created_at: 'T' })
    await flushPromises()
    await w.find('[data-testid="tv3-pick-student"]').trigger('click')
    await flushPromises()
    expect(v3Api.classroom.callRandom).toHaveBeenCalledWith('cs-1')
    expect(w.find('[data-testid="tv3-picked"]').text()).toContain('王小明')
  })
})

describe('学生 H5 · 无账号加入与作答', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    studentHandler = null
  })

  it('输码+姓名 → join → 等待老师发题；snapshot 投影出题目', async () => {
    const w = mountView(StudentH5)
    await w.find('[data-testid="h5-join-code"]').setValue('538201')
    await w.find('[data-testid="h5-join-name"]').setValue('王小明')
    await w.find('[data-testid="h5-join-go"]').trigger('click')
    await flushPromises()
    expect(v3Api.classroom.join).toHaveBeenCalledWith({ join_code: '538201', student_name: '王小明' })
    expect(w.find('[data-testid="h5-waiting"]').exists()).toBe(true)

    studentHandler!('snapshot', {
      session: {
        session_id: 'cs-1', status: 'open', join_code: '538201', class_name: '高二(3)班',
        participants: [], activities: [{ activity_id: 'act-1', kind: 'question', question_id: CHOICE.id, ord: 1, status: 'collecting', stats: { answered: 1, my_submitted: false } }],
      },
      questions: [{ question_id: CHOICE.id, stem_latex: CHOICE.stem_latex, options: CHOICE.options }],
      seq: 1, summary: null,
    })
    await flushPromises()
    expect(w.find('[data-testid="h5-stem"]').exists()).toBe(true)
    expect(w.find('[data-testid="h5-opt-0"]').exists()).toBe(true)
    expect(w.find('[data-testid="h5-answered"]').text()).toContain('1')
  })

  it('点选作答 → submitResponse 携带课堂 token；重复提交回执；公布后显示正确答案', async () => {
    const w = mountView(StudentH5)
    await w.find('[data-testid="h5-join-code"]').setValue('538201')
    await w.find('[data-testid="h5-join-name"]').setValue('王小明')
    await w.find('[data-testid="h5-join-go"]').trigger('click')
    await flushPromises()
    studentHandler!('snapshot', {
      session: {
        session_id: 'cs-1', status: 'open', join_code: '538201', class_name: '高二(3)班', participants: [],
        activities: [{ activity_id: 'act-1', kind: 'question', question_id: CHOICE.id, ord: 1, status: 'collecting', stats: { answered: 0, my_submitted: false } }],
      },
      questions: [{ question_id: CHOICE.id, stem_latex: CHOICE.stem_latex, options: CHOICE.options }],
      seq: 1, summary: null,
    })
    await flushPromises()
    await w.find('[data-testid="h5-opt-1"]').trigger('click')
    await flushPromises()
    expect(v3Api.classroom.submitResponse).toHaveBeenCalledWith('cs-1', { activity_id: 'act-1', answer: { choice: 'B' } }, { token: 'tok-1' })
    // 回执来自服务端投影（response_submitted 事件，participant_id===我）
    studentHandler!('event', { seq: 2, event_id: 'e2', event_type: 'response_submitted', payload: { activity_id: 'act-1', participant_id: 'p-me', activity: { activity_id: 'act-1', kind: 'question', question_id: CHOICE.id, ord: 1, status: 'collecting', stats: { answered: 1, my_submitted: true, distribution: { B: 1 } } } }, created_at: 'T' })
    await flushPromises()
    expect(w.find('[data-testid="h5-receipt"]').exists()).toBe(true)

    // 公布：答案 + 本人判分
    studentHandler!('event', { seq: 3, event_id: 'e3', event_type: 'activity_revealed', payload: { activity: { activity_id: 'act-1', kind: 'question', question_id: CHOICE.id, ord: 1, status: 'revealed', stats: { answered: 1, my_submitted: true, distribution: { B: 1 } } }, question: { question_id: CHOICE.id, stem_latex: CHOICE.stem_latex, options: CHOICE.options, answer: CHOICE.answer } }, created_at: 'T' })
    await flushPromises()
    expect(w.find('[data-testid="h5-reveal"]').text()).toContain(`正确答案：${CHOICE.answer}`)
  })

  it('课堂码错误给出可理解文案', async () => {
    ;(v3Api.classroom.join as any).mockRejectedValueOnce(Object.assign(new Error('课堂码无效或课堂已结束'), { code: 40401 }))
    const w = mountView(StudentH5)
    await w.find('[data-testid="h5-join-code"]').setValue('999999')
    await w.find('[data-testid="h5-join-name"]').setValue('王小明')
    await w.find('[data-testid="h5-join-go"]').trigger('click')
    await flushPromises()
    expect(w.find('[data-testid="h5-join-err"]').text()).toContain('课堂码无效')
  })
})
