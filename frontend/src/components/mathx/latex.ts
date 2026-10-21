/**
 * mathx/latex —— LaTeX 渲染工具（KaTeX + 白名单消毒）
 * V3 所有静态公式渲染走这里；交互编辑走 MathField（MathLive）。
 * 约定沿袭 V2 SlideCanvas：$..$ 行内公式 + 白名单标签 + DOMPurify。
 */
import katex from 'katex'
import DOMPurify from 'dompurify'

const ALLOW_TAG = /&lt;(\/?)(b|i|u|br|sub|sup)&gt;/g

export function escapeSegment(t: string): string {
  return t.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(ALLOW_TAG, '<$1$2>')
}

/**
 * 清洗 MathLive 导出残留：\placeholder{x} → x，模板槽位 #0/#1 → □
 * MathField 导出已用 latex-without-placeholders，这里是存量脏数据/键盘模板直存时的渲染兜底。
 */
export function cleanPlaceholder(latex: string | undefined): string {
  return String(latex ?? '')
    .replace(/\\placeholder\{([^{}]*)\}/g, '$1')
    .replace(/\\placeholder/g, '')
    .replace(/#(\d)/g, '\\square ')
}

export function renderLatex(latex: string | undefined, display = false): string {
  try {
    return DOMPurify.sanitize(katex.renderToString(cleanPlaceholder(latex), { throwOnError: false, displayMode: display }), {
      USE_PROFILES: { html: true, mathMl: true },
    })
  } catch {
    return escapeSegment(String(latex ?? ''))
  }
}

/** 富文本渲染：$..$ 内联公式 + 白名单标签（教案环节 / 题干 / 评语用） */
export function renderRich(src: string | undefined): string {
  const s = String(src ?? '')
  let out = ''
  let i = 0
  // 找下一个「未转义」的 $ 作为定界符（\$ 是字面美元）
  const nextDollar = (from: number) => {
    for (let j = from; j < s.length; j++) {
      if (s[j] === '\\' && s[j + 1] === '$') { j += 1; continue }
      if (s[j] === '$') return j
    }
    return -1
  }
  while (i < s.length) {
    if (s[i] !== '$') {
      const next = nextDollar(i)
      const end = next === -1 ? s.length : next
      out += escapeSegment(s.slice(i, end)).replace(/\\\$/g, '$')
      i = end
      continue
    }
    const close = nextDollar(i + 1)
    if (close === -1) { out += '$'; i += 1; continue }
    out += renderLatex(s.slice(i + 1, close))
    i = close + 1
  }
  return DOMPurify.sanitize(out, { USE_PROFILES: { html: true, mathMl: true } })
}

/**
 * 题干/答案渲染（V3 题库数据源）：兼容两种格式
 * - 含 \$..\$ 定界符的富文本（与教案/评语同构）→ renderRich
 * - 纯 LaTeX（无 \$）→ renderLatex
 * 修复：此前直接 renderLatex 会把富文本里的 \$ 定界符原样输出（组卷/题库/课堂乱码）。
 */
export function renderStem(latex: string | undefined): string {
  const s = String(latex ?? '')
  if (s.includes('\$')) return renderRich(s)
  return renderLatex(s)
}

/** LaTeX 粗校验：花括号配对 + 非空（供表单校验） */
export function latexOk(latex: string | undefined): boolean {
  const s = String(latex ?? '').trim()
  if (!s) return false
  let depth = 0
  for (const ch of s) {
    if (ch === '{') depth += 1
    if (ch === '}') depth -= 1
    if (depth < 0) return false
  }
  return depth === 0
}
