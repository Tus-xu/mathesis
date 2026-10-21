/**
 * 科研端「后端自由文本必须经 KaTeX 渲染」的回归锁（用户实测「公式乱码 + 红色」）。
 *
 * 背景缺陷（2026-10-05 用户截图复验）：
 * 后端 `reasoning.py` 的 `_SYSTEM_MATH` prompt 要求「公式写在 `$...$` 里」，
 * 但前端有两处把**含公式的字段当纯文本**处理：
 *   1. `FindingRow` 的 `math` 用 `renderLatex`（只认裸 LaTeX）→ KaTeX 遇到 `$` 解析失败，
 *      因 `throwOnError:false` 把整串**渲染成红色**（`.katex-error`）—— 即用户看到的红字；
 *   2. `ResultCard` 的 `summary` / latex_error 兜底正文 / `suggestion` 用 `{{ }}` 裸插值
 *      → `$z$`、`$\frac{\partial C}{\partial z}=0$` 原样吐出（黑色乱码）。
 *
 * 修复：`math` 走 `renderStem`（双格式）；散文类字段走 `renderRich`（富文本 + KaTeX）。
 * 两者都经 DOMPurify 消毒。
 *
 * 本测试用**真实后端返回的样本**（2026-10-05 从 `rv8300.research_agent_task.result` 逐字导出）
 * 锁定「不得再出现红色报错 / 不得再漏出定界符」这一不变量。
 */
import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import FindingRow from '@/features/research-workspace/ui/FindingRow.vue'
import ResultCard from '@/features/research-workspace/agent/ResultCard.vue'
import type { ResearchResult } from '@/features/research-workspace/api/types'

/** 真实样本 A：`math` 带 `$...$` 定界符（用户那次任务，6 条中 5 条如此） */
const DOLLAR_MATH_FINDINGS = [
  {
    label: '圆柱对称仅消去周向 $\\theta$ 依赖',
    math: '$\\frac{\\partial C}{\\partial\\theta}=0,\\quad \\frac{\\partial C}{\\partial z}\\neq 0$',
    detail: '轴对称条件下 $\\partial/\\partial\\theta=0$，但 $z$ 方向导数仍保留。',
  },
  {
    label: '端面影响需可忽略或短边界层',
    math: '$L_{\\text{end}}\\ll R$',
    detail: '文中仅定性说明，未给出端面边界层厚度与半径的定量比较。',
  },
  {
    label: '主方程形式需材料参数与边界条件',
    math: '$\\rho(C)c(\\cdot)\\frac{\\partial T}{\\partial t}=\\frac{1}{r}\\frac{\\partial}{\\partial r}\\left(rk\\frac{\\partial T}{\\partial r}\\right)$',
    detail: '缺少 $\\rho(C)c(\\cdot)$、$D(C,T)$、$k$ 的具体形式及侧面热风边界条件。',
  },
]

/** 真实样本 B：`math` 为**裸 LaTeX**（另一次任务，6 条全部无 `$`） */
const BARE_MATH = { label: '守恒形式', math: '\\nabla\\cdot(\\rho \\mathbf{u}) = 0', detail: '' }

/** 真实样本 C：`summary` 里带 `$z$`（用户那次任务的 summary 原文片段） */
const REAL_SUMMARY =
  '该段落的定性判断在建模意图层面是合理的，但作为数学推导并不完整。' +
  '当前缺少的条件：缺少初始温度与含水率沿轴向 $z$ 均匀的声明；' +
  '缺少侧面均匀热风边界条件 $\\frac{\\partial T}{\\partial n}=0$ 的具体数学形式。'

// TS 门禁：FindingRow 的 prop 契约是 Finding（detail 必填，见 api/types.ts:272）；
// 本文件所有用例也都实际传了 detail——helper 签名如实收紧，消除 TS2322。
function renderFinding(finding: { label: string; math?: string; detail: string }) {
  return mount(FindingRow, { props: { index: 1, finding } })
}

/**
 * 可见文本（剔除 `.katex` 子树）。
 * 注意：KaTeX 的 MathML `<annotation encoding="application/x-tex">` **本来就含
 * TeX 源码**（供无障碍/复制），所以直接断言 `w.text()` 不含 `\frac` 是错的 ——
 * 必须先把公式子树摘掉，再看**正文**里有没有漏出定界符或 LaTeX 源码。
 */
function visibleText(w: ReturnType<typeof mount>): string {
  const el = w.element.cloneNode(true) as HTMLElement
  el.querySelectorAll('.katex').forEach((n) => n.remove())
  return el.textContent || ''
}

describe('FindingRow · math 渲染（不得出现红色 KaTeX 报错）', () => {
  it.each(DOLLAR_MATH_FINDINGS)('带 $ 定界符的真实样本不产生 .katex-error（$label）', (finding) => {
    const w = renderFinding(finding)
    expect(w.findAll('.katex-error')).toHaveLength(0)
    expect(w.findAll('.katex').length).toBeGreaterThan(0)
    const text = visibleText(w)
    expect(text).not.toContain('$')
    expect(text).not.toContain('\\frac')
    expect(text).not.toContain('\\partial')
  })

  it('裸 LaTeX（无 $ 定界符）的真实样本同样正常渲染', () => {
    const w = renderFinding(BARE_MATH)
    expect(w.findAll('.katex-error')).toHaveLength(0)
    expect(w.findAll('.katex').length).toBeGreaterThan(0)
    expect(visibleText(w)).not.toContain('\\nabla')
  })

  it('math 为空时只渲染 label，且不报错', () => {
    const w = renderFinding({ label: '缺少初始温度声明', math: '', detail: '' })
    expect(w.findAll('.katex-error')).toHaveLength(0)
    expect(w.text()).toContain('缺少初始温度声明')
  })
})

describe('ResultCard · summary / suggestion 渲染（不得漏出 LaTeX 源码）', () => {
  const base = (patch: Partial<ResearchResult> = {}): ResearchResult => ({
    kind: 'derivation_check',
    status: 'needs_conditions',
    summary: '',
    findings: [],
    evidence: [],
    source_locations: [],
    patches: [],
    actions: ['locate', 'add_note'],
    confidence: 0.6,
    ...patch,
  })

  it('summary 里的 $z$ / $\\frac{..}$ 被 KaTeX 渲染，不残留字面 $', () => {
    const w = mount(ResultCard, { props: { result: base({ summary: REAL_SUMMARY }), mode: 'read' } })
    const body = w.find('.rw-result__body')
    expect(body.exists()).toBe(true)
    expect(body.findAll('.katex').length).toBeGreaterThanOrEqual(2) // $z$ 与 \frac{..}
    const text = visibleText(body as unknown as ReturnType<typeof mount>)
    expect(text).not.toContain('$')
    expect(text).not.toContain('\\frac')
    expect(w.findAll('.katex-error')).toHaveLength(0)
  })

  it('latex_error 分支：无 summary 时用 findings 正文，同样渲染公式', () => {
    const w = mount(ResultCard, {
      props: {
        result: base({
          kind: 'latex_error',
          status: 'conflict',
          summary: '',
          findings: [{ label: '下标越界', detail: '第 12 行 $a_{n+1}$ 越界。', math: '' }],
        }),
        mode: 'write',
      },
    })
    expect(w.findAll('.katex-error')).toHaveLength(0)
    expect(w.findAll('.katex').length).toBeGreaterThan(0)
    expect(visibleText(w as unknown as ReturnType<typeof mount>)).not.toContain('$')
  })

  it('suggestion 里的公式同样渲染（来自 patch.reason）', () => {
    const w = mount(ResultCard, {
      props: {
        result: base({
          kind: 'latex_error',
          status: 'conflict',
          summary: '编译出现错误。',
          patches: [
            {
              file: 'main.tex',
              line: 3,
              before: '',
              after: '',
              reason: '请修复：把 $x^2$ 改为 $x^{2}$。',
            },
          ] as unknown as ResearchResult['patches'],
        }),
        mode: 'write',
      },
    })
    const s = w.find('.rw-result__suggest')
    expect(s.exists()).toBe(true)
    expect(s.findAll('.katex').length).toBeGreaterThan(0)
    expect(visibleText(s as unknown as ReturnType<typeof mount>)).not.toContain('$')
  })
})
