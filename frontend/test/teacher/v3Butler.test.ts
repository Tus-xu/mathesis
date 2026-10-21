// AI 管家（butler）契约 + 组件测试：
//  A. mock 服务端深链路：tools 目录 / chat 意图路由（剧本A预填跳转·剧本B确认入库·联网引用·数学对话）/ voice-formula / confirm 红线
//  B. 组件：ButlerFab 悬浮球、ButlerPanel 对话流（卡片渲染·确认按钮·拖拽双通道·插入事件）
// 红线依据：写操作必须教师确认后执行（R2）；识别结果只出可编辑卡片不直接定稿。
import { describe, it, expect, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia } from 'pinia'

/* ---------- 组件层依赖 mock（vi.mock 工厂提升，引用一律走 vi.mocked(importee)) ---------- */
/* 可变的 route mock：个别用例（插入本页）需要 deck 上下文 */
const mockRoute: { path: string; query: Record<string, string> } = { path: '/teacher-v3/slides', query: {} }
vi.mock('vue-router', () => ({
  useRoute: () => mockRoute,
  useRouter: () => ({ push: vi.fn() }),
}))
vi.mock('@/api/teacherV3', () => ({
  v3Api: {
    butler: {
      chat: vi.fn(),
      voiceFormula: vi.fn(),
      confirmAction: vi.fn(async () => ({ code: 0, data: { ok: true } })),
    },
  },
}))

import { v3Api } from '@/api/teacherV3'
import ButlerFab from '@/components/teacherV3/ButlerFab.vue'
import ButlerPanel from '@/components/teacherV3/ButlerPanel.vue'
import { handleTeacherV3Api } from '@/mock/teacherV3Server'

const chatMock = vi.mocked(v3Api.butler.chat)
const voiceMock = vi.mocked(v3Api.butler.voiceFormula)
const confirmMock = vi.mocked(v3Api.butler.confirmAction)

/** SSE 模拟：同步派发事件序列（组件 handleEvent 对同步流兼容），返回与 v3Sse 同构的 ctrl */
function sseReply(events: { event: string; data: any }[]) {
  return vi.fn((body: any, onEvent: (e: string, d: any) => void) => {
    for (const { event, data } of events) onEvent(event, data)
    return { abort: () => {}, finished: Promise.resolve() }
  })
}

/* ---------- mock 服务端直调工具（同 v3MockContract 模式） ---------- */
function toReq(over: { method: string; url: string; body?: unknown }) {
  const req: any = { method: over.method, url: over.url, headers: { authorization: 'Bearer mock-token-teacher-preview' } }
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
    statusCode: 0, headers: {}, ended: false, writableEnded: false, destroyed: false,
    chunks: [] as string[], body: null as any,
    setHeader(k: string, v: string) { this.headers[k] = v },
    writeHead(code: number, hdrs?: Record<string, string>) { this.statusCode = code; Object.assign(this.headers, hdrs || {}) },
    write(chunk: string) { this.chunks.push(String(chunk)); return true },
    end(body?: string) {
      if (typeof body === 'string' && body) {
        this.chunks.push(body)
        try { this.body = JSON.parse(body) } catch { this.body = body }
      }
      if (this.statusCode === 0) this.statusCode = 200
      this.ended = true; this.writableEnded = true
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
function sseEventsOf(res: any) { return parseSse(res).map((x) => x.event) }

async function quizTotal() {
  const r = await call('GET', '/teacher-v3/quiz/questions')
  return r.body.data.total as number
}

/* ================= A. mock 服务端 ================= */
describe('butler mock · 工具目录', () => {
  it('写操作必须 confirm_required（红线 R2 的接口化表达）', async () => {
    const r = await call('GET', '/teacher-v3/butler/tools')
    const tools = r.body.data.tools as { name: string; kind: string; confirm_required: boolean }[]
    expect(tools.length).toBeGreaterThanOrEqual(5)
    const write = tools.filter((t) => t.kind === 'write')
    expect(write.length).toBeGreaterThan(0)
    expect(write.every((t) => t.confirm_required)).toBe(true)
    expect(tools.filter((t) => t.kind === 'read').every((t) => !t.confirm_required)).toBe(true)
  })
})

describe('butler mock · chat 意图路由（SSE）', () => {
  it('剧本A 做课件 → token + link 卡 + navigate action（预填 query，不代教师定稿）', async () => {
    const res = await call('POST', '/teacher-v3/butler/chat', { message: '帮我做一个《椭圆及其标准方程》的课件' })
    const events = sseEventsOf(res)
    expect(events[0]).toBe('meta')
    expect(events[events.length - 1]).toBe('done')
    const parsed = parseSse(res)
    const link = parsed.find((x) => x.event === 'card' && x.data.type === 'link')?.data
    // WP-G：「课件工坊」退役 → 剧本A 的落点改为备课会话（不再是独立课件页）
    expect(link.route).toBe('/teacher-v3/prep')
    expect(link.query.mode).toBe('topic')
    expect(link.query.topic).toBe('椭圆及其标准方程')
    const action = parsed.find((x) => x.event === 'action')?.data
    expect(action.action).toBe('navigate')
    expect(action.query.topic).toBe('椭圆及其标准方程')
  })

  it('剧本B 图片+存入题库 → 识别摘要 + pending 动作卡；未确认前题库不变', async () => {
    const before = await quizTotal()
    const res = await call('POST', '/teacher-v3/butler/chat', {
      message: '这道题存入题库',
      images: ['data:image/png;base64,xxx'],
      context: { route: '/teacher-v3/bank', route_title: '题库' },
    })
    const parsed = parseSse(res)
    const card = parsed.find((x) => x.event === 'card' && x.data.type === 'action')?.data
    expect(card.confirm_required).toBe(true)
    expect(card.status).toBe('pending')
    expect(card.params.kp_name).toBeTruthy()
    expect(parsed.some((x) => x.event === 'tool_call' && x.data.tool === 'photo_recognize')).toBe(true)
    expect(await quizTotal()).toBe(before)
  })

  it('联网搜索 → citation 携带来源列表', async () => {
    const res = await call('POST', '/teacher-v3/butler/chat', { message: '椭圆的定义是什么', web_search: true })
    const parsed = parseSse(res)
    const cite = parsed.find((x) => x.event === 'citation')?.data
    expect(cite.sources.length).toBe(2) // B0：移除 example.com 伪引用，仅保留官方来源
    expect(cite.sources.every((s: any) => s.title && s.url)).toBe(true)
  })

  it('数学口语 → 公式卡（可拖可编辑，不直接定稿）', async () => {
    const res = await call('POST', '/teacher-v3/butler/chat', { message: '根号下x平方加y平方' })
    const card = parseSse(res).find((x) => x.event === 'card')?.data
    expect(card.type).toBe('formula')
    expect(card.latex).toContain('\\sqrt')
    expect(card.teacher_confirmed).toBeUndefined()
  })
})

describe('butler mock · 语音公式链（SSE）', () => {
  it('asr_partial* → asr_final → formula 卡（含置信度与备选）', async () => {
    const res = await call('POST', '/teacher-v3/butler/voice-formula', { text: '负b加减根号下b平方减4ac，除以2a' })
    const events = sseEventsOf(res)
    expect(events.filter((e) => e === 'asr_partial').length).toBeGreaterThanOrEqual(2)
    const parsed = parseSse(res)
    expect(parsed.find((x) => x.event === 'asr_final')?.data.text).toContain('根号下')
    const card = parsed.find((x) => x.event === 'card')?.data
    expect(card.type).toBe('formula')
    expect(card.source).toBe('voice')
    expect(card.confidence).toBeLessThan(1)
    expect(card.alternatives.length).toBeGreaterThanOrEqual(1)
  })
})

describe('butler mock · 写动作确认（红线：教师点了执行才入库）', () => {
  it('confirm 后题库 +1，任务列表出现管家入库记录', async () => {
    const before = await quizTotal()
    const r = await call('POST', '/teacher-v3/butler/actions/act-1/confirm', { params: { kp_name: '圆锥曲线' } })
    expect(r.body.data.ok).toBe(true)
    expect(r.body.data.result.question_id).toBeTruthy()
    expect(r.body.data.result.demo).toBe(true) // B0：入库样例标记为演示数据
    expect(await quizTotal()).toBe(before + 1)
    const tasks = await call('GET', '/teacher-v3/tasks')
    expect(tasks.body.data.items.some((t: any) => t.capability === 'butler')).toBe(true)
    const q = await call('GET', '/teacher-v3/quiz/questions?q=演示数据')
    expect(q.body.data.total).toBe(1)
  })
})

/* ================= B. 组件层 ================= */
function mountPanel() {
  return mount(ButlerPanel, { props: { open: true }, global: { plugins: [createPinia()] } })
}

describe('ButlerFab 悬浮球', () => {
  it('点击发出 toggle；未读徽标仅关闭时显示', async () => {
    const w1 = mount(ButlerFab, { props: { open: false, unread: 3 } })
    expect(w1.find('[data-testid="tv3-butler-unread"]').text()).toBe('3')
    await w1.find('button').trigger('click')
    expect(w1.emitted('toggle')).toHaveLength(1)

    const w2 = mount(ButlerFab, { props: { open: true, unread: 3 } })
    expect(w2.find('[data-testid="tv3-butler-unread"]').exists()).toBe(false)
  })
})

describe('ButlerPanel 对话流', () => {
  it('空态：欢迎语 + 快捷提问；上下文条跟随当前页面', () => {
    const w = mountPanel()
    expect(w.find('.tv3-butler__empty-title').text()).toContain('李老师')
    expect(w.findAll('.tv3-butler__chip').length).toBe(3) // B6：快捷动作随页面变化（slides=3）
    expect(w.find('[data-testid="tv3-butler-ctx"]').text()).toContain('课件工坊')
  })

  it('发送消息 → 教师气泡 + 管家流式回复 + 公式卡（KaTeX 渲染、可拖拽）', async () => {
    chatMock.mockImplementationOnce(sseReply([
      { event: 'meta', data: {} },
      { event: 'thinking', data: { text: '解析口语' } },
      { event: 'token', data: { text: '识别为' } },
      { event: 'token', data: { text: '公式' } },
      { event: 'card', data: { type: 'formula', id: 'f1', latex: '\\sqrt{x^{2}+y^{2}}', confidence: 0.88, source: 'chat' } },
      { event: 'done', data: {} },
    ]))
    const w = mountPanel()
    await w.find('[data-testid="tv3-butler-input"]').setValue('根号下x平方加y平方')
    await w.find('[data-testid="tv3-butler-send"]').trigger('click')
    await flushPromises()

    expect(chatMock).toHaveBeenCalledTimes(1)
    expect(chatMock.mock.calls[0][0].message).toBe('根号下x平方加y平方')
    expect(chatMock.mock.calls[0][0].context.route).toBe('/teacher-v3/slides')
    const msgs = w.findAll('[data-testid="tv3-butler-msg-teacher"]')
    expect(msgs.at(-1)!.text()).toContain('根号下x平方加y平方')
    expect(w.find('[data-testid="tv3-butler-msg-butler"]').text()).toContain('识别为公式')
    const card = w.find('[data-testid="tv3-butler-card-formula"]')
    expect(card.html()).toContain('katex')
    expect(card.attributes('draggable')).toBe('true')
    expect(card.text()).toContain('解析候选') // B0：不再显示伪造的精确置信度（诚实化后文案=“解析候选”+“未打开课件”小按钮）
    expect(w.emitted('activity')).toBeTruthy()
  })

  it('动作卡：默认 pending；点执行才调 confirmAction 并置 executed；取消不调用', async () => {
    const w = mountPanel()
    chatMock.mockImplementationOnce(sseReply([
      { event: 'card', data: { type: 'action', id: 'act-9', title: '存入题库', summary: '写入圆锥曲线', status: 'pending', confirm_required: true, params: { kp_name: '圆锥曲线' } } },
    ]))
    await w.find('[data-testid="tv3-butler-input"]').setValue('存入题库')
    await w.find('[data-testid="tv3-butler-send"]').trigger('click')
    await flushPromises()

    const actionCard = w.find('[data-testid="tv3-butler-card-action"]')
    expect(actionCard.text()).toContain('存入题库')
    expect(actionCard.text()).toContain('执行前请核对')

    await w.find('[data-testid="tv3-butler-action-confirm"]').trigger('click')
    await flushPromises()
    expect(confirmMock).toHaveBeenCalledWith('act-9', { params: { kp_name: '圆锥曲线' } })
    expect(w.find('[data-testid="tv3-butler-card-action"]').text()).toContain('已执行')
  })

  it('语音公式：转写事件实时可见，最终出语音来源公式卡', async () => {
    voiceMock.mockImplementationOnce(sseReply([
      { event: 'asr_partial', data: { text: '根号下' } },
      { event: 'asr_partial', data: { text: 'x平方' } },
      { event: 'asr_final', data: { text: '根号下x平方' } },
      { event: 'card', data: { type: 'formula', id: 'f2', latex: '\\sqrt{x^{2}}', confidence: 0.86, source: 'voice' } },
      { event: 'done', data: {} },
    ]))
    const w = mountPanel()
    await w.find('[data-testid="tv3-butler-mic"]').trigger('click')
    await w.find('[data-testid="tv3-butler-voice-input"]').setValue('根号下x平方')
    await w.find('[data-testid="tv3-butler-voice-send"]').trigger('click')
    await flushPromises()

    expect(voiceMock).toHaveBeenCalledTimes(1)
    const card = w.find('[data-testid="tv3-butler-card-formula"]')
    expect(card.text()).toContain('语音')
    expect(card.find('.tv3-butler__formula').html()).toContain('katex')
  })

  it('公式卡 dragstart → dataTransfer 双通道（mx/latex + x-v3-element，未确认态）', async () => {
    chatMock.mockImplementationOnce(sseReply([
      { event: 'card', data: { type: 'formula', id: 'f3', latex: 'e=\\frac{c}{a}', confidence: 0.92, source: 'chat' } },
    ]))
    const w = mountPanel()
    await w.find('[data-testid="tv3-butler-input"]').setValue('离心率')
    await w.find('[data-testid="tv3-butler-send"]').trigger('click')
    await flushPromises()

    const dt = { setData: vi.fn() }
    const ev = new Event('dragstart', { bubbles: true })
    Object.defineProperty(ev, 'dataTransfer', { value: dt })
    w.find('[data-testid="tv3-butler-card-formula"]').element.dispatchEvent(ev)

    const calls = dt.setData.mock.calls as [string, string][]
    expect(calls.find(([k]) => k === 'mx/latex')![1]).toBe('e=\\frac{c}{a}')
    const raw = calls.find(([k]) => k === 'application/x-v3-element')![1]
    const parsed = JSON.parse(raw)
    expect(parsed).toMatchObject({ type: 'formula', latex: 'e=\\frac{c}{a}', teacher_confirmed: false })
  })

  it('「插入本页」→ 全局 tv3-butler-insert 事件（携带结构化公式与未确认标记）', async () => {
    chatMock.mockImplementationOnce(sseReply([
      { event: 'card', data: { type: 'formula', id: 'f4', latex: 'y=x^{2}', confidence: 0.9, source: 'chat' } },
    ]))
    const spy = vi.fn()
    window.addEventListener('tv3-butler-insert', spy)
    mockRoute.query.deck = 'deck-1' // B0：插入按钮需要课件上下文（deck query）才可用
    const w = mountPanel()
    await w.find('[data-testid="tv3-butler-input"]').setValue('y=x平方')
    await w.find('[data-testid="tv3-butler-send"]').trigger('click')
    await flushPromises()

    // B0：有课件上下文时插入按钮可用（无上下文时显示"未打开课件"禁用态）
    expect(w.find('.tv3-butler__card--formula .tv3-butler__mini').attributes('disabled')).toBeUndefined()
    // B6：两步确认——第一次点出预览，确认才派发
    await w.find('.tv3-butler__card--formula .tv3-butler__mini').trigger('click')
    expect(spy).toHaveBeenCalledTimes(0)
    await w.find('[data-testid^="tv3-butler-insert-confirm-"]').trigger('click')
    window.removeEventListener('tv3-butler-insert', spy)
    expect(spy).toHaveBeenCalledTimes(1)
    expect(spy.mock.calls[0][0].detail).toMatchObject({ type: 'formula', latex: 'y=x^{2}', teacher_confirmed: false })
  })

  it('Esc / 关闭按钮 → close 事件', async () => {
    const w = mountPanel()
    await w.find('[data-testid="tv3-butler-close"]').trigger('click')
    expect(w.emitted('close')).toHaveLength(1)
  })
})
