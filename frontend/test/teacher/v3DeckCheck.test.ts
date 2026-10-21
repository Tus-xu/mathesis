/**
 * B3 · 可讲性体检（确定性检查 + 修复应用）单测
 */
import { describe, it, expect } from 'vitest'
import { checkDeck, applyFix } from '@/pages/teacher-v3/deckCheck'
import type { V3Deck, V3Slide, V3Element } from '@/types/teacherV3'

const el = (o: Partial<V3Element> & { type: V3Element['type']; left: number; top: number; width: number; height: number }): V3Element =>
  ({ id: o.id || `e${Math.random()}`, z: 1, ...o } as V3Slide['elements'][number])

const slide = (layout: V3Slide['layout'], elements: V3Element[]): V3Slide => ({ id: `sl-${layout}-${Math.random()}`, layout, elements })

function baseDeck(slides: V3Slide[]): V3Deck {
  return { id: 'deck-t', title: '测试课件', template_id: 'tpl-academic-blue', source: 'topic', slides, updated_at: '' } as unknown as V3Deck
}

describe('deckCheck · 确定性规则', () => {
  it('字号过小 → error（带放大修复）；正常字号不报', () => {
    const deck = baseDeck([
      slide('definition', [
        el({ type: 'text', left: 70, top: 50, width: 500, height: 40, html: '定义页', font_size: 28 }),
        el({ type: 'formula', left: 70, top: 140, width: 400, height: 40, latex: 'a^{2}+b^{2}=c^{2}', font_size: 16 }),
      ]),
    ])
    const issues = checkDeck(deck)
    const small = issues.find((i) => i.rule === '字号过小')
    expect(small).toBeTruthy()
    expect(small!.severity).toBe('error')
    expect(small!.fix).toEqual({ kind: 'bump-font', font: 22 })
    // 应用修复
    expect(applyFix(deck, small!)).toBe('applied')
    const f = deck.slides[0].elements[1] as Extract<V3Element, { type: 'formula' }>
    expect(f.font_size).toBe(22)
  })

  it('溢出画布 → error；例题缺解答 → warn', () => {
    const deck = baseDeck([
      slide('definition', [el({ type: 'text', left: 1100, top: 600, width: 300, height: 200, html: '越界元素', font_size: 20 })]),
      slide('example', [el({ type: 'text', left: 70, top: 50, width: 600, height: 40, html: '例 1：求椭圆方程', font_size: 28 })]),
    ])
    const issues = checkDeck(deck)
    expect(issues.some((i) => i.rule === '内容溢出画布' && i.severity === 'error')).toBe(true)
    const miss = issues.find((i) => i.rule === '例题缺少解答')
    expect(miss).toBeTruthy()
    expect(miss!.slideIndex).toBe(1)
  })

  it('推导 ≥4 公式 → warn（拆页修复：后半移入续页，元素不丢）', () => {
    const deck = baseDeck([
      slide('derivation', [
        el({ type: 'text', left: 70, top: 50, width: 500, height: 40, html: '推导页', font_size: 28 }),
        el({ type: 'formula', left: 70, top: 140, width: 400, height: 40, latex: 'a', font_size: 22 }),
        el({ type: 'formula', left: 70, top: 220, width: 400, height: 40, latex: 'b', font_size: 22 }),
        el({ type: 'formula', left: 70, top: 300, width: 400, height: 40, latex: 'c', font_size: 22 }),
        el({ type: 'formula', left: 70, top: 380, width: 400, height: 40, latex: 'd', font_size: 22 }),
      ]),
    ])
    const issues = checkDeck(deck)
    const dense = issues.find((i) => i.rule === '推导步骤过密')!
    expect(dense).toBeTruthy()
    const before = deck.slides[0].elements.length
    expect(applyFix(deck, dense)).toBe('applied')
    expect(deck.slides.length).toBe(2)
    expect(deck.slides[1].anchor_bar).toContain('续')
    // 元素守恒：原页 + 续页（标题+页码 2 个新元素）
    const after = deck.slides[0].elements.length + deck.slides[1].elements.length
    expect(after).toBe(before + 2)
  })

  it('版式连续重复 → info；无理解检查点 → info', () => {
    const def = () => slide('definition', [el({ type: 'text', left: 70, top: 50, width: 500, height: 40, html: `第${Math.random()}页`, font_size: 24 })])
    const deck = baseDeck([def(), def(), def(), def(), def(), def()])
    const issues = checkDeck(deck)
    expect(issues.some((i) => i.rule === '版式连续重复')).toBe(true)
    expect(issues.some((i) => i.rule === '缺少理解检查点')).toBe(true)
  })

  it('跨页重复同一归一化公式 → error，定位后页公式', () => {
    const deck = baseDeck([
      slide('definition', [
        el({ type: 'text', left: 70, top: 50, width: 500, height: 40, html: '定义', font_size: 28 }),
        el({ type: 'formula', id: 'formula-first', left: 70, top: 140, width: 500, height: 50, latex: '$x^{2} + y^{2}=1$', font_size: 22 }),
      ]),
      slide('summary', [
        el({ type: 'text', left: 70, top: 50, width: 500, height: 40, html: '小结', font_size: 28 }),
        el({ type: 'formula', id: 'formula-repeat', left: 70, top: 140, width: 500, height: 50, latex: 'x^2+y^2=1', font_size: 22 }),
      ]),
    ])
    const issues = checkDeck(deck)
    const repeated = issues.find((i) => i.rule === '公式机械重复')
    expect(repeated).toBeTruthy()
    expect(repeated!.severity).toBe('error')
    expect(repeated!.slideIndex).toBe(1)
    expect(repeated!.elementId).toBe('formula-repeat')
  })

  it('正文课件没有可执行课堂任务 → error，提示目标/行动/产出', () => {
    const deck = baseDeck([
      slide('definition', [el({ type: 'text', left: 70, top: 50, width: 500, height: 40, html: '定义', font_size: 28 })]),
      slide('example', [
        el({ type: 'text', left: 70, top: 50, width: 600, height: 40, html: '例 1：求方程', font_size: 28 }),
        el({ type: 'text', left: 70, top: 140, width: 600, height: 50, html: '解：列式并计算，答案见下页', font_size: 22 }),
      ]),
    ])
    const issues = checkDeck(deck)
    const missingTask = issues.find((i) => i.rule === '缺少课堂任务')
    expect(missingTask).toBeTruthy()
    expect(missingTask!.severity).toBe('error')
    expect(missingTask!.why).toContain('目标')
    expect(missingTask!.why).toContain('行动')
    expect(missingTask!.why).toContain('产出')
  })

  it('合格课件零 error', () => {
    const deck = baseDeck([
      slide('cover', [el({ type: 'text', left: 90, top: 200, width: 700, height: 80, html: '标题', font_size: 44 })]),
      slide('definition', [el({ type: 'text', left: 70, top: 50, width: 500, height: 40, html: '定义', font_size: 28 })]),
      slide('example', [el({ type: 'text', left: 70, top: 50, width: 600, height: 40, html: '例 1 解：答案 ∴', font_size: 26 })]),
      slide('variation', [el({ type: 'text', left: 70, top: 50, width: 600, height: 40, html: '变式训练', font_size: 26 })]),
      slide('summary', [el({ type: 'text', left: 70, top: 50, width: 600, height: 40, html: '小结', font_size: 26 })]),
      slide('blank', [el({ type: 'text', left: 70, top: 50, width: 600, height: 40, html: '当堂检测', font_size: 26 })]),
    ])
    deck.slides[5].task = {
      goal: '检验本节掌握情况',
      context: '根据刚学的定义完成检测题',
      student_action: '独立列式并说明依据',
      expected_output: '提交答案和关键计算式',
    }
    const issues = checkDeck(deck)
    expect(issues.filter((i) => i.severity === 'error')).toHaveLength(0)
  })
})
