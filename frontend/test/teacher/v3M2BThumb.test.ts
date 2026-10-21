// A0 M2-B · 模板真实图卡（IFC-003 / 03-ACCEPTANCE L4 判定①）
//  1. mock 契约：GET /deck-templates 每项带服务端渲染 thumb（additive）
//  2. WorkshopFlow 模板卡：thumb 渲染 <img>；无 thumb/加载失败回落 swatch 骨架（绝不破图）
import { describe, it, expect, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { handleTeacherV3Api } from '@/mock/teacherV3Server'
import WorkshopFlow from '@/components/teacherV3/workshop/WorkshopFlow.vue'

function toReq(over: { method: string; url: string; headers?: Record<string, string> }) {
  return {
    method: over.method,
    url: over.url,
    headers: { authorization: 'Bearer mock-token-teacher-preview', ...(over.headers || {}) },
    on: (_ev: string, _cb: (c?: any) => void) => {},
  } as any
}
function captureRes() {
  const res: any = {
    statusCode: 0, headers: {}, writableEnded: false, destroyed: false,
    chunks: [] as string[], body: null as any,
    setHeader(k: string, v: string) { this.headers[k] = v },
    writeHead() { this.statusCode = 200 },
    write(chunk: string) { this.chunks.push(String(chunk)); return true },
    on() {},
    end(body?: string) {
      if (typeof body === 'string' && body) { try { this.body = JSON.parse(body) } catch { this.body = body } }
      if (this.statusCode === 0) this.statusCode = 200
      this.writableEnded = true
    },
  }
  return res
}

describe('mock 契约 · 模板缩略图（IFC-003）', () => {
  it('deck-templates 每项带服务端渲染 thumb（SVG data URL），既有字段不动', async () => {
    const res = captureRes()
    await handleTeacherV3Api(toReq({ method: 'GET', url: '/teacher-v3/deck-templates' }), res)
    expect(res.statusCode).toBe(200)
    const items = res.body.data.items
    expect(items.length).toBeGreaterThanOrEqual(5)
    for (const t of items) {
      expect(t.thumb).toMatch(/^data:image\/svg\+xml/)
      expect(t.swatch).toBeTruthy() // 既有字段仍在（骨架回落数据源）
      expect(t.page_kinds.length).toBeGreaterThan(0)
    }
  })
})

describe('WorkshopFlow 模板卡 · thumb 渲染与骨架兜底', () => {
  const THUMB = 'data:image/svg+xml;charset=utf-8,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%2F%3E'
  const TPLS = [
    { id: 'tpl-with-thumb', name: '有图模板', style: 'academic', swatch: { bg: '#0f4787', primary: '#0f4787', accent: '#c99735', light: true }, page_kinds: ['cover'], recommended_for: '新授课', thumb: THUMB },
    { id: 'tpl-no-thumb', name: '无图模板', style: 'minimal', swatch: { bg: '#ffffff', primary: '#23272e', accent: '#3b82c4', light: true }, page_kinds: ['cover'], recommended_for: '复习课' },
  ]
  function wsFixture() {
    return {
      screen: 'template', screenTitleSub: '椭圆及其标准方程',
      tplFiltered: TPLS, tplFilter: '全部',
      form: { template_id: '', font_tier: 'standard', margin_notes: true },
      currentTemplate: null, previewTopic: '椭圆及其标准方程', className: '高二(3)班',
      applyScope: 'all', fontCards: [{ value: 'standard', name: '标准档' }],
      gateOutline: [{}, {}],
      openHome: vi.fn(), prevTemplate: vi.fn(), startGenerate: vi.fn(),
      chapters: null, classes: [], decks: [], plans: [], photos: [],
    }
  }

  it('有 thumb 渲染 <img>；无 thumb 渲染 swatch 骨架', () => {
    const w = mount(WorkshopFlow, { props: { ws: wsFixture() } })
    const img = w.find('img.ws-tplthumb')
    expect(img.exists()).toBe(true)
    expect(img.attributes('src')).toBe(THUMB)
    expect(img.attributes('loading')).toBe('lazy')
    // 无 thumb 的卡片：走骨架（ws-tplprev-cover 存在于该卡内）
    const cards = w.findAll('.ws-tplcard')
    expect(cards.length).toBe(2)
    expect(cards[1].find('.ws-tplprev-cover').exists()).toBe(true)
    expect(cards[1].find('img.ws-tplthumb').exists()).toBe(false)
  })

  it('thumb 加载失败（@error）→ 回落骨架，绝不破图', async () => {
    const w = mount(WorkshopFlow, { props: { ws: wsFixture() } })
    await w.find('img.ws-tplthumb').trigger('error')
    await flushPromises()
    expect(w.find('img.ws-tplthumb').exists()).toBe(false)
    const cards = w.findAll('.ws-tplcard')
    expect(cards[0].find('.ws-tplprev-cover').exists()).toBe(true)
  })

  it('选中模板后点开始生成（沿用既有交互，零改布局）', async () => {
    const ws = wsFixture()
    const w = mount(WorkshopFlow, { props: { ws } })
    await w.find('[data-testid="tv3-tpl-tpl-with-thumb"]').trigger('click')
    expect(ws.form.template_id).toBe('tpl-with-thumb')
  })
})
