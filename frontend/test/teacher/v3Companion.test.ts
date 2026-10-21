// C2 伴随工具层契约测试：教学意图镜头 / 四层来源候选 / 视频引用卡结构 / companion 仓互斥与暂存 / Butler tool 卡。
// 依据：《教师端V3-伴随式资源库与B站教学素材创新方案.md》§5-§8 + 执行提示词 §18（AI 调用工具不做万能聊天）。
// WP-K / Q-1：候选池是**虚构演示数据**，现由 VITE_USE_MOCK=1 门控 —— 相关用例显式 stub 环境变量。
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { handleTeacherV3Api } from '@/mock/teacherV3Server'
import {
  COMPANION_INTENTS, detectIntent, buildCandidates, companionDemoEnabled,
} from '@/pages/teacher-v3/companionData'
import { useCompanion, openTool, closeTool, stashFigure, removeStash, markStashUsed, setReceipt, registerUndo, undoLast, __resetCompanionForTest } from '@/stores/companion'

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
    statusCode: 0, headers: {}, writableEnded: false, destroyed: false, chunks: [], body: null as any,
    setHeader() {}, writeHead(code: number) { this.statusCode = code },
    write(chunk: string) { this.chunks.push(String(chunk)); return true },
    end() { if (this.statusCode === 0) this.statusCode = 200; this.writableEnded = true },
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

describe('C2 教学意图镜头（detectIntent）', () => {
  it('Prep 环节名 → 对应意图；课件页无选择时诚实返回 null（不假装知道）', () => {
    expect(detectIntent({ selectionSummary: '环节「例题精讲」教师活动光标处' })).toBe('例题精讲')
    expect(detectIntent({ sectionName: '变式训练' })).toBe('变式迁移')
    expect(detectIntent({ selectionSummary: '环节「课堂小结与检测」' })).toBe('理解检查')
    expect(detectIntent({ sectionName: '情境引入' })).toBe('概念发生')
    expect(detectIntent({})).toBeNull()
    expect(detectIntent({ selectionSummary: '某个毫不匹配的字符串' })).toBeNull()
  })
})

describe('C2 候选数据（四层来源 + 意图过滤 + 视频卡诚实结构）', () => {
  beforeEach(() => { __resetCompanionForTest(); vi.stubEnv('VITE_USE_MOCK', '1') })
  afterEach(() => { vi.unstubAllEnvs() })

  it('Q-1 生产门控：未开演示时任何意图/来源层都返回空候选（不编造资源）', () => {
    vi.stubEnv('VITE_USE_MOCK', '0')
    expect(companionDemoEnabled()).toBe(false)
    for (const intent of COMPANION_INTENTS) {
      for (const layer of ['mine', 'team', 'official', 'external'] as const) {
        expect(buildCandidates(intent, layer)).toEqual([])
        expect(buildCandidates(intent, layer, '椭圆')).toEqual([])
      }
    }
  })

  it('每个意图在「我的」层有 1-5 条候选，字段回答七问（来源/为什么/几分钟/变成什么）', () => {
    for (const intent of COMPANION_INTENTS) {
      const items = buildCandidates(intent, 'mine')
      expect(items.length).toBeGreaterThanOrEqual(1)
      expect(items.length).toBeLessThanOrEqual(5)
      for (const c of items) {
        expect(c.source).toBeTruthy()
        expect(c.why).toBeTruthy()
        expect(c.becomes).toBeTruthy()
        expect(c.intents).toContain(intent)
      }
    }
  })

  it('来源层隔离：外部层全是 external 且外链/视频带 url；教材层标记官方来源', () => {
    for (const c of buildCandidates('概念发生', 'external')) {
      expect(['video', 'link']).toContain(c.kind)
      expect(c.url).toBeTruthy()
    }
    for (const c of buildCandidates('例题精讲', 'official')) {
      expect(c.source).toMatch(/人教A版|教材/)
    }
  })

  it('视频卡必须带时间轴与播放前/后问（教学动作而非 URL 收藏），且不提供下载语义字段', () => {
    const vids = buildCandidates('动态演示', 'external').filter((c) => c.kind === 'video')
    expect(vids.length).toBeGreaterThanOrEqual(1)
    for (const v of vids) {
      expect(v.video?.start).toMatch(/^\d{2}:\d{2}$/)
      expect(v.video?.pre).toBeTruthy()
      expect(v.video?.post).toBeTruthy()
      expect(v.note).toContain('不下载')
    }
  })

  it('关键词过滤生效（搜索是第二层，不是第一层）', () => {
    expect(buildCandidates('概念发生', 'mine', '拉链').length).toBe(0) // 「拉链」在外部层视频卡与教材层
    expect(buildCandidates('概念发生', 'external', '拉链').length).toBeGreaterThanOrEqual(1)
  })
})

describe('C2 companion 仓：互斥 / 回执撤销 / 绘图暂存', () => {
  beforeEach(() => __resetCompanionForTest())

  it('打开一个工具即关闭另一个（覆盖层互斥）', () => {
    const s = useCompanion()
    openTool('resource')
    expect(s.open).toBe('resource')
    openTool('draw', { targetLabel: '第 6 页', insertLabel: '插入第 6 页' })
    expect(s.open).toBe('draw')
    expect(s.drawContext?.insertLabel).toBe('插入第 6 页')
    closeTool()
    expect(s.open).toBeNull()
  })

  it('回执 + 撤销：registerUndo 后 undoLast 执行且清回执', () => {
    const s = useCompanion()
    let undone = 0
    openTool('resource')
    setReceipt({ ok: true, message: '已插入「教材例 2」', locationLabel: '例题精讲 · 已挂例题' })
    registerUndo('撤销', () => { undone += 1 })
    expect(s.receipt?.undoLabel).toBe('撤销')
    undoLast()
    expect(undone).toBe(1)
    expect(s.receipt).toBeNull()
  })

  it('绘图暂存：localStorage 持久、可标记已用、可移除', () => {
    const s = useCompanion()
    stashFigure({ name: '正方体截面', kind: 'free', thumb: 'data:image/svg+xml;utf8,test', records: [] })
    expect(s.stash.length).toBe(1)
    markStashUsed(s.stash[0].id)
    expect(s.stash[0].used).toBe(true)
    removeStash(s.stash[0].id)
    expect(s.stash.length).toBe(0)
    expect(localStorage.getItem('tv3-draw-stash')).toBe('[]')
  })
})

describe('C2 Butler tool 卡：找资源/画图 → 打开伴随工具，不在聊天里丢题', () => {
  it('「帮我找几道椭圆的题」→ find_resource 意图 + resource 工具卡（无题目正文卡片）', async () => {
    const res = captureRes()
    await handleTeacherV3Api(toReq({
      method: 'POST', url: '/teacher-v3/butler/chat',
      body: { message: '帮我找几道椭圆的题', context: { route: '/teacher-v3/prep' } },
    }), res)
    const events = parseSse(res)
    const meta = events.find((e) => e.event === 'meta')
    expect(meta?.data.intent).toBe('find_resource')
    const card = events.find((e) => e.event === 'card' && e.data.type === 'tool')
    expect(card?.data.tool).toBe('resource')
    const tokens = events.filter((e) => e.event === 'token').map((e) => e.data.text).join('')
    expect(tokens).not.toMatch(/\\frac/) // 聊天里不丢题目内容
  }, 15000)

  it('「画一个椭圆焦点动态图」→ draw 工具卡；且「做个课件」仍走既有 deck 剧本（不回归）', async () => {
    const res = captureRes()
    await handleTeacherV3Api(toReq({
      method: 'POST', url: '/teacher-v3/butler/chat',
      body: { message: '画一个能解释椭圆两个焦点的动态图', context: { route: '/teacher-v3/prep' } },
    }), res)
    const card = parseSse(res).find((e) => e.event === 'card' && e.data.type === 'tool')
    expect(card?.data.tool).toBe('draw')

    const res2 = captureRes()
    await handleTeacherV3Api(toReq({
      method: 'POST', url: '/teacher-v3/butler/chat',
      body: { message: '帮我做一份《双曲线》的课件', context: { route: '/teacher-v3/today' } },
    }), res2)
    const events2 = parseSse(res2)
    expect(events2.find((e) => e.event === 'meta')?.data.intent).toBe('deck_generate')
    expect(events2.some((e) => e.event === 'card' && e.data.type === 'tool')).toBe(false)
  }, 15000)
})
