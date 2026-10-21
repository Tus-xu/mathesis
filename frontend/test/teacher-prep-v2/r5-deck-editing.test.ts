/**
 * R5「PPT 编辑能力对齐」回归测试。
 *
 * - G17 数学联动：改参数（a/b）→ 可识别的椭圆规范式公式（标准方程 / c²=a²−b² /
 *   c=√(a²−b²)）确定性代入并随 patch 落库；教师自撰公式不覆盖；非法参数（a ≤ b ≤ 0）拒绝代入；
 * - 文字/公式编辑：元素改动走 patch 通道（乐观并发 expected_revision），刷新后仍保留；
 * - 页操作：删除页（remove_slide）/ 页序调整（move_slide）/ 单页重试（/regen）。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { mount } from '@vue/test-utils'

import {
  deriveC,
  fmtNum,
  instantiateEllipseLatex,
  syncSlideFormulas,
  validEllipseParams,
} from '@/features/teacher-prep-center/math/ellipseSync'
import { useDeckEditorStore, type DeckSlide } from '@/features/teacher-prep-center/stores/deckEditor'
import SlideElements from '@/features/teacher-prep-center/ppt/SlideElements.vue'
import type { MathObject, PatchOp, SlideElement } from '@/features/teacher-prep-center/api/deckApi'

function slideOf(partial: Partial<DeckSlide>): DeckSlide {
  return {
    id: 'slide-01',
    type: 'derivation',
    title: '椭圆的简单几何性质',
    content: '',
    elements: [],
    ...partial,
  } as DeckSlide
}

const CANON_STD = String.raw`\frac{x^2}{a^2} + \frac{y^2}{b^2} = 1 \quad (a > b > 0)`
const CANON_SQRT = String.raw`c = \sqrt{a^2 - b^2}`
const CANON_SQ = String.raw`c^2 = a^2 - b^2`

describe('G17 · instantiateEllipseLatex（数据→公式同源，确定性代入）', () => {
  it('标准方程：分式代入 a²/b²，约束代入当前 a/b', () => {
    const out = instantiateEllipseLatex(CANON_STD, { a: 5, b: 3 })
    expect(out).toBe(String.raw`\frac{x^2}{25} + \frac{y^2}{9} = 1 (5 > 3 > 0)`)
    // 产物反泄漏：间距命令不写回 spec（8201 会把 \quad 裸奔进 PPTX <a:t>）
    expect(out).not.toContain('\\quad')
  })

  it('c=√(a²−b²)：代入数值并给派生结果；c²=a²−b² 同步', () => {
    expect(instantiateEllipseLatex(CANON_SQRT, { a: 5, b: 3 })).toBe(String.raw`c = \sqrt{25 - 9} = 4`)
    expect(instantiateEllipseLatex(CANON_SQ, { a: 5, b: 3 })).toBe(String.raw`c^2 = 25 - 9 = 16`)
    expect(instantiateEllipseLatex('c² = a² − b²', { a: 5, b: 3 })).toBe('c² = 25 − 9 = 16')
  })

  it('幂等可重入：已代入数值式在再次改参后仍同步', () => {
    const once = instantiateEllipseLatex(CANON_STD, { a: 5, b: 3 })
    const twice = instantiateEllipseLatex(once, { a: 6, b: 3 })
    expect(twice).toBe(String.raw`\frac{x^2}{36} + \frac{y^2}{9} = 1 (6 > 3 > 0)`)
    const sqrtTwice = instantiateEllipseLatex(instantiateEllipseLatex(CANON_SQRT, { a: 5, b: 3 }), { a: 6, b: 3 })
    expect(sqrtTwice).toBe(String.raw`c = \sqrt{36 - 9} = 5.2`)
  })

  it('教师自撰公式不覆盖（无规范式命中 → 原样返回）', () => {
    const custom = String.raw`S = \pi a b`
    expect(instantiateEllipseLatex(custom, { a: 5, b: 3 })).toBe(custom)
  })

  it('非法参数（a ≤ b ≤ 0）拒绝代入，原式保留', () => {
    expect(validEllipseParams(3, 3)).toBe(false)
    expect(validEllipseParams(2, 3)).toBe(false)
    expect(validEllipseParams(0, -1)).toBe(false)
    expect(instantiateEllipseLatex(CANON_STD, { a: 3, b: 3 })).toBe(CANON_STD)
  })

  it('派生口径与画布一致：c=√(a²−b²)，数值显示去尾零', () => {
    expect(deriveC(5, 3)).toBe(4)
    expect(fmtNum(25)).toBe('25')
    expect(fmtNum(4.6)).toBe('4.6')
  })
})

describe('G17 · syncSlideFormulas（公式随参数落库；只改变化部分）', () => {
  const math: MathObject = { kind: 'ellipse', params: { a: 5, b: 3 } }

  it('elements 里的 formula/knowledge_card 公式同步；figure/note 不动', () => {
    const els: SlideElement[] = [
      { role: 'figure', figure_ref: 'math_object' },
      { role: 'formula', latex: CANON_SQRT },
      { role: 'knowledge_card', title: '知识要点', latex: CANON_STD, items: ['要点一'] },
      { role: 'note', text: '小贴士' },
    ]
    const out = syncSlideFormulas({ elements: els }, math)
    expect(out.elements?.[1].latex).toBe(String.raw`c = \sqrt{25 - 9} = 4`)
    expect(out.elements?.[2].latex).toBe(String.raw`\frac{x^2}{25} + \frac{y^2}{9} = 1 (5 > 3 > 0)`)
    expect(out.elements?.[0]).toBe(els[0])
    expect(out.elements?.[3]).toBe(els[3])
    expect(out.content).toBeUndefined()
  })

  it('content 纯公式行同步；普通文本行不动', () => {
    const out = syncSlideFormulas({ content: `椭圆定义\n$${CANON_SQ}$\n要点行` }, math)
    expect(out.content).toBe(`椭圆定义\n$c^2 = 25 - 9 = 16$\n要点行`)
  })

  it('无规范式命中 / 非法参数 → 不产出 patch 内容', () => {
    expect(syncSlideFormulas({ elements: [{ role: 'formula', latex: 'S = \\pi a b' }] }, math)).toEqual({})
    expect(syncSlideFormulas({ elements: [{ role: 'formula', latex: CANON_STD }] }, { kind: 'ellipse', params: { a: 3, b: 3 } })).toEqual({})
    expect(syncSlideFormulas({ elements: [{ role: 'formula', latex: CANON_STD }] }, null)).toEqual({})
  })
})

describe('R5 · deckEditor patch 通道（文字/公式/参数/页操作真实落库）', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  function seeded() {
    const store = useDeckEditorStore()
    store.deckId = 'deck-1'
    store.revision = 7
    store.deck = {
      deck_id: 'deck-1',
      revision: 7,
      slides: [
        slideOf({ id: 's-1', elements: [{ role: 'formula', latex: CANON_STD }] }),
        slideOf({ id: 's-2', elements: [{ role: 'bullets', items: ['原文'] }] }),
      ],
    } as never
    return store
  }

  it('改参数 → 一个 patch 内同步 math_object 与公式（G17 落库）', async () => {
    const store = seeded()
    const spy = vi.spyOn(store, 'patch').mockResolvedValue(true)
    await expect(
      store.updateMathObject('s-1', { kind: 'ellipse', params: { a: 6, b: 3 } }),
    ).resolves.toBe(true)
    const [ops] = spy.mock.calls[0] as [PatchOp[], string]
    expect(ops[0]).toMatchObject({ op: 'set_slide', target: 's-1', field: 'math_object' })
    const elsOp = ops.find((o) => o.field === 'elements')
    expect(elsOp).toBeTruthy()
    expect((elsOp?.value as SlideElement[])[0].latex).toBe(String.raw`\frac{x^2}{36} + \frac{y^2}{9} = 1 (6 > 3 > 0)`)
  })

  it('文字元素编辑 → set_slide.elements 落库（乐观并发）', async () => {
    const store = seeded()
    const spy = vi.spyOn(store, 'patch').mockResolvedValue(true)
    await store.updateElement('s-2', 0, { role: 'bullets', items: ['改后文本R5X7'] })
    const [ops, summary] = spy.mock.calls[0] as [PatchOp[], string]
    expect(ops).toEqual([
      { op: 'set_slide', target: 's-2', field: 'elements', value: [{ role: 'bullets', items: ['改后文本R5X7'] }] },
    ])
    expect(summary).toContain('s-2')
  })

  it('公式编辑 → 只改对应元素 latex', async () => {
    const store = seeded()
    const spy = vi.spyOn(store, 'patch').mockResolvedValue(true)
    await store.updateElement('s-1', 0, { role: 'formula', latex: 'y = kx + b' })
    const [ops] = spy.mock.calls[0] as [PatchOp[], string]
    expect((ops[0].value as SlideElement[])[0]).toEqual({ role: 'formula', latex: 'y = kx + b' })
  })

  it('页标题编辑 → set_slide.title；空标题拒绝（不发 patch）', async () => {
    const store = seeded()
    const spy = vi.spyOn(store, 'patch').mockResolvedValue(true)
    await expect(store.updateSlideTitle('s-2', '   ')).resolves.toBe(false)
    expect(spy).not.toHaveBeenCalled()
    await expect(store.updateSlideTitle('s-2', '椭圆焦点与焦距')).resolves.toBe(true)
    expect(spy.mock.calls[0][0]).toEqual([
      { op: 'set_slide', target: 's-2', field: 'title', value: '椭圆焦点与焦距' },
    ])
  })

  it('页操作：删除页 → remove_slide；页序调整 → move_slide（0 基目标位）', async () => {
    const store = seeded()
    const spy = vi.spyOn(store, 'patch').mockResolvedValue(true)
    await store.removePage(1)
    expect(spy.mock.calls[0][0]).toEqual([{ op: 'remove_slide', target: 's-2' }])
    await store.movePage(1, -1)
    expect(spy.mock.calls[1][0]).toEqual([{ op: 'move_slide', target: 's-2', value: { index: 0 } }])
  })
})

describe('R5 · SlideElements 双击编辑（文字 + 公式，真实 emit 落库事件）', () => {
  it('双击要点条目 → 输入改文 → Enter 提交 elementChange', async () => {
    const wrapper = mount(SlideElements, {
      props: {
        elements: [{ role: 'bullets', items: ['原文A'] } as SlideElement],
        editable: true,
      },
    })
    await wrapper.find('.pc-el__li').trigger('dblclick')
    const input = wrapper.find('input.pc-el__edit')
    expect(input.exists()).toBe(true)
    await input.setValue('改后文本R5X7')
    await input.trigger('keydown.enter')
    const emitted = wrapper.emitted('elementChange')
    expect(emitted).toHaveLength(1)
    expect(emitted?.[0][0]).toEqual({ index: 0, next: { role: 'bullets', items: ['改后文本R5X7'] } })
  })

  it('双击公式胶囊 → LaTeX 编辑（含 KaTeX 预览，禁裸源码上屏）→ 提交新 latex', async () => {
    const wrapper = mount(SlideElements, {
      props: {
        elements: [{ role: 'formula', latex: 'c = \\sqrt{a^2 - b^2}' } as SlideElement],
        editable: true,
      },
    })
    await wrapper.find('.pc-el__capsule').trigger('dblclick')
    const area = wrapper.find('textarea.pc-el__edit-area')
    expect(area.exists()).toBe(true)
    // 编辑态仍渲染 KaTeX 预览（不是只有裸源码）
    expect(wrapper.find('.pc-el__formula-preview').exists()).toBe(true)
    await area.setValue('y = kx + b')
    await area.trigger('keydown.enter')
    const emitted = wrapper.emitted('elementChange')
    expect(emitted).toHaveLength(1)
    expect((emitted?.[0][0] as { next: SlideElement }).next).toEqual({ role: 'formula', latex: 'y = kx + b' })
  })

  it('空公式拒绝提交；Esc 取消编辑', async () => {
    const wrapper = mount(SlideElements, {
      props: {
        elements: [{ role: 'formula', latex: 'x = 1' } as SlideElement],
        editable: true,
      },
    })
    await wrapper.find('.pc-el__capsule').trigger('dblclick')
    await wrapper.find('textarea.pc-el__edit-area').setValue('   ')
    await wrapper.find('textarea.pc-el__edit-area').trigger('keydown.enter')
    expect(wrapper.emitted('elementChange')).toBeUndefined()

    await wrapper.find('.pc-el__capsule').trigger('dblclick')
    await wrapper.find('textarea.pc-el__edit-area').trigger('keydown.esc')
    expect(wrapper.find('textarea.pc-el__edit-area').exists()).toBe(false)
    expect(wrapper.emitted('elementChange')).toBeUndefined()
  })
})
/* ── R5 编辑体验修复回归（失败可见+可重试 / 元素操作 / 提交前校验 / 公式增强） ── */
describe('R5 · 保存失败可见与队列重发（flush 基于最新状态重新推导）', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  function seededQueue() {
    const store = useDeckEditorStore()
    store.deckId = 'deck-1'
    store.revision = 7
    store.deck = {
      deck_id: 'deck-1',
      revision: 7,
      slides: [
        slideOf({ id: 's-1', elements: [{ role: 'formula', latex: CANON_STD }] }),
        slideOf({ id: 's-2', elements: [{ role: 'bullets', items: ['原文'] }] }),
      ],
    } as never
    return store
  }

  it('patchBusy 期间的编辑入队（不静默丢弃），flush 时基于最新状态重定位写回', async () => {
    const store = seededQueue()
    store.patchBusy = true // 模拟一次保存正在进行（编辑临界）
    await expect(store.updateElement('s-2', 0, { role: 'bullets', items: ['排队编辑B'] })).resolves.toBe(true)
    expect(store.pendingPatchCount).toBe(1)
    // 排队期间状态推进：另一处变更先落库（最新数组多了一条，原条目移到 index 1）
    store.slides[1].elements = [
      { role: 'bullets', items: ['排队期间新增A'] },
      { role: 'bullets', items: ['原文'] },
    ]
    store.patchBusy = false
    const spy = vi.spyOn(store, 'patch').mockResolvedValue(true)
    await store.flushQueue()
    // 重发重定位：base（items=['原文']）现位于 index 1 → 写回 index 1，绝不回退 index 0 的新增
    const [ops] = spy.mock.calls[0] as [PatchOp[], string]
    expect(ops[0].value).toEqual([
      { role: 'bullets', items: ['排队期间新增A'] },
      { role: 'bullets', items: ['排队编辑B'] },
    ])
    expect(store.pendingPatchCount).toBe(0)
  })

  it('patch 失败 → saveState=error 且失败操作入队；重试成功后清空并回到 saved', async () => {
    const store = seededQueue()
    const spy = vi.spyOn(store, 'patch').mockResolvedValueOnce(false).mockResolvedValueOnce(true)
    await expect(store.updateContent('s-2', '排队后正文R5X9')).resolves.toBe(false)
    expect(store.saveState).toBe('error')
    expect(store.saveError).toBeTruthy()
    expect(store.pendingPatchCount).toBe(1)
    await expect(store.retrySave()).resolves.toBe(true)
    expect(spy).toHaveBeenCalledTimes(2)
    expect(store.saveState).toBe('saved')
    expect(store.pendingPatchCount).toBe(0)
  })

  it('错误态期间的继续编辑继续入队；放弃修改后清空', async () => {
    const store = seededQueue()
    vi.spyOn(store, 'patch').mockResolvedValue(false)
    await store.updateContent('s-2', '第一处失败')
    await store.updateSlideTitle('s-2', '排队标题R5X9')
    expect(store.pendingPatchCount).toBe(2)
    await store.discardPendingEdits()
    expect(store.pendingPatchCount).toBe(0)
    expect(store.saveState).not.toBe('error')
  })
})

describe('R5 · 元素操作（removeElement / moveElement：同 role 邻居间）', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  function seededOps() {
    const store = useDeckEditorStore()
    store.deckId = 'deck-1'
    store.revision = 7
    store.deck = {
      deck_id: 'deck-1',
      revision: 7,
      slides: [
        slideOf({
          id: 's-1',
          elements: [
            { role: 'definition', text: '定义带' },
            { role: 'bullets', items: ['要点一'] },
            { role: 'bullets', items: ['要点二'] },
            { role: 'formula', latex: 'x = 1' },
          ],
        }),
      ],
    } as never
    return store
  }

  it('removeElement → 整体落库且元素被移除', async () => {
    const store = seededOps()
    const spy = vi.spyOn(store, 'patch').mockResolvedValue(true)
    await store.removeElement('s-1', 1)
    const [ops] = spy.mock.calls[0] as [PatchOp[], string]
    expect(ops[0]).toMatchObject({ op: 'set_slide', target: 's-1', field: 'elements' })
    expect(ops[0].value).toEqual([
      { role: 'definition', text: '定义带' },
      { role: 'bullets', items: ['要点二'] },
      { role: 'formula', latex: 'x = 1' },
    ])
  })

  it('moveElement：同 role 邻居换位；跨 role / 越界拒绝（不发 patch）', async () => {
    const store = seededOps()
    const spy = vi.spyOn(store, 'patch').mockResolvedValue(true)
    await store.moveElement('s-1', 2, -1) // bullets(2) ↔ bullets(1)：同 role 邻居
    const [ops] = spy.mock.calls[0] as [PatchOp[], string]
    expect(ops[0].value).toEqual([
      { role: 'definition', text: '定义带' },
      { role: 'bullets', items: ['要点二'] },
      { role: 'bullets', items: ['要点一'] },
      { role: 'formula', latex: 'x = 1' },
    ])
    spy.mockClear()
    await store.moveElement('s-1', 1, -1) // bullets(1) ↔ definition(0)：跨 role → 拒绝
    expect(spy).not.toHaveBeenCalled()
    await store.moveElement('s-1', 3, 1) // 越界 → 拒绝
    expect(spy).not.toHaveBeenCalled()
  })
})

describe('R5 · 提交前预校验与公式编辑增强（SlideElements）', () => {
  const OVER_TEXT = '超'.repeat(401)
  const OVER_ITEM = '超'.repeat(201)

  it('正文超 400 字 → 行内红字提示且不 emit（不再静默丢弃）', async () => {
    const wrapper = mount(SlideElements, {
      props: { elements: [{ role: 'definition', text: '定义' } as SlideElement], editable: true },
    })
    await wrapper.find('.pc-el__def').trigger('dblclick')
    const input = wrapper.find('input.pc-el__edit')
    await input.setValue(OVER_TEXT)
    await input.trigger('keydown.enter')
    expect(wrapper.emitted('elementChange')).toBeUndefined()
    expect(wrapper.find('.pc-el__edit-hint').exists()).toBe(true)
    expect(wrapper.find('.pc-el__edit-hint').text()).toContain('400')
    // 编辑器保持打开（可就地精简）
    expect(wrapper.find('input.pc-el__edit').exists()).toBe(true)
  })

  it('列表条目超 200 字 → 行内红字提示且不 emit', async () => {
    const wrapper = mount(SlideElements, {
      props: { elements: [{ role: 'bullets', items: ['原文'] } as SlideElement], editable: true },
    })
    await wrapper.find('.pc-el__li').trigger('dblclick')
    const input = wrapper.find('input.pc-el__edit')
    await input.setValue(OVER_ITEM)
    await input.trigger('keydown.enter')
    expect(wrapper.emitted('elementChange')).toBeUndefined()
    expect(wrapper.find('.pc-el__edit-hint').text()).toContain('200')
  })

  it('latex 超 2000 → 行内红字提示且不 emit（消除静默 return）', async () => {
    const wrapper = mount(SlideElements, {
      props: { elements: [{ role: 'formula', latex: 'x = 1' } as SlideElement], editable: true },
    })
    await wrapper.find('.pc-el__capsule').trigger('dblclick')
    const area = wrapper.find('textarea.pc-el__edit-area')
    await area.setValue(`x ${'+'.repeat(2010)} 1`)
    await area.trigger('keydown.enter')
    expect(wrapper.emitted('elementChange')).toBeUndefined()
    expect(wrapper.find('.pc-el__edit-hint').text()).toContain('2000')
  })

  it('KaTeX 语法错误 → 预览区出现中文提示行；合法公式不出现', async () => {
    const wrapper = mount(SlideElements, {
      props: { elements: [{ role: 'formula', latex: 'x = 1' } as SlideElement], editable: true },
    })
    await wrapper.find('.pc-el__capsule').trigger('dblclick')
    const area = wrapper.find('textarea.pc-el__edit-area')
    await area.setValue('\\frac{') // 非法：katex-error 红字兜底
    expect(wrapper.find('.pc-el__formula-error').exists()).toBe(true)
    expect(wrapper.find('.pc-el__formula-error').text()).toContain('公式语法有误，请检查')
    await area.setValue('y = kx + b')
    expect(wrapper.find('.pc-el__formula-error').exists()).toBe(false)
  })

  it('符号快捷插入：点击分数按钮在光标处插入 \\frac{}{}', async () => {
    const wrapper = mount(SlideElements, {
      props: { elements: [{ role: 'formula', latex: 'x = 1' } as SlideElement], editable: true },
    })
    await wrapper.find('.pc-el__capsule').trigger('dblclick')
    const area = wrapper.find('textarea.pc-el__edit-area')
    await area.setValue('x^2 = 4')
    ;(area.element as HTMLTextAreaElement).setSelectionRange(1, 1)
    await wrapper.find('.pc-el__symbols .pc-el__symbol').trigger('click')
    expect((wrapper.find('textarea.pc-el__edit-area').element as HTMLTextAreaElement).value).toBe('x\\frac{}{}^2 = 4')
    expect(wrapper.findAll('.pc-el__symbol').length).toBeGreaterThanOrEqual(10)
  })

  it('元素块操作条：删除 emit 真实下标；跨 role 邻居的移动按钮禁用', async () => {
    const wrapper = mount(SlideElements, {
      props: {
        elements: [
          { role: 'definition', text: '定义带' },
          { role: 'bullets', items: ['要点一'] },
          { role: 'bullets', items: ['要点二'] },
          { role: 'formula', latex: 'x = 1' },
        ] as SlideElement[],
        editable: true,
      },
    })
    const blocks = wrapper.findAll('.pc-el__block') // 两个 bullets 各自成块
    expect(blocks.length).toBe(2)
    const firstOps = blocks[0].findAll('.pc-el__op')
    // bullets(1)：上邻 definition（跨 role）→ 上移禁用；下邻 bullets(2)（同 role）→ 下移可用
    expect((firstOps[0].element as HTMLButtonElement).disabled).toBe(true)
    expect((firstOps[1].element as HTMLButtonElement).disabled).toBe(false)
    await firstOps[2].trigger('click') // 删除 bullets(1) 块
    expect(wrapper.emitted('elementRemove')?.[0]).toEqual([1])
    // bullets(2)：上邻 bullets(1)（同 role）→ 上移可用；下邻 formula（跨 role）→ 下移禁用
    const secondOps = blocks[1].findAll('.pc-el__op')
    expect((secondOps[0].element as HTMLButtonElement).disabled).toBe(false)
    expect((secondOps[1].element as HTMLButtonElement).disabled).toBe(true)
    await secondOps[0].trigger('click')
    expect(wrapper.emitted('elementMove')?.[0]).toEqual([2, -1])
  })
})
