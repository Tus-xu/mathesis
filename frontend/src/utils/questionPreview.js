const TABLE_ROW = /^\s*\|.*\|\s*$/
const TABLE_SEP = /^\s*\|?[\s:|-]{3,}\|?\s*$/

const GREEK_AND_SYMBOLS = {
  alpha: 'α', beta: 'β', gamma: 'γ', delta: 'δ', theta: 'θ', lambda: 'λ',
  mu: 'μ', pi: 'π', sigma: 'σ', phi: 'φ', omega: 'ω', triangle: '△',
  angle: '∠', infty: '∞', cdot: '·', times: '×', div: '÷', pm: '±',
  mp: '∓', leq: '≤', geq: '≥', neq: '≠', to: '→', rightarrow: '→',
  left: '', right: '',
}

/** 单行摘要里的公式源码 → 可读纯文本（不把 $/反斜杠漏到卡片上）。 */
function plainLatex(text) {
  let t = String(text || '')
  t = t.replace(/\\begin\{[^{}]*\}|\\end\{[^{}]*\}/g, ' ')
  t = t.replace(/\\text\{([^{}]*)\}/g, '$1')
  t = t.replace(
    /\\frac\{([^{}]*)\}\{([^{}]*)\}/g,
    (_, a, b) => (String(a).includes('+') || String(a).includes('-') || String(b).includes('+') || String(b).includes('-')
      ? `(${a})/(${b})`
      : `${a}/${b}`),
  )
  t = t.replace(/\\sqrt\{([^{}]*)\}/g, '√($1)')
  t = t.replace(/\\([A-Za-z]+)/g, (m, name) => (name in GREEK_AND_SYMBOLS ? GREEK_AND_SYMBOLS[name] : name))
  t = t.replace(/\\[,;!]/g, ' ').replace(/\\quad|\\qquad/g, ' ')
  t = t.replace(/[{}]/g, '').replace(/\$/g, '')
  return t.replace(/△\s+/g, '△').replace(/\s+/g, ' ').trim()
}

/**
 * 题干摘要：先剔除 Markdown 表格/图片等块级语法，再在公式边界内截断。
 * 列表只做预览；完整题干请使用 MarkdownView 渲染（支持表格 + KaTeX）。
 */
export function compactQuestionPreview(text, limit = 90) {
  const source = String(text || '')
  const kept = source
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line && !TABLE_ROW.test(line) && !TABLE_SEP.test(line))
    .map((line) =>
      line
        .replace(/!\[[^\]]*\]\([^)]*\)/g, '［图］')
        .replace(/^#{1,6}\s*/, '')
        .replace(/^[>*+-]\s+/, ''),
    )
  if (!kept.length) return '（题目含数据表，点开查看完整题目）'
  const flat = plainLatex(kept.join(' ').replace(/\s+/g, ' ').trim())
  if (flat.length <= limit) return flat
  return safeLatexTruncate(flat, limit)
}

/** 截断位置不落在 $...$ 或 \\begin{...} 环境内部，避免公式残片。 */
export function safeLatexTruncate(text, maxLen = 120) {
  if (!text || text.length <= maxLen) return text || ''
  let cut = Math.min(maxLen, text.length)
  while (cut > 0) {
    const prefix = text.slice(0, cut)
    const dollars = (prefix.match(/\$/g) || []).length
    const lastBegin = prefix.lastIndexOf('\\begin{')
    const insideEnv = lastBegin !== -1 && prefix.indexOf('\\end{', lastBegin) === -1
    if (dollars % 2 === 0 && !insideEnv) break
    cut -= 1
  }
  if (cut <= 0) cut = Math.min(maxLen, text.length)
  return `${text.slice(0, cut).replace(/[，。；;、\s]+$/, '')}…`
}

/** 判断题干是否包含 Markdown 表格（列表卡片显示「含数据表」提示）。 */
export function hasMarkdownTable(text) {
  return String(text || '')
    .split(/\r?\n/)
    .some((line) => TABLE_ROW.test(line.trim()))
}
