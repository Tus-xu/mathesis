/**
 * 参数化数学图形 → 公式联动（G17）：确定性纯函数，无 LLM、无编造。
 *
 * 数据→图形→公式同源（与 MathFigure / PropertiesPanel 同口径）：
 * `a`/`b` 是唯一事实，`c=√(a²−b²)` 恒派生。教师改参数 → 公式必须同步：
 * - 标准方程 `\frac{x^2}{a^2} + \frac{y^2}{b^2} = 1 (a > b > 0)` → 代入当前 a²/b² 与约束值；
 * - 半焦距关系 `c = \sqrt{a^2 - b^2}` / `c^2 = a^2 - b^2` → 代入数值并给出派生结果。
 *
 * 红线：
 * 1. 只改写**可识别的椭圆规范式**（符号式或已代入的数值式，幂等可重入）；
 *    教师自撰公式不匹配即原样保留（教师主权，AI/系统不得覆盖教师内容）；
 * 2. 非法参数（a > b > 0 不成立）拒绝代入（返回原式），由属性面板负责拒绝提示；
 * 3. 该结果直接落库（patch `set_slide.elements/content`）→ 画布=导出=落库一致。
 */
import type { MathObject, SlideElement } from '@/features/teacher-prep-center/api/deckApi'

/** 数值显示：≤2 位小数，去掉多余尾零（25 / 4.6 / 4.61）。 */
export function fmtNum(n: number): string {
  return String(Math.round(n * 100) / 100)
}

/** 半焦距 c=√(a²−b²)（与 MathFigure / PropertiesPanel 同公式）。 */
export function deriveC(a: number, b: number): number {
  return Math.sqrt(Math.max(0, a * a - b * b))
}

/** 椭圆参数合法性：a > b > 0（数学红线）。 */
export function validEllipseParams(a: number, b: number): boolean {
  return Number.isFinite(a) && Number.isFinite(b) && a > 0 && b > 0 && a > b
}

export function ellipseParamsOf(mathObject: MathObject | null | undefined): { a: number; b: number } | null {
  if (!mathObject || mathObject.kind !== 'ellipse') return null
  const a = Number(mathObject.params?.a)
  const b = Number(mathObject.params?.b)
  return validEllipseParams(a, b) ? { a, b } : null
}

/* ── 规范式识别（符号式 / 数值式均可命中，保证再次改参仍能同步）───────────── */
// 标准方程两个分式（分母任意：a^2 / 25 / 30.25 均可）
const STD_EQ_X_RE = /\\frac\{x\^\{?2\}?\}\{[^}]*\}/
const STD_EQ_Y_RE = /\\frac\{y\^\{?2\}?\}\{[^}]*\}/
// c = √(...)（符号或数值；已代入式的尾部 ` = 4` 一并消费，保证重入幂等）
const SQRT_C_RE = /c\s*=\s*\\sqrt\{[^}]*\}(?:\s*=\s*[\d.]+)?/
// c² = … − …（LaTeX 与 Unicode 两种写法；同上消费尾部派生值）
const SQ_C_RE = /c\^\{?2\}?\s*=\s*(?:a\^\{?2\}?|[\d.]+)\s*[-−]\s*(?:b\^\{?2\}?|[\d.]+)(?:\s*=\s*[\d.]+)?/
const SQ_C_UNI_RE = /c²\s*=\s*(?:a²|[\d.]+)\s*[-−]\s*(?:b²|[\d.]+)(?:\s*=\s*[\d.]+)?/
// 约束 (a > b > 0) / (5 > 3 > 0)；紧邻其前的间距命令（\quad 等）一并消费 ——
// 导出链 8201 对元素数学只做部分 LaTeX→Unicode，`\quad` 会裸奔进 PPTX <a:t>（产物源码泄漏），
// 代入改写时顺手去掉（渲染无差异，导出干净）。
const CONSTRAINT_RE = /(?:\\(?:quad|qquad|,|;|:)\s*)?\(\s*(?:a|[\d.]+)\s*[>＞]\s*(?:b|[\d.]+)\s*[>＞]\s*0\s*\)/

/**
 * 单条显示式 LaTeX → 参数代入后的 LaTeX。
 * 无可识别规范式 → 原样返回（教师自撰公式不覆盖）；非法参数 → 原样返回。
 */
export function instantiateEllipseLatex(raw: string | null | undefined, params: { a: number; b: number }): string {
  const latex = (raw || '').trim()
  if (!latex) return latex
  const { a, b } = params
  if (!validEllipseParams(a, b)) return latex
  const a2 = fmtNum(a * a)
  const b2 = fmtNum(b * b)
  const c = fmtNum(deriveC(a, b))
  const c2 = fmtNum(a * a - b * b)

  let out = latex
  let hit = false
  if (STD_EQ_X_RE.test(out) && STD_EQ_Y_RE.test(out)) {
    out = out.replace(STD_EQ_X_RE, () => `\\frac{x^2}{${a2}}`).replace(STD_EQ_Y_RE, () => `\\frac{y^2}{${b2}}`)
    hit = true
  }
  if (SQRT_C_RE.test(out)) {
    out = out.replace(SQRT_C_RE, () => `c = \\sqrt{${a2} - ${b2}} = ${c}`)
    hit = true
  }
  if (SQ_C_RE.test(out)) {
    out = out.replace(SQ_C_RE, () => `c^2 = ${a2} - ${b2} = ${c2}`)
    hit = true
  }
  if (SQ_C_UNI_RE.test(out)) {
    out = out.replace(SQ_C_UNI_RE, () => `c² = ${a2} − ${b2} = ${c2}`)
    hit = true
  }
  if (hit) out = out.replace(CONSTRAINT_RE, () => `(${fmtNum(a)} > ${fmtNum(b)} > 0)`)
  return out
}

/**
 * 一页 slide 的公式同步：`elements`（formula.latex / knowledge_card.latex）与
 * `content` 纯公式行全部代入当前参数。返回**仅变化的部分**（未变不进 patch）。
 */
export function syncSlideFormulas(
  slide: { elements?: SlideElement[] | null; content?: string | null },
  mathObject: MathObject | null | undefined,
): { elements?: SlideElement[]; content?: string } {
  const params = ellipseParamsOf(mathObject)
  if (!params) return {}
  const out: { elements?: SlideElement[]; content?: string } = {}

  const els = Array.isArray(slide.elements) ? slide.elements : []
  if (els.length) {
    let changed = false
    const next = els.map((el) => {
      const isFormula = el.role === 'formula' && !!el.latex
      const isCardEq = el.role === 'knowledge_card' && !!el.latex
      if (!isFormula && !isCardEq) return el
      const latex = instantiateEllipseLatex(el.latex, params)
      if (latex === (el.latex || '').trim()) return el
      changed = true
      return { ...el, latex }
    })
    if (changed) out.elements = next
  }

  const content = typeof slide.content === 'string' ? slide.content : ''
  if (content) {
    let changed = false
    const lines = content.split('\n').map((ln) => {
      const m = /^\s*\$([^$]+)\$\s*$/.exec(ln)
      if (!m) return ln
      const latex = instantiateEllipseLatex(m[1], params)
      if (latex === m[1].trim()) return ln
      changed = true
      return `$${latex}$`
    })
    if (changed) out.content = lines.join('\n')
  }
  return out
}
