// C1 AI 备课台契约测试：deck-outline 课型→环节语义结构 / 章节锚定路由 / generation-deck 接地上下文回写。
// 依据：Desktop\V3课件工坊-审计与创新报告.md §A1（环节语义大纲 = 差异化点；章节显式路由优先于课题关键词）。
import { describe, it, expect } from 'vitest'
import { handleTeacherV3Api } from '@/mock/teacherV3Server'

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

describe('C1 deck-outline：课型 → 环节语义大纲', () => {
  it('讲评课返回讲评环节链（得分概览→错因聚焦→典型错例→变式再练→方法归纳），kind 随页下发', async () => {
    const res = await call('POST', '/teacher-v3/generation/deck-outline', { topic: '椭圆及其标准方程', course_type: '讲评课' })
    expect(res.statusCode).toBe(200)
    const d = res.body.data
    expect(d.outline.map((o: any) => o.title)).toEqual(['得分概览', '错因聚焦', '典型错例剖析', '变式再练', '方法归纳'])
    expect(d.outline.map((o: any) => o.kind)).toEqual(['blank', 'review', 'example', 'variation', 'summary'])
    expect(d.course_type).toBe('讲评课')
    expect(d.note).toContain('讲评课')
  })

  it('习题课/复习课返回各自的环节链（与新授课结构不同）', async () => {
    const drill = await call('POST', '/teacher-v3/generation/deck-outline', { topic: '导数与单调性', course_type: '习题课' })
    expect(drill.body.data.outline[0].title).toBe('复习铺垫')
    const review = await call('POST', '/teacher-v3/generation/deck-outline', { topic: '导数与单调性', course_type: '复习课' })
    expect(review.body.data.outline[0].title).toBe('知识梳理')
  })

  it('新授课（默认）仍走内容源章节链，首页为封面 kind', async () => {
    const res = await call('POST', '/teacher-v3/generation/deck-outline', { topic: '双曲线及其标准方程（第1课时）' })
    const d = res.body.data
    expect(d.matched).toBe(true)
    expect(d.outline[0].kind).toBe('cover')
    expect(d.outline.some((o: any) => /双曲线的定义/.test(o.title))).toBe(true)
  })

  it('章节锚定：课题关键词不命中但章节命中时，按章节路由内容源（章节优先）', async () => {
    const res = await call('POST', '/teacher-v3/generation/deck-outline', {
      topic: '第 1 课时',
      chapter: '人教A版（2019） ▸ 选择性必修一 ▸ 圆锥曲线 ▸ 双曲线 ▸ 双曲线及其标准方程',
    })
    const d = res.body.data
    expect(d.matched).toBe(true)
    expect(d.outline.some((o: any) => /双曲线/.test(o.title))).toBe(true)
    expect(d.note).toContain('章节锚定')
  })

  it('裸课题未命中内容源时如实返回 matched=false（不冒充）', async () => {
    const res = await call('POST', '/teacher-v3/generation/deck-outline', { topic: '数列求和' })
    expect(res.body.data.matched).toBe(false)
  })
})

describe('C1.1 要求编译器：教师要求 → 大纲个性化 + 回应台账', () => {
  it('引入方式 + 例题分层：页名被改写、例题拆基础/提升两页，台账逐条 applied 带页码', async () => {
    const res = await call('POST', '/teacher-v3/generation/deck-outline', {
      topic: '双曲线及其标准方程（第1课时）',
      requirements: ['用拉链实验引入，例题从基础到提升'],
    })
    const d = res.body.data
    const titles: string[] = d.outline.map((o: any) => o.title)
    expect(titles.some((t) => /拉链实验/.test(t))).toBe(true)
    expect(titles.filter((t) => /例题精讲 · 基础|例题精讲 · 提升/.test(t)).length).toBe(2)
    expect(d.reqs.length).toBe(2)
    expect(d.reqs.every((r: any) => r.status === 'applied' && r.pages.length > 0)).toBe(true)
    expect(d.note).toContain('2/2 条要求')
  })

  it('一段话里的多个环节都吃掉：「加易错辨析和当堂检测」插入两页且排在小结前', async () => {
    const res = await call('POST', '/teacher-v3/generation/deck-outline', {
      topic: '双曲线及其标准方程（第1课时）',
      requirements: ['加易错辨析和当堂检测'],
    })
    const d = res.body.data
    const titles: string[] = d.outline.map((o: any) => o.title)
    expect(titles).toContain('易错辨析')
    expect(titles).toContain('当堂检测')
    expect(titles.indexOf('易错辨析')).toBeLessThan(titles.indexOf('课堂小结'))
    expect(titles.indexOf('当堂检测')).toBeLessThan(titles.indexOf('课堂小结'))
    expect(d.reqs[0].status).toBe('applied')
    expect(d.reqs[0].pages.length).toBe(2)
  })

  it('移除类要求：「不要复习」删掉复习页，不误伤其他页', async () => {
    const res = await call('POST', '/teacher-v3/generation/deck-outline', {
      topic: '双曲线及其标准方程（第1课时）',
      requirements: ['不要复习，直接上新课'],
    })
    const d = res.body.data
    expect(d.outline.some((o: any) => o.kind === 'review' || /^复习/.test(o.title))).toBe(false)
    expect(d.outline.some((o: any) => /双曲线的定义/.test(o.title))).toBe(true)
    const removal = d.reqs.find((r: any) => /不要复习/.test(r.text))
    expect(removal.status).toBe('applied')
    expect(removal.note).toContain('移除')
  })

  it('词表外要求诚实降级 uncovered，不假装听懂', async () => {
    const res = await call('POST', '/teacher-v3/generation/deck-outline', {
      topic: '双曲线及其标准方程（第1课时）',
      requirements: ['配一个课堂讲解视频'],
    })
    const d = res.body.data
    expect(d.reqs[0].status).toBe('uncovered')
    expect(d.reqs[0].note).toContain('C2')
  })

  it('课题本身不进要求台账（避免「确认了一个跟自己无关的要求」噪音）', async () => {
    const res = await call('POST', '/teacher-v3/generation/deck-outline', {
      topic: '双曲线及其标准方程（第1课时）',
      requirements: ['双曲线及其标准方程（第1课时）'],
    })
    expect(res.body.data.reqs.length).toBe(0)
  })

  it('调整指令回路：原始要求 + 追加指令可叠加编译（对话式改大纲）', async () => {
    const first = await call('POST', '/teacher-v3/generation/deck-outline', {
      topic: '双曲线及其标准方程（第1课时）',
      requirements: ['用拉链实验引入'],
    })
    expect(first.body.data.outline.some((o: any) => /拉链实验/.test(o.title))).toBe(true)
    const second = await call('POST', '/teacher-v3/generation/deck-outline', {
      topic: '双曲线及其标准方程（第1课时）',
      requirements: ['用拉链实验引入', '去掉复习回顾，加一道当堂检测'],
    })
    const d = second.body.data
    expect(d.outline.some((o: any) => /拉链实验/.test(o.title))).toBe(true)
    expect(d.outline.some((o: any) => o.title === '当堂检测')).toBe(true)
    expect(d.outline.some((o: any) => o.kind === 'review' || /^复习/.test(o.title))).toBe(false)
  })

  it('移除类要求只删复习页不误删易错辨析，页码按最终结构回查（回归锁）', async () => {
    const res = await call('POST', '/teacher-v3/generation/deck-outline', {
      topic: '双曲线及其标准方程（第1课时）',
      requirements: ['用拉链实验引入，加易错辨析', '去掉复习回顾'],
    })
    const d = res.body.data
    const titles: string[] = d.outline.map((o: any) => o.title)
    expect(titles).toContain('易错辨析')
    expect(titles.some((t: string) => /^复习/.test(t))).toBe(false)
    const yicuo = d.reqs.find((r: any) => /易错辨析/.test(r.text))
    expect(yicuo.pages).toEqual([titles.indexOf('易错辨析')])
  })

  it('无要求时行为与 C1 完全一致（不回归）', async () => {
    const res = await call('POST', '/teacher-v3/generation/deck-outline', { topic: '双曲线及其标准方程（第1课时）' })
    const d = res.body.data
    expect(d.reqs).toEqual([])
    expect(d.outline.length).toBe(6)
  })
})


describe('C1 generation/deck：接地上下文回写', () => {
  it('携带 chapter/course_type/material_name 生成后，deck 带 brief_context，meta/outline 事件回显课型', async () => {
    const res = captureRes()
    await handleTeacherV3Api(toReq({
      method: 'POST',
      url: '/teacher-v3/generation/deck',
      body: {
        topic: '双曲线及其标准方程（第1课时）', class_id: 'c2-05', template_id: 'tpl-academic-blue',
        chapter: '人教A版（2019） ▸ 选择性必修一 ▸ 圆锥曲线 ▸ 双曲线 ▸ 双曲线及其标准方程',
        course_type: '习题课', material_name: '椭圆讲义.docx',
        outline: [{ title: '复习铺垫', kind: 'review' }, { title: '典型例题', kind: 'example' }],
      },
    }), res)
    const events = parseSse(res)
    const meta = events.find((e) => e.event === 'meta')
    expect(meta?.data.course_type).toBe('习题课')
    const done = events.find((e) => e.event === 'done')
    expect(done?.data.deck_id).toBeTruthy()

    const deckRes = await call('GET', `/teacher-v3/decks/${done!.data.deck_id}`)
    const deck = deckRes.body.data
    expect(deck.brief_context).toEqual({ course_type: '习题课', chapter: '人教A版（2019） ▸ 选择性必修一 ▸ 圆锥曲线 ▸ 双曲线 ▸ 双曲线及其标准方程', material: '椭圆讲义.docx' })
    /* 页数 = 教师确认大纲长度（B3 Gate 契约保持） */
    expect(deck.slides.length).toBe(2)
  }, 20000)

  it('纯新授（无章节/课型/材料）不写 brief_context（保持旧契约形状）', async () => {
    const res = captureRes()
    await handleTeacherV3Api(toReq({
      method: 'POST',
      url: '/teacher-v3/generation/deck',
      body: { topic: '双曲线及其标准方程（第1课时）', class_id: 'c2-05', template_id: 'tpl-academic-blue' },
    }), res)
    const done = parseSse(res).find((e) => e.event === 'done')
    const deckRes = await call('GET', `/teacher-v3/decks/${done!.data.deck_id}`)
    expect(deckRes.body.data.brief_context).toBeUndefined()
  }, 20000)
})
