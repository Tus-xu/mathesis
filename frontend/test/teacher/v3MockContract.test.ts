// V3 mock 契约同构测试：/teacher-v3/* 响应形状必须 == src/types/teacherV3.ts 契约（四个后端智能体的对接面）。
// 深链路 SSE 全覆盖：photo-ingest（meta→photo→block→paginate→done）、generation deck/plan、ai-element diff、review-pack。
import { describe, it, expect } from 'vitest'
import { handleTeacherV3Api } from '@/mock/teacherV3Server'
import type { V3TodayData, V3Deck } from '@/types/teacherV3'

function toReq(over: { method: string; url: string; headers?: Record<string, string>; body?: unknown }) {
  const req: any = {
    method: over.method,
    url: over.url,
    headers: { authorization: 'Bearer mock-token-teacher-preview', ...(over.headers || {}) },
  }
  if (over.body !== undefined) {
    req.on = (ev: string, cb: (c?: any) => void) => {
      if (ev === 'data') cb(JSON.stringify(over.body))
      if (ev === 'end') cb()
    }
  } else {
    req.on = (_ev: string, _cb: (c?: any) => void) => {}
  }
  return req
}

function captureRes() {
  const res: any = {
    statusCode: 0,
    headers: {},
    ended: false,
    writableEnded: false,
    destroyed: false,
    chunks: [] as string[],
    body: null as any,
    setHeader(k: string, v: string) { this.headers[k] = v },
    writeHead(code: number, hdrs?: Record<string, string>) { this.statusCode = code; Object.assign(this.headers, hdrs || {}) },
    write(chunk: string) { this.chunks.push(String(chunk)); return true },
    end(body?: string) {
      if (typeof body === 'string' && body) {
        this.chunks.push(body)
        try { this.body = JSON.parse(body) } catch { this.body = body }
      }
      if (this.statusCode === 0) this.statusCode = 200
      this.ended = true
      this.writableEnded = true
    },
  }
  return res
}

async function call(method: string, url: string, body?: unknown) {
  const res = captureRes()
  await handleTeacherV3Api(toReq({ method, url, body }), res)
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

const ELEMENT_TYPES = ['text', 'formula', 'geometry', 'functionPlot', 'dynamicDemo', 'image', 'anchorPhoto', 'pageNo']

describe('V3 mock · 角色门禁', () => {
  it('非教师 token → 403 role_denied', async () => {
    const res = captureRes()
    await handleTeacherV3Api(toReq({ method: 'GET', url: '/teacher-v3/today', headers: { authorization: 'Bearer mock-token-preview' } }), res)
    expect(res.statusCode).toBe(403)
    expect(res.body.code).toBe(40301)
  })
})

describe('V3 mock · catalog 基础目录域', () => {
  it('today 契约：teacher/schedule/todos/class_brief', async () => {
    const res = await call('GET', '/teacher-v3/today')
    expect(res.statusCode).toBe(200)
    const d: V3TodayData = res.body.data
    expect(d.teacher.name).toBeTruthy()
    expect(d.schedule.length).toBeGreaterThan(0)
    expect(d.schedule.some((s) => s.status === 'next')).toBe(true)
    expect(d.todos.every((t) => ['grade', 'prep', 'review', 'meeting'].includes(t.kind))).toBe(true)
    expect(d.class_brief.every((c) => c.trend.length > 0 && typeof c.avg === 'number')).toBe(true)
  })

  it('classes → {items:[...]} 非裸数组', async () => {
    const res = await call('GET', '/teacher-v3/classes')
    expect(res.statusCode).toBe(200)
    expect(Array.isArray(res.body.data)).toBe(false)
    expect(res.body.data.items.length).toBeGreaterThan(0)
    expect(res.body.data.items[0]).toHaveProperty('class_id')
  })

  it('tasks → {items, running}，含进行中任务进度', async () => {
    const res = await call('GET', '/teacher-v3/tasks')
    expect(res.body.data.items.length).toBeGreaterThan(0)
    expect(typeof res.body.data.running).toBe('number')
    expect(res.body.data.items.every((t: any) => typeof t.progress === 'number')).toBe(true)
  })

  it('deck-templates / lesson-templates：模板含自渲染样张字段', async () => {
    const dt = await call('GET', '/teacher-v3/deck-templates')
    expect(dt.body.data.items[0]).toMatchObject({ id: 'tpl-academic-blue', swatch: expect.any(Object), page_kinds: expect.any(Array) })
    const lt = await call('GET', '/teacher-v3/lesson-templates')
    expect(lt.body.data.items[0]).toMatchObject({ id: 'lt-explorer', sections: expect.any(Array) })
  })

  it('figures/presets：剥离 build/miniSvg 函数，纯数据可序列化', async () => {
    const res = await call('GET', '/teacher-v3/figures/presets')
    const items = res.body.data.items
    expect(items.length).toBeGreaterThanOrEqual(17)
    expect(items.some((p: any) => p.id === 'conic/ellipse')).toBe(true)
    for (const p of items) {
      expect(typeof p.build).toBe('undefined')
      expect(typeof p.miniSvg).toBe('undefined')
      expect(p.params.every((sp: any) => typeof sp.def === 'number')).toBe(true)
    }
  })

  it('quiz/questions：q_type / difficulty / source 枚举合法，含 P4 图片题型', async () => {
    const res = await call('GET', '/teacher-v3/quiz/questions')
    const items = res.body.data.items
    expect(res.body.data.total).toBe(items.length)
    expect(items.every((q: any) => ['fill', 'choice', 'solve', 'image'].includes(q.q_type))).toBe(true)
    expect(items.every((q: any) => ['easy', 'medium', 'hard'].includes(q.difficulty))).toBe(true)
    expect(items.some((q: any) => q.source === '拍照入库')).toBe(true)
    const img = items.find((q: any) => q.q_type === 'image')
    expect(img).toBeDefined()
    expect(typeof img.stem_image).toBe('string')
  })

  it('P4 分类树 / 扫描入库：kp-tree 结构及 POST 入库后题目回读可见', async () => {
    const tree = await call('GET', '/teacher-v3/quiz/kp-tree')
    expect(tree.statusCode).toBe(200)
    const root = tree.body.data.tree
    expect(root.length).toBeGreaterThan(0)
    // 三级结构：模块 → 章 → 知识点（叶挂 kp_codes）
    const mod = root[0]
    expect(mod).toHaveProperty('children')
    const leaf: any = mod.children[0]?.children?.[0]
    expect(leaf).toHaveProperty('name')
    expect(leaf).toHaveProperty('kp_codes')

    // 扫描入库（图片题型 POST）
    const before = (await call('GET', '/teacher-v3/quiz/questions')).body.data.items.length
    const imp = await call('POST', '/teacher-v3/quiz/scan-import', {
      src: 'data:image/svg+xml;utf8,<svg/>', kp_code: 'LT-05', kp_name: '立体截面', as_image: true, kp_path: ['立体几何', '截面'],
    })
    expect(imp.statusCode).toBe(200)
    expect(imp.body.data).toMatchObject({ q_type: 'image', source: '拍照入库', kp_code: 'LT-05' })
    const after = await call('GET', '/teacher-v3/quiz/questions')
    expect(after.body.data.items.length).toBe(before + 1)
    expect(after.body.data.items.some((q: any) => q.id === imp.body.data.id && q.q_type === 'image')).toBe(true)
  })

  it('insights / resources / recipes 形状', async () => {
    const ins = await call('GET', '/teacher-v3/insights/overview')
    expect(ins.body.data.kp_heat.length).toBeGreaterThan(0)
    expect(ins.body.data.watchlist.length).toBeGreaterThan(0)
    const rs = await call('GET', '/teacher-v3/resources')
    expect(rs.body.data.items.some((r: any) => r.kind === 'figure-recipe')).toBe(true)
    const rc = await call('GET', '/teacher-v3/recipes')
    expect(rc.body.data.items[0]).toMatchObject({ id: 'rcp-1', params: expect.any(Array) })
  })
})

describe('V3 mock · V3.3 题目删除 & 教材目录知识库驱动', () => {
  it('quiz DELETE：删除一道自编题，议题淡出列表（含拍照入库题）', async () => {
    // 先自编一道题拿到 id
    const created = await call('POST', '/teacher-v3/quiz/questions', {
      kp_code: 'YD-01', kp_name: '椭圆标准方程', q_type: 'solve', difficulty: 'easy', stem_latex: '要删除的测试题', answer: '略',
    })
    expect(created.statusCode).toBe(200)
    const id = created.body.data.id
    const before = (await call('GET', '/teacher-v3/quiz/questions')).body.data.items.length

    const del = await call('DELETE', `/teacher-v3/quiz/questions/${id}`)
    expect(del.statusCode).toBe(200)
    expect(del.body.data).toEqual({ ok: true })

    const after = await call('GET', '/teacher-v3/quiz/questions')
    expect(after.body.data.items.length).toBe(before - 1)
    expect(after.body.data.items.some((q: any) => q.id === id)).toBe(false)

    // 不存在则 404
    const miss = await call('DELETE', '/teacher-v3/quiz/questions/not-exist-x')
    expect(miss.statusCode).toBe(404)
  })

  it('教材目录：GET 标记知识库来源可编辑，POST rebuild-index 重建为 knowledge_base', async () => {
    const got = await call('GET', '/teacher-v3/textbook-chapters')
    expect(got.body.data.provenance).toBe('preset')
    const rj = got.body.data.textbooks.find((t: any) => t.name === '人教A版（2019）')
    expect(rj.editable).toBe(true)
    // 人教A版真实目录：圆锥曲线含 椭圆/双曲线/抛物线 的细分知识点
    expect(rj.chapters.some((c: any) => c.path.includes('椭圆'))).toBe(true)
    expect(rj.chapters.some((c: any) => c.path.includes('导数在研究函数中的应用'))).toBe(true)

    const rebuilt = await call('POST', '/teacher-v3/textbooks/rebuild-index', { file: '人教A版选择性必修一.pdf', version: '人教A版（2019）' })
    expect(rebuilt.statusCode).toBe(200)
    expect(rebuilt.body.data.provenance).toBe('knowledge_base')
    expect(rebuilt.body.data.rebuilt_from).toBe('人教A版选择性必修一.pdf')
    expect(rebuilt.body.data.textbooks[0].editable).toBe(true)
    expect(typeof rebuilt.body.data.note).toBe('string')
  })
})

describe('V3 mock · decks 课件域（CRUD 内存态）', () => {
  it('列表 → {items} 含 slide_count；详情 → 结构化元素', async () => {
    const list = await call('GET', '/teacher-v3/decks')
    expect(list.body.data.items.every((d: any) => typeof d.slide_count === 'number')).toBe(true)
    const detail = await call('GET', '/teacher-v3/decks/deck-ellipse')
    const deck: V3Deck = detail.body.data
    expect(deck.slides.length).toBeGreaterThan(0)
    for (const s of deck.slides) {
      expect(s.elements.every((e) => ELEMENT_TYPES.includes(e.type))).toBe(true)
    }
    expect(deck.slides.some((s) => s.elements.some((e) => e.type === 'formula' && typeof e.latex === 'string'))).toBe(true)
  })

  it('PATCH 改标题 + 增删页 + 改页内 notes', async () => {
    const before = (await call('GET', '/teacher-v3/decks/deck-ellipse')).body.data as V3Deck
    const patched = await call('PATCH', '/teacher-v3/decks/deck-ellipse', { title: '椭圆（改）' })
    expect(patched.body.data.title).toBe('椭圆（改）')

    const newSlide = { id: 'sl-test-1', layout: 'blank' as const, elements: [] }
    const afterAdd = (await call('POST', '/teacher-v3/decks/deck-ellipse/slides', { slide: newSlide, after_id: before.slides[0].id })).body.data.deck as V3Deck
    expect(afterAdd.slides).toHaveLength(before.slides.length + 1)
    expect(afterAdd.slides[1].id).toBe('sl-test-1')

    const afterDel = (await call('DELETE', '/teacher-v3/decks/deck-ellipse/slides/sl-test-1')).body.data.deck as V3Deck
    expect(afterDel.slides).toHaveLength(before.slides.length)

    const target = afterDel.slides[0]
    const afterNotes = await call('PATCH', `/teacher-v3/decks/deck-ellipse/slides/${target.id}`, { notes: '讲解提示：先回顾定义' })
    expect(afterNotes.body.data.slides[0].notes).toBe('讲解提示：先回顾定义')
  })

  it('导出 → 异步任务 task_id', async () => {
    const res = await call('POST', '/teacher-v3/decks/deck-ellipse/export', { format: 'pptx' })
    expect(res.body.data.task_id).toMatch(/^task-/)
  })
})

describe('V3 mock · plans 教案域', () => {
  it('十板块详情：三栏 + $..$ 公式 + 挂例题 examples + 反套话 cliche', async () => {
    const res = await call('GET', '/teacher-v3/plans/plan-ellipse')
    const plan = res.body.data
    expect(plan.sections.length).toBe(10)
    expect(plan.sections[0]).toHaveProperty('teacher_activity')
    expect(plan.sections[0]).toHaveProperty('student_activity')
    expect(plan.sections[0]).toHaveProperty('design_intent')
    expect(plan.sections[4].teacher_activity).toContain('$')
    // 挂例题：例题精讲/变式/作业板块带结构化题
    const withExamples = plan.sections.filter((s: any) => Array.isArray(s.examples) && s.examples.length)
    expect(withExamples.length).toBeGreaterThanOrEqual(3)
    expect(withExamples[0].examples[0]).toMatchObject({ label: expect.any(String), stem_latex: expect.any(String), answer: expect.any(String) })
    // 反套话：教学目标板块标记 cliche 并给出命中明细
    const objectives = plan.sections.find((s: any) => s.name === '教学目标')
    expect(objectives.cliche).toBe(true)
    expect(objectives.cliche_hits.length).toBeGreaterThanOrEqual(1)
  })

  it('推送课件 → 新 deck（source=lesson-push）', async () => {
    const res = await call('POST', '/teacher-v3/plans/plan-ellipse/push-to-deck', { template_id: 'tpl-academic-blue' })
    const deckId = res.body.data.deck_id
    expect(deckId).toBeTruthy()
    const list = await call('GET', '/teacher-v3/decks')
    const pushed = list.body.data.items.find((d: any) => d.id === deckId)
    expect(pushed.source).toBe('lesson-push')
  })
})

describe('V3 mock · grading 批改域', () => {
  it('作业列表 → {items}；详情 → 按题聚类 + 原图区域归一化', async () => {
    const list = await call('GET', '/teacher-v3/grading/assignments')
    expect(list.body.data.items.length).toBe(2)
    const detail = await call('GET', '/teacher-v3/grading/assignments/ga-1')
    const g = detail.body.data
    expect(g.questions.length).toBe(3)
    for (const q of g.questions) {
      expect(q.clusters.every((c: any) => ['correct', 'partial', 'wrong', 'blank'].includes(c.kind))).toBe(true)
      for (const c of q.clusters) {
        for (const s of c.sample) {
          const r = s.photo_region
          expect(r.x).toBeGreaterThanOrEqual(0)
          expect(r.x + r.w).toBeLessThanOrEqual(1.000001)
          expect(s.recognized_steps.every((st: any) => ['ok', 'ai-flag', 'corrected'].includes(st.status))).toBe(true)
        }
      }
    }
    expect(g.tiers.map((t: any) => t.tier)).toEqual(['A', 'B', 'C'])
  })

  it('聚类评语审定 → 写回 sample.feedback', async () => {
    const before = await call('GET', '/teacher-v3/grading/assignments/ga-1')
    const fb = '审定评语：注意不等号方向'
    await call('POST', '/teacher-v3/grading/assignments/ga-1/clusters/q1c2/confirm', { feedback: fb })
    const after = await call('GET', '/teacher-v3/grading/assignments/ga-1')
    const c = after.body.data.questions[0].clusters.find((x: any) => x.id === 'q1c2')
    expect(c.sample[0].feedback).toBe(fb)
  })
})

describe('V3 mock · recognition 重建域', () => {
  it('重建候选：构造校验门——未通过者不允许上屏', async () => {
    const res = await call('POST', '/teacher-v3/recognition/rebuild', { photo_region: { x: 0.28, y: 0.62, w: 0.45, h: 0.3 } })
    const cands = res.body.data.candidates
    expect(cands).toHaveLength(3)
    expect(cands.filter((c: any) => c.passed_validation).length).toBe(2)
    expect(cands.find((c: any) => !c.passed_validation).match_note).toContain('不允许上屏')
  })
})

describe('V3 mock · SSE 深链路', () => {
  it('photo-ingest：meta→photo→block×7→paginate→done，识别块全部 editable', async () => {
    const res = await call('POST', '/teacher-v3/recognition/photo-ingest', {
      question_label: '椭圆焦点弦例题',
      photos: ['blob:p1'],
      config: { scope: 'stem+solution', mode: 'full-solution', template_id: 'tpl-academic-blue', font_tier: 'standard', margin_notes: true },
    })
    expect(String(res.headers['Content-Type'])).toContain('text/event-stream')
    const evs = parseSse(res)
    const seq = evs.map((e) => e.event)
    expect(seq[0]).toBe('meta')
    expect(seq.filter((e) => e === 'photo')).toHaveLength(1)
    expect(seq.filter((e) => e === 'block')).toHaveLength(7)
    expect(seq).toContain('paginate')
    expect(seq[seq.length - 1]).toBe('done')
    for (const e of evs.filter((x) => x.event === 'block')) {
      expect(e.data.editable).toBe(true)
      expect(e.data.confidence).toBeGreaterThan(0.8)
    }
    const paginate = evs.find((e) => e.event === 'paginate')!.data
    expect(paginate.pages).toBe(2)
    expect(paginate.anchor_bars.length).toBe(2)
    expect(evs[evs.length - 1].data.deck_id).toMatch(/^deck-/)
  }, 15000)

  it('generation/deck：outline→slide×6→done，AI 元素全部未确认（R5 不覆写）', async () => {
    const res = await call('POST', '/teacher-v3/generation/deck', { topic: '双曲线及其标准方程', class_id: 'c2-03', template_id: 'tpl-academic-blue' })
    const evs = parseSse(res)
    const seq = evs.map((e) => e.event)
    expect(seq).toContain('outline')
    expect(seq.filter((e) => e === 'slide')).toHaveLength(6)
    expect(seq[seq.length - 1]).toBe('done')
    for (const e of evs.filter((x) => x.event === 'slide')) {
      for (const el of e.data.slide.elements) {
        if (el.type === 'formula' || el.type === 'geometry') expect(el.teacher_confirmed).toBe(false)
      }
    }
    expect(evs[evs.length - 1].data.deck_id).toMatch(/^deck-/)
  }, 15000)

  it('generation/plan：outline→十板块 section×10→done（含挂例题与反套话）', async () => {
    const res = await call('POST', '/teacher-v3/generation/plan', { topic: '双曲线及其标准方程', class_id: 'c2-03', lesson_type: '新授课', template_id: 'lt-explorer' })
    const evs = parseSse(res)
    const seq = evs.map((e) => e.event)
    expect(seq.filter((e) => e === 'section')).toHaveLength(10)
    expect(evs.find((e) => e.event === 'section')!.data.section.confirmed).toBe(false)
    const objective = evs.find((e) => e.event === 'section' && e.data.section.name === '教学目标')
    expect(objective!.data.section.cliche).toBe(true)
    const example = evs.find((e) => e.event === 'section' && Array.isArray(e.data.section.examples) && e.data.section.examples.length)
    expect(example).toBeTruthy()
    expect(evs[evs.length - 1].data.plan_id).toMatch(/^plan-/)
  }, 15000)

  it('ai-element：suggest→diff→done，diff 不自动应用', async () => {
    const res = await call('POST', '/teacher-v3/generation/decks/deck-ellipse/ai-element', { slide_id: 's2', hint: '补充离心率' })
    const evs = parseSse(res)
    const seq = evs.map((e) => e.event)
    expect(seq).toEqual(['meta', 'suggest', 'diff', 'done'])
    const diff = evs.find((e) => e.event === 'diff')!.data
    expect(diff.diffs.length).toBe(2)
    expect(diff.diffs[0].op).toBe('add')
    expect(diff.diffs[0].after.type).toBe('formula')
    expect(evs[evs.length - 1].data.applied).toBe(false)
  }, 10000)

  it('review-pack：按题生成讲评课件页', async () => {
    const res = await call('POST', '/teacher-v3/grading/assignments/ga-1/review-pack', {})
    const evs = parseSse(res)
    const slides = evs.filter((e) => e.event === 'slide')
    expect(slides).toHaveLength(3)
    expect(slides[0].data.q_no).toBe(1)
    expect(slides[0].data.clusters.length).toBeGreaterThan(0)
    expect(String(evs[evs.length - 1].data.deck_id)).toContain('deck-review-') // B4：讲评课件为真实 Artifact（id 动态）
  }, 15000)
})
