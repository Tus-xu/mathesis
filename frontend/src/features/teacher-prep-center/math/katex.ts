/**
 * 备课中心 KaTeX 渲染内核（黄金图红线：数学一律 KaTeX 渲染，禁止裸 LaTeX 源码上屏）。
 *
 * - 支持 $...$（行内）、$$...$$（显示）、\(...\)（行内）、\[...\]（显示）定界；
 * - 其余文本 HTML 转义直出；
 * - 渲染结果经 DOMPurify 消毒；任何失败回退为转义源码，绝不白屏。
 */
import katex from 'katex'
import DOMPurify from 'dompurify'

const ESCAPES: Record<string, string> = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }
export function escapeHtml(s: string): string {
  return s.replace(/[&<>"]/g, (ch) => ESCAPES[ch])
}

function renderTex(tex: string, displayMode: boolean): string {
  const html = katex.renderToString(tex, {
    displayMode,
    throwOnError: false,
    strict: false,
    output: 'html',
    trust: false,
  })
  return DOMPurify.sanitize(html, { USE_PROFILES: { html: true, svg: true } })
}

/** 单段文本（可能混合数学与普通文字）→ 安全 HTML。 */
export function renderMathText(src: string): string {
  const s = String(src ?? '')
  let out = ''
  let i = 0
  const n = s.length
  while (i < n) {
    // $$...$$（显示）
    if (s.startsWith('$$', i)) {
      const end = s.indexOf('$$', i + 2)
      if (end > i) {
        const tex = s.slice(i + 2, end).trim()
        if (tex) {
          out += renderTex(tex, true)
        }
        // 空块（教材切片常见 `$$\n\n$$`）：整块吃掉不产出，否则游标会落在上，
        // 把后文整段误配成「一个超长显示公式」（KaTeX 报错后把源码染红上屏）。
        i = end + 2
        continue
      }
    }
    // $...$（行内；跳过转义 \$）
    if (s[i] === '\\' && s[i + 1] === '$') {
      out += '$'
      i += 2
      continue
    }
    if (s[i] === '$') {
      const end = s.indexOf('$', i + 1)
      if (end > i) {
        const tex = s.slice(i + 1, end).trim()
        if (tex) {
          out += renderTex(tex, false)
        }
        // 空行内式（`$ $`）：整对吃掉，避免游标停在定界符上（同 $$ 空块）。
        i = end + 1
        continue
      }
    }
    // \(...\) 行内
    if (s.startsWith('\\(', i)) {
      const end = s.indexOf('\\)', i + 2)
      if (end > i) {
        const tex = s.slice(i + 2, end).trim()
        if (tex) {
          out += renderTex(tex, false)
          i = end + 2
          continue
        }
      }
    }
    // \[...\] 显示
    if (s.startsWith('\\[', i)) {
      const end = s.indexOf('\\]', i + 2)
      if (end > i) {
        const tex = s.slice(i + 2, end).trim()
        if (tex) {
          out += renderTex(tex, true)
          i = end + 2
          continue
        }
      }
    }
    // 普通字符（按 & < > 段落批量转义直到下一个潜在定界符）
    let j = i
    while (j < n && s[j] !== '$' && !s.startsWith('\\(', j) && !s.startsWith('\\[', j)) {
      if (s[j] === '\\' && s[j + 1] === '$') j += 2
      else j += 1
    }
    // 定界符未被上方分支消费（未闭合 `$`/`\(`/`\[`，或定界符内为空/空白）：
    // 此时 j === i，若直接 i = j 则游标永不前进 → 同步死循环（实测曾整页卡死、渲染进程 100% CPU）。
    // 据此把该字符按普通文本吃掉落屏，保证每轮至少前进 1。
    if (j === i) {
      out += escapeHtml(s[i])
      i += 1
      continue
    }
    out += escapeHtml(s.slice(i, j))
    i = j
  }
  return out
}
