import { describe, expect, it } from 'vitest'
import { compactQuestionPreview } from '@/utils/questionPreview'

describe('compactQuestionPreview · LaTeX 摘要', () => {
  it('不把 $...$ 与反斜杠命令漏到首页复习卡', () => {
    const out = compactQuestionPreview(
      '已知抛物线 $y=x^2-4x+3$ 的顶点为 $C$，与 $x$ 轴交于 $A,B$，求 $\\triangle ABC$ 的面积。',
      120,
    )
    expect(out).not.toContain('$')
    expect(out).not.toContain('\\')
    expect(out).toContain('△ABC')
    expect(out).toContain('y=x^2-4x+3')
  })

  it('分数与根号压成可读文本', () => {
    expect(compactQuestionPreview('求 $\\frac{1}{2}+\\sqrt{3}$ 的值', 60)).toContain('1/2')
    expect(compactQuestionPreview('求 $\\frac{1}{2}+\\sqrt{3}$ 的值', 60)).toContain('√(3)')
  })
})
