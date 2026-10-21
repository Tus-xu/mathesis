// V3 mathx 纯函数层单测：expr（LaTeX→JS 求值）/ latex（KaTeX 渲染+消毒）/ section（多面体截面算法）
// 对应红线 R1：数学元素永远结构化（公式可再编辑、图形可参数化）的底层能力。
import { describe, it, expect } from 'vitest'
import { parseLatexExpr, makeFn } from '@/components/mathx/expr'
import { renderLatex, renderRich, renderStem, latexOk, cleanPlaceholder } from '@/components/mathx/latex'
import {
  CUBE_VERTS, CUBE_EDGES, planeFrom3Points, intersectSegmentPlane,
  sectionPolygon, collinear, pointOnEdge,
} from '@/components/mathx/section'

const near = (a: number, b: number, eps = 1e-9) => Math.abs(a - b) < eps

describe('expr：LaTeX 表达式 → 可求值函数（输入即生图）', () => {
  it('SPEC 主样例 y=a·sin(bx+c)+d：参数自动提取为 a-d 滑杆', () => {
    const p = parseLatexExpr('y=a\\cdot\\sin(bx+c)+d')
    expect(p.error).toBeUndefined()
    expect([...p.params].sort()).toEqual(['a', 'b', 'c', 'd'])
    const f = makeFn(p)
    expect(near(f(Math.PI / 2, { a: 2, b: 1, c: 0, d: 1 }), 3)).toBe(true)
  })

  it('分式/根式嵌套：\\frac{\\sqrt{3}}{2}', () => {
    const f = makeFn(parseLatexExpr('\\frac{\\sqrt{3}}{2}'))
    expect(near(f(0, {}), Math.sqrt(3) / 2, 1e-12)).toBe(true)
  })

  it('幂与常量：y=x^{2}、\\pi x', () => {
    expect(near(makeFn(parseLatexExpr('y=x^{2}'))(3, {}), 9)).toBe(true)
    expect(near(makeFn(parseLatexExpr('\\pi x'))(1, {}), Math.PI)).toBe(true)
  })

  it('指数：e^{-x} 在 x=1 处为 1/e', () => {
    expect(near(makeFn(parseLatexExpr('y=e^{-x}'))(1, {}), 1 / Math.E)).toBe(true)
  })

  it('裸操作数函数：\\ln x / \\sin\\pi / \\sinh x 不再拼成未定义标识符', () => {
    expect(near(makeFn(parseLatexExpr('y=\\ln x'))(Math.E, {}), 1)).toBe(true)
    expect(near(makeFn(parseLatexExpr('\\sin\\pi'))(0, {}), 0, 1e-12)).toBe(true)
    expect(near(makeFn(parseLatexExpr('y=\\sinh x'))(0, {}), 0)).toBe(true)
  })

  it('反三角与余切：求值期绑定齐全', () => {
    expect(near(makeFn(parseLatexExpr('y=\\arcsin x'))(1, {}), Math.PI / 2)).toBe(true)
    expect(near(makeFn(parseLatexExpr('y=\\cot x'))(Math.PI / 4, {}), 1)).toBe(true)
  })

  it('下标变量被剥离：x_{1}+x_{2} 可求值', () => {
    expect(near(makeFn(parseLatexExpr('x_{1}+x_{2}'))(3, {}), 6)).toBe(true)
  })

  it('y= 前缀剥离与空表达式报错', () => {
    expect(parseLatexExpr('y=')).toEqual({ js: '', params: [], error: '表达式为空' })
    expect(parseLatexExpr('   ').error).toBe('表达式为空')
  })

  it('参数只认 a-d：x、e 不进滑杆', () => {
    expect(parseLatexExpr('x^{2}+e^{-x}').params).toEqual([])
  })

  it('花括号不配对 → error 而非抛异常', () => {
    const p = parseLatexExpr('\\frac{1}{')
    expect(p.error).toBeDefined()
    expect(makeFn(p)(0, {})).toBeNaN()
  })
})

describe('latex：KaTeX 渲染 + 白名单消毒', () => {
  it('renderLatex 输出 katex 标记', () => {
    expect(renderLatex('\\frac{1}{2}')).toContain('katex')
  })

  it('renderRich：$..$ 内联公式 + 白名单 <b> 保留', () => {
    const html = renderRich('设 $a>b>0$，这是<b>重点</b>')
    expect(html).toContain('katex')
    expect(html).toContain('<b>重点</b>')
    expect(html).not.toContain('$a')
  })

  it('renderRich：白名单外标签被消毒（script 不落地）', () => {
    const html = renderRich('x <script>alert(1)</script> 与 $x^2$')
    expect(html).not.toContain('<script')
    expect(html).toContain('katex')
  })

  it('renderRich：转义 \\$ 输出字面美元，不进入公式', () => {
    const html = renderRich('单价 \\$5 与 $x^2$')
    expect(html).toContain('$5')
    expect(html).toContain('katex')
  })

  it('latexOk：花括号配对 + 非空校验', () => {
    expect(latexOk('\\frac{1}{2}')).toBe(true)
    expect(latexOk('x^{2')).toBe(false)
    expect(latexOk('')).toBe(false)
    expect(latexOk('x^2')).toBe(true)
  })

  it('cleanPlaceholder：MathLive 导出残留清洗（\\placeholder / 键盘模板槽位）', () => {
    expect(cleanPlaceholder('\\sqrt{\\placeholder{}}')).toBe('\\sqrt{}')
    expect(cleanPlaceholder('\\frac{\\placeholder{3}}{\\placeholder{}}')).toBe('\\frac{3}{}')
    expect(cleanPlaceholder('\\sqrt{#0}')).toBe('\\sqrt{\\square }')
    expect(cleanPlaceholder('\\frac{#0}{#1}')).toBe('\\frac{\\square }{\\square }')
    expect(cleanPlaceholder('x^2')).toBe('x^2')
  })

  it('renderLatex：脏数据兜底——\\placeholder 与 #0 不再报错出"?"', () => {
    const html = renderLatex('\\sqrt{\\placeholder{}}')
    expect(html).toContain('katex')
    expect(html).not.toContain('placeholder')
    expect(renderLatex('\\sqrt{#0}')).toContain('katex')
  })

  it('renderStem：文本+$..$ 混合题干/答案不得出 katex-error 红字（tel F4 批改台/发布页题面）', () => {
    // 复现评审截图 04b/08b 的真实形态：题面/解析为「中文文本 + 行内公式」混合串
    const stem = '已知函数 $f(x)=x^{2}-2x$，求 $f(3)$ 的值。'
    const rich = renderStem(stem)
    expect(rich).not.toContain('katex-error')
    expect(rich).toContain('katex')
    expect(rich).toContain('已知函数')
    // 参考答案/解析：整段混合串同样分段渲染
    const analysis = '答：代入得 $f(3)=9-6=3$，因此答案是 $3$。'
    expect(renderStem(analysis)).not.toContain('katex-error')
    // 纯 LaTeX（无 $ 定界）回落 renderLatex，仍出公式
    expect(renderStem('\\frac{1}{2}')).toContain('katex')
    // 对照：旧整串 renderLatex 路径对同一混合串会产生 katex-error（锁定修复动机）
    expect(renderLatex(stem)).toContain('katex-error')
  })
})

describe('section：多面体截面算法（L2 构造式）', () => {
  it('三点定面：底面三点 → 法向沿 z 轴', () => {
    const pl = planeFrom3Points([0, 0, 0], [1, 0, 0], [0, 1, 0])
    expect(near(Math.hypot(...pl.n), 1)).toBe(true)
    expect(Math.abs(pl.n[2])).toBeCloseTo(1)
    expect(near(pl.d, 0)).toBe(true)
  })

  it('经典截面：平面 x+y+z=1.5 截单位正方体得正六边形', () => {
    const pts = sectionPolygon(CUBE_VERTS, CUBE_EDGES, [1, 0.5, 0], [0.5, 1, 0], [0, 1, 0.5])
    expect(pts).toHaveLength(6)
    for (const p of pts) expect(near(p[0] + p[1] + p[2], 1.5, 1e-9)).toBe(true)
    let per = 0
    for (let i = 0; i < 6; i++) {
      const a = pts[i]
      const b = pts[(i + 1) % 6]
      per += Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2])
    }
    expect(near(per, 6 * Math.sqrt(0.5), 1e-6)).toBe(true)
    const c = pts.reduce((s, p) => [s[0] + p[0] / 6, s[1] + p[1] / 6, s[2] + p[2] / 6], [0, 0, 0])
    expect(near(c[0], 0.5) && near(c[1], 0.5) && near(c[2], 0.5)).toBe(true)
  })

  it('三点共线无法定面 → 空截面', () => {
    expect(collinear([0, 0, 0], [0.5, 0.5, 0.5], [1, 1, 1])).toBe(true)
    expect(sectionPolygon(CUBE_VERTS, CUBE_EDGES, [0, 0, 0], [0.5, 0.5, 0.5], [1, 1, 1])).toEqual([])
  })

  it('截面与底面重合（无严格内部交点）→ 空截面', () => {
    expect(sectionPolygon(CUBE_VERTS, CUBE_EDGES, [0.2, 0.3, 0], [0.7, 0.1, 0], [0.4, 0.8, 0])).toEqual([])
  })

  it('线段与平面求交：内部命中 / 平行 / 端点在面上', () => {
    const pl = { n: [0, 0, 1] as [number, number, number], d: -0.5 }
    const hit = intersectSegmentPlane([0, 0, 0], [0, 0, 1], pl)
    expect(hit !== null && near(hit[2], 0.5)).toBe(true)
    expect(intersectSegmentPlane([0, 0, 0], [1, 0, 0], pl)).toBeNull()
    expect(intersectSegmentPlane([0, 0, 0.5], [0, 0, 1], pl)).toBeNull()
  })

  it('棱上取点：t 比例插值', () => {
    expect(pointOnEdge([0, 0, 0], [2, 0, 0], 0.25)).toEqual([0.5, 0, 0])
  })
})
