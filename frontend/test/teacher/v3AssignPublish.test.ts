/**
 * B4 · 作业发布 → 模拟提交 → 教师终审 → 讲评 Artifact（mock 契约链）
 */
import { describe, it, expect, beforeAll } from 'vitest'
import { handleTeacherV3Api } from '@/mock/teacherV3Server'

function makeReqRes(method: string, url: string, body?: any) {
  const req: any = { method, url, headers: { authorization: 'Bearer mock-token-teacher-preview' } }
  const chunks: any[] = []
  const res: any = {
    statusCode: 200, headersSent: false, writableEnded: false, destroyed: false,
    setHeader() {}, write(c: any) { chunks.push(c) }, end(c?: any) { if (c) chunks.push(c); this.writableEnded = true },
  }
  if (body !== undefined) {
    req.on = (ev: string, cb: (c?: any) => void) => {
      if (ev === 'data') setImmediate(() => cb(JSON.stringify(body)))
      if (ev === 'end') setImmediate(() => cb())
    }
  }
  const text = () => chunks.join('')
  const json = () => { try { const raw = text(); const i = raw.indexOf('{'); return JSON.parse(raw.slice(i)) } catch { return null } }
  const sseEvents = () => text().split('\n\n').filter((x: string) => x.startsWith('event:')).map((x: string) => {
    const lines = x.split('\n')
    return { event: lines[0].slice(7).trim(), data: JSON.parse(lines[1].slice(6)) }
  })
  return { req, res, json, sseEvents }
}

beforeAll(async () => {
  // 建立教师身份态（mock 身份写 localStorage；node 环境由 handleTeacherV3Api 内 isTeacher 判定 header 即可）
})

describe('B4 · 发布 → 提交 → 终审 → 讲评', () => {
  it('发布为作业 → 列表置顶（待提交）→ 模拟提交 → 聚类出现 → 逐生终审确认 → 讲评 Artifact', { timeout: 15000 }, async () => {
    // 发布
    const pub = makeReqRes('POST', '/teacher-v3/assignments/publish', {
      title: '《测试》课后作业', class_id: 'c2-03', deadline: '明晚 22:00', answer_policy: 'manual',
      questions: [{ stem_latex: '求 f(x)=x^{2} 的单调区间', answer: '增区间 (0,+∞)', full_score: 5, kp_name: '导数与单调性' }],
    })
    await handleTeacherV3Api(pub.req, pub.res)
    const pubBody = pub.json()
    expect(pubBody.code).toBe(0)
    expect(pubBody.data.demo).toBe(true)
    const aid = pubBody.data.assignment_id
    expect(aid).toContain('ga-pub')

    // 列表：新作业置顶、待提交、非示例
    const list = makeReqRes('GET', '/teacher-v3/grading/assignments')
    await handleTeacherV3Api(list.req, list.res)
    const items = list.json().data.items
    expect(items[0].id).toBe(aid)
    expect(items[0].submitted).toBe(0)
    expect(items[0].is_sample).toBe(false)
    expect(items[0].status).toBe('collecting')

    // 详情：题干+标准答案+rubric 存在
    const det = makeReqRes('GET', `/teacher-v3/grading/assignments/${aid}`)
    await handleTeacherV3Api(det.req, det.res)
    const detail = det.json().data
    expect(detail.questions.length).toBe(1)
    expect(detail.questions[0].standard_answer).toContain('增区间')
    expect(detail.questions[0].rubric.length).toBeGreaterThan(0)
    expect(detail.questions[0].clusters.length).toBe(0)

    // 模拟提交
    const sim = makeReqRes('POST', `/teacher-v3/grading/assignments/${aid}/simulate-submissions`, {})
    await handleTeacherV3Api(sim.req, sim.res)
    expect(sim.json().data.demo).toBe(true)
    const det2 = makeReqRes('GET', `/teacher-v3/grading/assignments/${aid}`)
    await handleTeacherV3Api(det2.req, det2.res)
    const detail2 = det2.json().data
    expect(detail2.submitted).toBeGreaterThan(0)
    expect(detail2.questions[0].clusters.length).toBe(3)
    const partial = detail2.questions[0].clusters.find((c: any) => c.kind === 'partial')
    expect(partial.members.length).toBeGreaterThan(0)
    expect(partial.sample[0].recognized_steps.some((st: any) => st.status === 'ai-flag')).toBe(true)

    // 逐生终审确认（教师改分 + 步骤判定）
    const cid = partial.id
    const confirm = makeReqRes('POST', `/teacher-v3/grading/assignments/${aid}/clusters/${cid}/confirm`, {
      feedback: '末步骤缺失，已按评分点扣分',
      reviews: [{ student: partial.sample[0].student, score: 3, steps: [{ step: 0, verdict: 'ok' }, { step: 1, verdict: 'judge' }] }],
    })
    await handleTeacherV3Api(confirm.req, confirm.res)
    expect(confirm.json().data.ok).toBe(true)
    const det3 = makeReqRes('GET', `/teacher-v3/grading/assignments/${aid}`)
    await handleTeacherV3Api(det3.req, det3.res)
    const detail3 = det3.json().data
    const c3 = detail3.questions[0].clusters.find((c: any) => c.id === cid)
    expect(c3.confirmed.reviews[0].score).toBe(3)
    expect(c3.confirmed.reviews[0].steps[1].verdict).toBe('judge')

    // 讲评 Artifact
    let deckId = ''
    {
      const req: any = { method: 'POST', url: `/teacher-v3/grading/assignments/${aid}/review-pack`, headers: { authorization: 'Bearer mock-token-teacher-preview' } }
      const chunks: string[] = []
      const res: any = { statusCode: 200, headersSent: false, writableEnded: false, destroyed: false, setHeader() {}, writeHead() {}, write(c: any) { chunks.push(c) }, end(c?: any) { if (c) chunks.push(c); this.writableEnded = true } }
      req.on = (ev: string, cb: (c?: any) => void) => {
        if (ev === 'data') setImmediate(() => cb('{}'))
        if (ev === 'end') setImmediate(() => cb())
      }
      await handleTeacherV3Api(req, res)
      const events = chunks.join('').split('\n\n').filter((x) => x.startsWith('event:')).map((x) => {
        const lines = x.split('\n')
        return { event: lines[0].slice(7).trim(), data: JSON.parse(lines[1].slice(6)) }
      })
      const done = events[events.length - 1]
      expect(done.data.deck_id).toContain('deck-review')
      deckId = done.data.deck_id
      expect(done.data.top_error).toBeTruthy()
    }
    // 讲评 deck 可直接打开（真实 Artifact）
    const deckReq = makeReqRes('GET', `/teacher-v3/decks/${deckId}`)
    await handleTeacherV3Api(deckReq.req, deckReq.res)
    const deck = deckReq.json().data
    expect(deck.title).toContain('讲评')
    expect(deck.slides.length).toBeGreaterThan(1)
  })

  it('发布缺答案题目不会被阻断在前端（检查在前端做），但 mock 仍生成默认评分点', async () => {
    const pub = makeReqRes('POST', '/teacher-v3/assignments/publish', {
      title: '缺答案作业', class_id: 'c2-05',
      questions: [{ stem_latex: '无答案题', answer: '', full_score: 4 }],
    })
    await handleTeacherV3Api(pub.req, pub.res)
    const aid = pub.json().data.assignment_id
    const det = makeReqRes('GET', `/teacher-v3/grading/assignments/${aid}`)
    await handleTeacherV3Api(det.req, det.res)
    expect(det.json().data.questions[0].standard_answer).toContain('待补标准答案')
  })
})
