/**
 * R3d G13 / G14 回归测试。
 *
 * - G14：content 重写（/regen）落库后，正文必须**确定性投影**回语义元素（画布按
 *   elements 渲染、导出按正文渲染 —— 同源才能 画布=导出=落库 三者一致）；
 * - G13：enrich 进度实时 n/N 只读作业 durable `progress` 事件（服务端每页真实完成
 *   发一条），前端禁止编造。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import {
  bodyElementsFromContent,
  isTeachingAnnotation,
  useDeckEditorStore,
  type DeckSlide,
} from '@/features/teacher-prep-center/stores/deckEditor'

function slideOf(partial: Partial<DeckSlide>): DeckSlide {
  return {
    id: 'slide-01',
    type: 'derivation',
    title: '点斜式方程推导',
    content: '',
    elements: [],
    ...partial,
  } as DeckSlide
}

describe('G14 · bodyElementsFromContent（正文→版式确定性投影）', () => {
  it('编号步骤行 → steps（去编号，渲染层重新编号），纯公式行 → formula', () => {
    const els = bodyElementsFromContent(
      [
        '1. 设直线$l$过定点$P_0(x_0,y_0)$，斜率为$k$',
        '2. 整理得点斜式方程',
        '$y - y_0 = k(x - x_0)$',
        '3. 代入截距得斜截式方程',
      ].join('\n'),
    )
    const steps = els.find((e) => e.role === 'steps')
    expect(steps).toBeTruthy()
    expect(steps?.items?.[0]).toBe('设直线$l$过定点$P_0(x_0,y_0)$，斜率为$k$')
    expect(steps?.items).toHaveLength(3)
    expect(els.find((e) => e.role === 'formula')?.latex).toBe('y - y_0 = k(x - x_0)')
  })

  it('无编号正文 → bullets；教研批注不上屏（与画布同口径）', () => {
    const els = bodyElementsFromContent(
      ['引导学生观察图形，归纳结论', '点斜式方程的形式', '斜截式方程的截距意义'].join('\n'),
    )
    // 第一行是教研批注（isTeachingAnnotation 判定）→ 不进元素
    expect(isTeachingAnnotation('引导学生观察图形，归纳结论')).toBe(true)
    const bullets = els.find((e) => e.role === 'bullets')
    expect(bullets?.items).toEqual(['点斜式方程的形式', '斜截式方程的截距意义'])
  })

  it('figure / note 保留（正文重排不丢图形与小贴士）', () => {
    const keep = [
      { role: 'figure' as const, figure_ref: 'math_object' as const },
      { role: 'note' as const, text: '注意适用条件' },
    ]
    const els = bodyElementsFromContent('1. 第一步\n2. 第二步', { keep })
    expect(els.map((e) => e.role)).toEqual(['figure', 'steps', 'note'])
    expect(els.find((e) => e.role === 'note')?.text).toBe('注意适用条件')
  })

  it('空正文 → 只保留 figure/note（不产出空元素）', () => {
    const els = bodyElementsFromContent('', { keep: [{ role: 'note' as const, text: 'x' }] })
    expect(els.map((e) => e.role)).toEqual(['note'])
  })
})

describe('G14 · syncBodyElementsFromContent（重写落库 → 画布可见）', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('按新正文投影元素并走 updateElements 落库通道', async () => {
    const store = useDeckEditorStore()
    store.deckId = 'deck-1'
    store.revision = 3
    store.deck = {
      deck_id: 'deck-1',
      revision: 3,
      slides: [
        slideOf({
          id: 'slide-01',
          content: '1. 新推导第一步\n2. 新推导第二步',
          elements: [
            { role: 'knowledge_card', title: '知识铺垫', items: ['旧内容'] },
            { role: 'note', text: '小贴士保留' },
          ],
        }),
      ],
    } as never
    const spy = vi.spyOn(store, 'updateElements').mockResolvedValue(true)

    await expect(store.syncBodyElementsFromContent('slide-01')).resolves.toBe(true)

    const [slideId, next] = spy.mock.calls[0] as [string, { role: string; items?: string[]; text?: string }[]]
    expect(slideId).toBe('slide-01')
    // 旧知识卡（正文）被新正文取代；note 保留
    expect(next.map((e) => e.role)).toEqual(['steps', 'note'])
    expect(next[0].items).toEqual(['新推导第一步', '新推导第二步'])
    expect(next[1].text).toBe('小贴士保留')
  })
})

describe('G13 · enrich 实时 n/N 只读 durable progress 事件', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.unstubAllGlobals()
  })

  it('轮询期间 enrichProgress 跟随 progress 事件递增，完成后与 enrich_result 计数共存', async () => {
    const store = useDeckEditorStore()
    store.deckId = 'deck-1'
    store.revision = 1
    store.deck = {
      deck_id: 'deck-1',
      revision: 1,
      slides: [slideOf({ id: 'slide-00', type: 'title', layout_hint: 'title' }), slideOf({ id: 'slide-01' })],
    } as never

    const seenProgress: Array<{ done: number; total: number } | null> = []
    let poll = 0
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        const u = String(url)
        if (u.includes('/enrich') && u.endsWith('/enrich')) {
          return new Response(JSON.stringify({ code: 0, message: 'ok', data: { job_id: 'job-1', deck_id: 'deck-1', status: 'pending' } }), { status: 200 })
        }
        poll += 1
        // 第 2 次轮询前记录进行中读数（n/N 必须来自 progress 事件，不是终态编造）
        seenProgress.push(store.enrichProgress ? { ...store.enrichProgress } : null)
        const body =
          poll < 3
            ? {
                code: 0,
                message: 'ok',
                data: {
                  job_id: 'job-1',
                  status: 'running',
                  events: [{ seq: 1, type: 'progress', detail: { stage: 'enrich_page', done: 2, total: 9, slide_id: 'slide-01' } }],
                },
              }
            : {
                code: 0,
                message: 'ok',
                data: {
                  job_id: 'job-1',
                  status: 'done',
                  events: [
                    { seq: 1, type: 'progress', detail: { stage: 'enrich_page', done: 2, total: 9, slide_id: 'slide-01' } },
                    { seq: 2, type: 'progress', detail: { stage: 'enrich_page', done: 9, total: 9, slide_id: 'slide-08' } },
                    { seq: 3, type: 'enrich_result', detail: { pages: 9, enriched_pages: 8, fallback_pages: 1, density_ok_pages: 9, total_ms: 44000, errors: {} } },
                  ],
                },
              }
        return new Response(JSON.stringify(body), { status: 200 })
      }),
    )

    await expect(store.startEnrich()).resolves.toBe(true)

    // 进行中：n/N 来自 progress 事件（2/9）
    expect(seenProgress[1]).toEqual({ done: 2, total: 9 })
    // 终态：最后一条 progress（9/9）+ enrich_result 真实计数并存
    expect(store.enrichProgress).toEqual({ done: 9, total: 9 })
    expect(store.enrichCounts).toMatchObject({ pages: 9, enriched: 8, fallback: 1, totalMs: 44000 })
    vi.unstubAllGlobals()
  })
})
