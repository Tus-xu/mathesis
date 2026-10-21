import { describe, expect, it } from 'vitest'
import { CLICHE_RULES, dejargonize, detectCliche, isClicheHeavy } from '@/components/mathx/cliche'

describe('detectCliche 反套话识别', () => {
  it('命中已知套话短语，返回命中说明', () => {
    const hits = detectCliche('本节课着重激发学生学习兴趣，突出重点难点，培养学生的核心素养。')
    expect(hits.length).toBeGreaterThanOrEqual(2)
    expect(hits.some((h) => h.includes('激发'))).toBe(true)
  })

  it('具体可执行的表述不误报', () => {
    const hits = detectCliche('讲例1：焦点在x轴、a=5、b=3，求椭圆的标准方程并分三步板书。')
    expect(hits).toHaveLength(0)
  })

  it('重复命中去重', () => {
    expect(detectCliche('寓教于乐 · 寓教于乐')).toHaveLength(1)
  })
})

describe('isClicheHeavy 套话过重判定', () => {
  it('命中≥2 句 → 过重', () => {
    expect(isClicheHeavy('激发兴趣，培养能力，增强信心，体验形成过程。')).toBe(true)
  })
  it('命中 1 句但句子很短 → 过重（整段只剩空话）', () => {
    expect(isClicheHeavy('激发学生学习兴趣')).toBe(true)
  })
  it('长文偶见一句套话 → 不过重', () => {
    const t = '讲例1：a=5、b=3 求椭圆方程。' + ' '.repeat(40) + '适当激发兴趣，其余均为具体板书步骤与演示。'
    expect(isClicheHeavy(t)).toBe(false)
  })
})

describe('dejargonize 一键去套话', () => {
  it('无套话时原样返回', () => {
    const s = '板书两步平方化简，学生完成课本填空。'
    expect(dejargonize('椭圆', '新知探究', s)).toBe(s)
  })

  it('有套话时围绕课题重写为可执行目标', () => {
    const out = dejargonize('椭圆', '教学目标', '激发学生学习兴趣，培养分析问题与解决问题的能力。')
    expect(out).toContain('椭圆及其标准方程')
    expect(out).toContain('可检验')
  })

  it('例题板块重写含具体例 1 结构', () => {
    const out = dejargonize('椭圆', '例题精讲', '层层递进，突出重点难点。')
    expect(out).toContain('例 1')
  })

  it('所有规则均可匹配且 label 非空', () => {
    for (const r of CLICHE_RULES) {
      expect(r.label.length).toBeGreaterThan(0)
      expect(r.re).toBeInstanceOf(RegExp)
    }
  })
})