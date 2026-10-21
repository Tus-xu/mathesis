/**
 * 裸 LaTeX 智能包装：后端部分字段（如 socratic_complete 的 final_answer）
 * 给的是无 $ 定界符的 LaTeX 源码（如 \dfrac{\sqrt{3}}{3}）。
 * 含 LaTeX 命令且不含 $ 时裹定界符，交给 MarkdownView 的 KaTeX 渲染；
 * 已含 $ 或纯文本则原样返回。
 * inline=true 用 $...$（行内，适合选项等短文本），否则 $$...$$（展示模式）。
 */
export function wrapBareLatex(text, { inline = false } = {}) {
  const s = String(text ?? '').trim()
  if (!s) return ''
  if (s.includes('$')) return s
  if (/\\[a-zA-Z]+/.test(s)) return inline ? `$${s}$` : `$$${s}$$`
  return s
}

/**
 * 嵌套/碎片化 $ 定界符归一化（题库 AGIEval/GAOKAO-Bench 原始语料治理）。
 * 病灶样例（DB 实拍）：
 *   $(x $\sqrt{y}$-y $\sqrt{x}$)^{4}$          → $(x \sqrt{y}-y \sqrt{x})^{4}$
 *   $\left($\frac{x}{$\sqrt{y}$$}-$\frac{y}{$\sqrt{x}$$}\right)^{8}$
 *     → $\left(\frac{x}{\sqrt{y}}-\frac{y}{\sqrt{x}}\right)^{8}$
 * 规则：从单 $ 起始扫描，括号（圆/花/方）未平衡时遇到的 $ 视为内层碎片剥掉，
 * 平衡时的 $ 才是闭合。找不到平衡闭合则回退到下一个 $（保持原解析行为）。
 * 不误伤：独立多段 $a$ 和 $b$、$$ 展示数学、半开区间 $[0,1)$（负深度回退）。
 */
export function mergeNestedMath(text) {
  const s = String(text ?? '')
  if (!s.includes('$')) return s
  let out = ''
  let i = 0
  const n = s.length
  while (i < n) {
    if (s[i] === '\\' && s[i + 1] === '$') { out += '\\$'; i += 2; continue }
    if (s[i] !== '$') { out += s[i]; i++; continue }
    if (s[i + 1] === '$') {
      const close = s.indexOf('$$', i + 2)
      if (close === -1) { out += s.slice(i); break }
      out += s.slice(i, close + 2)
      i = close + 2
      continue
    }
    const span = findBalancedClose(s, i + 1)
    if (!span) { out += '$'; i++; continue }
    out += '$' + span.content + '$'
    i = span.end + 1
  }
  return out
}

function findBalancedClose(s, start) {
  let paren = 0, brace = 0, bracket = 0
  let content = ''
  let firstDollar = -1
  let j = start
  const n = s.length
  while (j < n) {
    const ch = s[j]
    if (ch === '\\') {
      const nx = s[j + 1]
      if (nx === '$') { content += '\\$'; j += 2; continue }
      content += nx === undefined ? ch : ch + nx // 转义整体复制，\{ \( 等不计括号
      j += 2
      continue
    }
    if (ch === '$') {
      if (firstDollar === -1) firstDollar = j
      if (paren === 0 && brace === 0 && bracket === 0) return { content, end: j }
      j++ // 未平衡：内层碎片 $，剥掉继续
      continue
    }
    if (ch === '(') paren++
    else if (ch === ')') paren--
    else if (ch === '{') brace++
    else if (ch === '}') brace--
    else if (ch === '[') bracket++
    else if (ch === ']') bracket--
    if (paren < 0 || brace < 0 || bracket < 0) break // 负深度：本段非嵌套语料，回退
    content += ch
    j++
  }
  if (firstDollar === -1) return null
  return { content: s.slice(start, firstDollar), end: firstDollar } // 回退原行为
}
