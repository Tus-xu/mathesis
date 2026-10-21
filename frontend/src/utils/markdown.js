/**
 * Markdown 渲染共享内核（marked + KaTeX + DOMPurify + highlight.js）
 * 供 IncrementalMarkdown（流式增量）与 MarkdownView（静态）两条管线复用，
 * 保证历史消息与流式消息渲染结果一致。
 */
import { marked } from 'marked'
import markedKatex from 'marked-katex-extension'
import DOMPurify from 'dompurify'
import hljs from 'highlight.js/lib/core'
import { mergeNestedMath } from '@/utils/latex'
// 常用语言子集（控制包体）
import javascript from 'highlight.js/lib/languages/javascript'
import typescript from 'highlight.js/lib/languages/typescript'
import python from 'highlight.js/lib/languages/python'
import java from 'highlight.js/lib/languages/java'
import cpp from 'highlight.js/lib/languages/cpp'
import c from 'highlight.js/lib/languages/c'
import json from 'highlight.js/lib/languages/json'
import bash from 'highlight.js/lib/languages/bash'
import yaml from 'highlight.js/lib/languages/yaml'
import markdown from 'highlight.js/lib/languages/markdown'
import xml from 'highlight.js/lib/languages/xml'
import css from 'highlight.js/lib/languages/css'
import sql from 'highlight.js/lib/languages/sql'

hljs.registerLanguage('javascript', javascript)
hljs.registerLanguage('js', javascript)
hljs.registerLanguage('typescript', typescript)
hljs.registerLanguage('ts', typescript)
hljs.registerLanguage('python', python)
hljs.registerLanguage('py', python)
hljs.registerLanguage('java', java)
hljs.registerLanguage('cpp', cpp)
hljs.registerLanguage('c', c)
hljs.registerLanguage('json', json)
hljs.registerLanguage('bash', bash)
hljs.registerLanguage('sh', bash)
hljs.registerLanguage('shell', bash)
hljs.registerLanguage('yaml', yaml)
hljs.registerLanguage('yml', yaml)
hljs.registerLanguage('markdown', markdown)
hljs.registerLanguage('md', markdown)
hljs.registerLanguage('xml', xml)
hljs.registerLanguage('html', xml)
hljs.registerLanguage('css', css)
hljs.registerLanguage('sql', sql)

marked.use(markedKatex({ throwOnError: false, nonStandard: true }))
marked.setOptions({ breaks: true, gfm: true })

// 图片白名单：http(s) + data:image（限小图）。其余一律移除 src
// '/api/' 同源相对路径（2026-10-11：教材切片插图 /api/teacher-v3/textbook-v2/slice-images/*）
const IMG_SAFE_PREFIX = ['http://', 'https://', 'data:image/', '/api/']

/**
 * LaTeX tabular → Markdown 表格（KaTeX 不支持 tabular 环境，23 行真题含频数分布表实测）。
 * 行按 \ 切、列按 & 切（忽略 \hline/\cline）；单元格内 | 转义为 \| 以保住 gfm 表格；
 * 首行作表头（频数分布表的第一行本来就是表头）。
 */
function renderLatexTabular(s) {
  return String(s ?? '').replace(
    /\\begin\{tabular\}\s*\{[^}]*\}([\s\S]*?)\\end\{tabular\}/g,
    (m, body) => {
      const rows = body
        .replace(/\\hline/g, '')
        .replace(/\\cline\{[^}]*\}/g, '')
        .split(/\\/)
        .map((r) => r.trim().replace(/^\{(.*)\}$/, '$1'))
        .filter(Boolean)
      if (!rows.length) return m
      const cell = (c) =>
        c
          .trim()
          .replace(/\\multicolumn\{\d+\}\{[^}]*\}\{([\s\S]*?)\}/g, '$1')
          .replace(/\|/g, '\\|')
      const mdRows = rows.map((r) => '| ' + r.split(/(?<!\\)&/).map(cell).join(' | ') + ' |')
      const sep = '| ' + mdRows[0].split('|').slice(1, -1).map(() => '---').join(' | ') + ' |'
      return [mdRows[0], sep, ...mdRows.slice(1)].join('\n')
    }
  )
}

/**
 * OCR 语料的"答案空位"归一：\$ \qquad \$ / $ \qquad $ 这类转义美元+空位命令，
 * 公式定界判定会失效导致 \qquad 以原码红字裸奔（用户实测）→ 归一为填空线 ____；
 * 残留的转义美元 \$ → HTML 实体字面 $（不会被 KaTeX 当定界符）。
 */
function normalizeOcrArtifacts(s) {
  let t = String(s ?? '')
  t = t.replace(/\\\$[\s|]*\\qquad[\s|]*\\\$/gi, ' ____ ')
  t = t.replace(/\$[\s|]*\\qquad[\s|]*\$/gi, ' ____ ')
  t = t.replace(/\\\$/g, '&#36;')
  return t
}

/** 渲染单段 markdown → 消毒后 HTML（失败回退源码转义，绝不白屏） */
export function renderMarkdown(src, { streamingTail = false, imgMode = 'question' } = {}) {
  let s = normalizeOcrArtifacts(src || '') // OCR 答案空位/转义美元先归一
    .replace(/\\{2,}([a-zA-Z])/g, '\\$1') // P1：\\lg、\\sin 等双反斜杠+字母 → 单反斜杠（KaTeX 中 \\ 是换行命令，否则渲染乱码）
    .replace(/\\\(([\s\S]*?)\\\)/g, (_match, tex) => `$${tex}$`)  // S11：\( \)=课堂练习定界归一为 $..$
    .replace(/\\qquad|\\quad|\\;/g, ' ')  // S3（V2 文档）：老题库残留的 LaTeX 间距命令不进渲染
    .replace(/\\\./g, '')
  s = renderLatexTabular(s) // LaTeX tabular → Markdown 表格
  s = mergeNestedMath(s) // 题库嵌套 $ 语料先归一化，再进 KaTeX
  if (streamingTail) {
    // 流式容错：未闭合的 $$ 块按补齐处理，避免半公式报错
    const dollars = (s.match(/\$\$/g) || []).length
    if (dollars % 2 === 1) s += '$$'
  }
  let out
  try {
    out = marked.parse(s)
  } catch {
    out = `<pre>${s.replace(/&/g, '&amp;').replace(/</g, '&lt;')}</pre>`
  }
  // 迭代15：CJK 相邻的孤儿 ** 清理——CommonMark flanking 规则在中文标点旁失效，
  // 实测"函数递减**"裸奔。仅清理与中文相邻的 **（能正常解析的粗体已渲染为
  // <strong> 不会命中；代码块内 a**b 无中文相邻，不受影响）
  out = out
    .replace(/([一-鿿，。；：、！？（）【】「」《》—…·])\*\*/g, '$1')
    .replace(/\*\*([一-鿿])/g, '$1')
  return DOMPurify.sanitize(out, {
    USE_PROFILES: { html: true, mathMl: true, svg: true },
    ADD_ATTR: ['referrerpolicy', 'loading', 'decoding'],
    afterSanitizeAttributes: (node) => {
      if (node.tagName === 'IMG') {
        const srcAttr = node.getAttribute('src') || ''
        if (!IMG_SAFE_PREFIX.some((p) => srcAttr.startsWith(p))) {
          node.removeAttribute('src')
          return
        }
        node.setAttribute('loading', 'lazy')
        node.setAttribute('decoding', 'async')
        node.setAttribute('referrerpolicy', 'no-referrer')
        node.removeAttribute('onerror')
        node.removeAttribute('onload')
        node.setAttribute('class', imgMode === 'option' ? 'md-img-option' : 'md-img-question')
      }
    },
  })
}

/**
 * 按块边界切分 markdown：空行 / 标题行为边界，跳过 ``` 代码围栏与 $$ 数学段内部。
 * 流式渲染的关键：已完成的块内容不再变化，渲染结果可缓存，仅尾块随 token 重渲。
 */
export function splitMarkdownBlocks(src) {
  const lines = String(src || '').split('\n')
  const blocks = []
  let cur = []
  let inFence = false
  let fenceMark = ''
  let inMath = false

  const flush = () => {
    if (cur.length) {
      blocks.push(cur.join('\n'))
      cur = []
    }
  }

  for (const line of lines) {
    const fenceOpen = line.match(/^\s*(```|~~~)/)
    if (!inMath && fenceOpen) {
      if (!inFence) {
        inFence = true
        fenceMark = fenceOpen[1]
      } else if (line.trimStart().startsWith(fenceMark)) {
        inFence = false
      }
      cur.push(line)
      continue
    }
    if (!inFence && /^\s*\$\$\s*$/.test(line)) {
      inMath = !inMath // $$ 单独成行：进入/退出数学块
      cur.push(line)
      continue
    }
    if (!inFence && !inMath) {
      if (/^\s*$/.test(line)) {
        flush() // 空行 → 块边界
        continue
      }
      if (/^#{1,6}\s/.test(line)) {
        flush() // 标题自成一块
        cur.push(line)
        flush()
        continue
      }
    }
    cur.push(line)
  }
  flush()
  return blocks
}

/** 快速字符串哈希（缓存 key，无需加密强度） */
export function hashStr(s) {
  let h = 5381
  for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) | 0
  return (h >>> 0).toString(36) + '_' + s.length
}

/** 已渲染块缓存（内容 → HTML）。超容时整体清空（简单 LRU 替代，命中率高损低） */
const htmlCache = new Map()
const CACHE_CAP = 800

export function renderBlockCached(text, opts) {
  const key = hashStr(text)
  const hit = htmlCache.get(key)
  if (hit !== undefined) return hit
  const html = renderMarkdown(text, opts)
  if (htmlCache.size >= CACHE_CAP) htmlCache.clear()
  htmlCache.set(key, html)
  return html
}

export { hljs }

/**
 * 渲染后增强：代码块语法高亮 + 右上角复制按钮。
 * 幂等：已处理的节点打 data 标记，重复调用跳过。
 * @param {HTMLElement} root
 * @param {{ skipTail?: boolean }} opts 流式中跳过尾块（避免每帧重高亮）
 */
export function enhanceCodeBlocks(root, { skipTail = false } = {}) {
  if (!root) return
  const pres = [...root.querySelectorAll('pre')]
  pres.forEach((pre, i) => {
    if (skipTail && i === pres.length - 1) return
    const code = pre.querySelector('code')
    if (code && !code.dataset.hljsDone) {
      code.dataset.hljsDone = '1'
      try { hljs.highlightElement(code) } catch { /* 未注册语言静默跳过 */ }
    }
    if (!pre.querySelector('.code-copy-btn')) {
      const btn = document.createElement('button')
      btn.type = 'button'
      btn.className = 'code-copy-btn'
      btn.textContent = '复制'
      btn.addEventListener('click', (ev) => {
        ev.stopPropagation()
        const text = pre.querySelector('code')?.innerText || pre.innerText
        copyText(text).then((ok) => {
          btn.textContent = ok ? '已复制' : '复制失败'
          btn.classList.add(ok ? 'copied' : 'copy-fail')
          setTimeout(() => {
            btn.textContent = '复制'
            btn.classList.remove('copied', 'copy-fail')
          }, 1200)
        })
      })
      pre.appendChild(btn)
    }
  })
}

/** 复制文本（clipboard API + 降级 textarea） */
export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    try {
      const ta = document.createElement('textarea')
      ta.value = text
      ta.style.cssText = 'position:fixed;opacity:0;pointer-events:none'
      document.body.appendChild(ta)
      ta.select()
      const ok = document.execCommand('copy')
      ta.remove()
      return ok
    } catch {
      return false
    }
  }
}
